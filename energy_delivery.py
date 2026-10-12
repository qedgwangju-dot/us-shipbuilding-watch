"""Prospective outbox: public-source queue + durable pre-send ambiguity holds."""
import copy
import datetime as dt
import hashlib
import json
import os
from pathlib import Path

import energy_state_checkpoint as durable


class DeliveryError(RuntimeError):
    pass


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    with tmp.open('w', encoding='utf-8') as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)


def read_json(path):
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8'))
    except Exception:
        raise DeliveryError('State missing or corrupt; refusing to reset or replay') from None
    if not isinstance(value, dict):
        raise DeliveryError('Invalid state object')
    return value


class Outbox:
    def __init__(self, root='.', now=None):
        self.root = Path(root).resolve()
        self.now = now or (lambda: dt.datetime.now(dt.timezone.utc))
        self.refinery = read_json(self.root / durable.STATE_NAMES[0])
        self.spr = read_json(self.root / durable.STATE_NAMES[1])
        for value, seen_key in ((self.refinery, 'seen'), (self.spr, 'seen_news')):
            if value.get('initialized') is not True or not isinstance(value.get(seen_key), list):
                raise DeliveryError('Existing initialized history is required; no automatic baseline')
            if not isinstance(value.get('events', {}), dict):
                raise DeliveryError('Invalid event history')
            value.setdefault('events', {})
        self.path = self.root / durable.STATE_NAMES[2]
        self.state = read_json(self.path) if self.path.exists() else {
            'schema_version': 1, 'outbox': {}, 'receipts': [], 'dispositions': [], 'observed_data': {}}
        if (self.state.get('schema_version') != 1 or not isinstance(self.state.get('outbox'), dict)
                or not isinstance(self.state.get('receipts'), list)
                or not isinstance(self.state.get('dispositions'), list)):
            raise DeliveryError('Invalid outbox schema')
        self.state.setdefault('observed_data', {})
        self.validate_entries()
        self.marker = self.root / 'out/energy_delivery_checkpoint.json'
        self.blocked = False
        self.errors = []
        self.source_failures = []
        self.sent = {'refinery': [], 'spr': []}
        self.attempts = 0

    def validate_entries(self):
        allowed = {'enrichment_pending', 'ready', 'delivery_unknown', 'source_blocked', 'rejected'}
        for key, entry in self.state['outbox'].items():
            if (not isinstance(entry, dict) or entry.get('topic') not in ('refinery', 'spr')
                    or entry.get('kind') not in ('news', 'data') or entry.get('status') not in allowed):
                raise DeliveryError('Invalid stored outbox entry')
            if entry['kind'] == 'news' and (not isinstance(entry.get('items'), list)
                    or any(not isinstance(x, dict) or not isinstance(x.get('id'), str) for x in entry['items'])):
                raise DeliveryError('Invalid stored news candidates')
            chunks = entry.get('chunks')
            if chunks is None:
                if entry['status'] in ('delivery_unknown', 'rejected'):
                    raise DeliveryError('Missing stored delivery intent')
                continue
            if not isinstance(chunks, list) or not chunks:
                raise DeliveryError('Invalid stored chunks')
            destination = entry.get('destination_sha256', '')
            if not isinstance(destination, str) or len(destination) != 64 or any(c not in '0123456789abcdef' for c in destination):
                raise DeliveryError('Invalid stored destination binding')
            for chunk in chunks:
                if (not isinstance(chunk, dict) or not isinstance(chunk.get('text'), str)
                        or not chunk['text'] or chunk.get('message_sha256') != digest(chunk['text'])
                        or chunk.get('status') not in ('ready', 'confirmed', 'delivery_unknown', 'rejected')):
                    raise DeliveryError('Invalid stored chunk binding')
                if chunk['status'] == 'confirmed' and (type(chunk.get('message_id')) is not int or chunk['message_id'] < 1):
                    raise DeliveryError('Invalid stored Telegram receipt')

    def timestamp(self):
        return self.now().isoformat(timespec='seconds')

    def record_error(self, stage, key, exc=None, source_details=None):
        # Exception text can contain token-bearing request URLs. Keep identifiers/stages only.
        text = f'{stage}:{key}' + (f':{type(exc).__name__}' if exc else '')
        self.errors.append(text)
        print('energy_delivery_error=' + text)
        if source_details is not None:
            detail = {'item_id': key, **copy.deepcopy(source_details)}
            self.source_failures.append(detail)
            print('energy_source_failure=' + json.dumps(detail, sort_keys=True))
        return text

    def save_local(self):
        for name, value in zip(durable.STATE_NAMES, (self.refinery, self.spr, self.state)):
            atomic_json(self.root / name, value)
        atomic_json(self.marker, {'schema_version': 1, 'run_id': os.getenv('GITHUB_RUN_ID'),
            'persistence_blocked': self.blocked,
            'state_sha256': {name: hashlib.sha256((self.root / name).read_bytes()).hexdigest()
                             for name in durable.STATE_NAMES}})
        atomic_json(self.root / 'out/energy_delivery_diagnostics.json', {
            'errors': self.errors, 'source_failures': self.source_failures,
            'sent': self.sent, 'attempts': self.attempts,
            'pending': {key: entry.get('status') for key, entry in self.state['outbox'].items()},
            'persistence_blocked': self.blocked, 'run_id': os.getenv('GITHUB_RUN_ID')})

    def checkpoint(self, intent=None):
        if self.blocked:
            raise DeliveryError('Persistence blocked; preserve remote state and inspect artifacts')
        self.save_local()
        try:
            commit = durable.persist(self.root, self.marker, intent=intent)
            if not isinstance(commit, str) or len(commit) != 40 or any(c not in '0123456789abcdef' for c in commit):
                raise DeliveryError('Invalid durable checkpoint acknowledgement')
            return commit
        except Exception:
            self.blocked = True
            self.record_error('checkpoint_blocked', 'bundle')
            self.save_local()
            raise DeliveryError('Durable checkpoint failed; no further sends allowed') from None

    def legacy(self, topic):
        return self.refinery if topic == 'refinery' else self.spr

    def seen_key(self, topic):
        return 'seen' if topic == 'refinery' else 'seen_news'

    def mark_seen(self, topic, ids):
        state = self.legacy(topic)
        field = self.seen_key(topic)
        # Keep every historical key; the migration never prunes confirmed or legacy history.
        state[field] = list(dict.fromkeys(list(ids) + state[field]))

    def suppress(self, key, reason):
        entry = self.state['outbox'].pop(key)
        self.mark_seen(entry['topic'], [x['id'] for x in entry.get('items', [])])
        self.state['dispositions'].append({'key': key, 'reason': reason, 'at': self.timestamp(),
                                          'ids': [x['id'] for x in entry.get('items', [])]})
        retained = entry.get('invalid_items', []) + entry.get('held_items', [])
        if retained:
            self.queue_news(entry['topic'], entry['event_key'], retained)

    def queue_news(self, topic, event_key, items):
        key = f'{topic}:news:{event_key}'
        existing = self.state['outbox'].get(key)
        if existing:
            # Never mutate a payload that might already have been accepted.
            if existing['status'] == 'source_blocked' and any(x['id'] not in {i['id'] for i in existing['items']} for x in items):
                existing['status'] = 'enrichment_pending'
            if existing['status'] in ('delivery_unknown', 'ready', 'source_blocked', 'rejected'):
                held = {x['id']: x for x in existing.get('held_items', [])}
                original = {x['id'] for x in existing['items']}
                held.update({x['id']: copy.deepcopy(x) for x in items if x['id'] not in original})
                existing['held_items'] = list(held.values())
                return key
            merged = {item['id']: item for item in existing['items'] + existing.pop('invalid_items', [])}
            for item in items:
                previous = merged.get(item['id'], {})
                updated = copy.deepcopy(item)
                if previous.get('_source_blocked'):
                    updated['_source_blocked'] = True
                    if previous.get('_source_failure') is not None:
                        updated['_source_failure'] = copy.deepcopy(previous['_source_failure'])
                merged[item['id']] = updated
            existing['items'] = list(merged.values())
        else:
            self.state['outbox'][key] = {'topic': topic, 'kind': 'news', 'event_key': event_key,
                'status': 'enrichment_pending', 'items': copy.deepcopy(items),
                'queued_at': self.timestamp(), 'attempts': 0}
        return key

    def queue_data(self, field, current, text):
        # Observation freshness and delivered checkpoint remain separate.
        observed = self.state['observed_data'].get(field) or {}
        if not observed.get('date') or current['date'] >= observed['date']:
            self.state['observed_data'][field] = copy.deepcopy(current)
        old = self.spr.get(field) or {}
        if not old.get('date'):
            self.spr[field] = copy.deepcopy(current)  # Existing first-observation baseline rule.
            return
        if current['date'] <= old['date']:
            return
        key = f'spr:data:{field}:{current["date"]}'
        if key not in self.state['outbox']:
            self.state['outbox'][key] = {'topic': 'spr', 'kind': 'data', 'field': field,
                'current': copy.deepcopy(current), 'status': 'ready', 'message': text,
                'queued_at': self.timestamp(), 'attempts': 0}

    def complete(self, key):
        entry = self.state['outbox'].pop(key)
        self.state['receipts'].append({'key': key, 'at': self.timestamp(),
            'destination_sha256': entry['destination_sha256'],
            'chunks': [{k: c[k] for k in ('message_id', 'message_sha256')} for c in entry['chunks']]})
        if entry['kind'] == 'news':
            self.mark_seen(entry['topic'], entry['accepted_ids'])
            self.legacy(entry['topic'])['events'][entry['event_key']] = entry['event']
            remaining = entry.get('retry_items', []) + entry.get('invalid_items', []) + entry.get('held_items', [])
            if remaining:
                self.queue_news(entry['topic'], entry['event_key'], remaining)
        else:
            old = self.spr.get(entry['field']) or {}
            if entry['current']['date'] > old.get('date', ''):
                self.spr[entry['field']] = entry['current']

    def deliver(self, key, token, chat_id, rw):
        entry = self.state['outbox'][key]
        if entry['status'] != 'ready':
            return
        if entry['kind'] == 'data':
            old = self.spr.get(entry['field']) or {}
            if entry['current']['date'] <= old.get('date', ''):
                self.suppress(key, 'official_observation_superseded')
                return
        cooldown = self.state.get('retry_after')
        if cooldown and self.now() < dt.datetime.fromisoformat(cooldown):
            return
        destination = digest(rw.EXPECTED_BOT.lower() + '\n' + str(chat_id))
        if entry.get('destination_sha256') not in (None, destination):
            self.record_error('destination_changed', key)
            return
        entry['destination_sha256'] = destination
        entry.setdefault('chunks', [{'text': text, 'message_sha256': digest(text), 'status': 'ready'}
                                   for text in rw.split_message(entry['message'])])
        if not entry['chunks']:
            self.record_error('empty_message', key)
            return
        for index, chunk in enumerate(entry['chunks']):
            if chunk['status'] == 'confirmed':
                continue
            if chunk['status'] != 'ready' or self.attempts >= 6:
                return
            self.attempts += 1
            entry['attempts'] += 1
            entry['status'] = chunk['status'] = 'delivery_unknown'
            chunk['prepared_at'] = self.timestamp()
            self.checkpoint((key, index, chunk['message_sha256'], destination))
            try:
                response = rw.requests.post(f'https://api.telegram.org/bot{token}/sendMessage',
                    data={'chat_id': chat_id, 'text': chunk['text'], 'parse_mode': 'HTML',
                          'disable_web_page_preview': 'true'}, timeout=25)
                payload = response.json()
                if not isinstance(payload, dict):
                    raise DeliveryError('Malformed Telegram response')
                error_code = payload.get('error_code')
                if (payload.get('ok') is False and type(error_code) is int
                        and 400 <= error_code < 500 and response.status_code < 500):
                    if payload['error_code'] == 429:
                        seconds = (payload.get('parameters') or {}).get('retry_after')
                        if not isinstance(seconds, int) or isinstance(seconds, bool) or seconds < 1:
                            seconds = 60
                        self.state['retry_after'] = (self.now() + dt.timedelta(seconds=seconds)).isoformat()
                        entry['status'] = chunk['status'] = 'ready'
                        self.record_error('rate_limited', key)
                    else:
                        entry['status'] = chunk['status'] = 'rejected'
                        self.record_error('telegram_rejected', key)
                    self.checkpoint()
                    return
                result = payload.get('result') or {}
                ident = result.get('message_id') if isinstance(result, dict) else None
                receipt_chat = (result.get('chat') or {}).get('id') if isinstance(result, dict) else None
                if (response.status_code != 200 or payload.get('ok') is not True
                        or not isinstance(ident, int) or isinstance(ident, bool) or ident < 1
                        or str(receipt_chat) != str(chat_id)):
                    raise DeliveryError('Telegram receipt is not strictly confirmed')
                chunk.update(status='confirmed', message_id=ident, confirmed_at=self.timestamp())
                self.sent[entry['topic']].append(ident)
                entry['status'] = 'ready'
                self.checkpoint()  # Persist each chunk receipt before attempting its successor.
            except DeliveryError:
                if self.blocked:
                    raise
                self.record_error('delivery_unknown', key)
                self.checkpoint()
                return
            except Exception as exc:
                self.record_error('delivery_unknown', key, exc)
                self.checkpoint()
                return
        self.complete(key)
        self.checkpoint()
