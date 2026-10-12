"""Offline state/delivery regressions. No live source or Telegram requests allowed."""
import contextlib
import copy
import datetime as dt
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

import requests
import yaml
import semiconductor_consumption_watch_v2 as w
import semiconductor_consumption_watch_v6 as v6
import tesla_state_checkpoint as durable

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / '.github/workflows/semiconductor-consumption-watch.yml'


class DeliveryStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = dt.datetime(2026, 10, 12, 16, 0, tzinfo=w.KST)
        self.patch(w, 'STATE_PATH', self.root / 'state.json')
        self.patch(w, 'CHECKPOINT_PATH', self.root / 'checkpoint.json')
        self.patch(w, 'DIAGNOSTICS_PATH', self.root / 'diagnostics.json')
        self.patch(w, 'now_kst', side_effect=lambda: self.now)
        self.patch(w, 'validate_bot')
        self.patch(w, 'resolve_chat_id', return_value='fixture-chat')
        self.patch(requests.sessions.Session, 'request', side_effect=AssertionError('network forbidden'))
        p = mock.patch.dict(os.environ, {'KHS887900_BOT_TOKEN': 'fixture-token', 'KHS887900_CHAT_ID': 'fixture-chat', 'GITHUB_RUN_ID': 'fixture-run'})
        p.start(); self.addCleanup(p.stop)
        self.items = []
        self.collect = self.patch(w, 'collect_items', side_effect=lambda: (self.items, []))
        self.publisher = self.patch(w, 'publisher_url', side_effect=lambda item: 'https://publisher.example/' + item['key'])
        self.article = self.patch(w, 'article_sentences', return_value=['반도체 수혜지역의 소비와 테슬라 신규등록이 늘었다.'])
        self.format = self.patch(w, 'build_alert', side_effect=v6.build_alert)
        self.durable = self.patch(w, 'durable_checkpoint', return_value='a' * 40)
        self.send = self.patch(w, 'send_telegram', return_value={'message_id': 101, 'bot_username': w.EXPECTED_BOT, 'confirmed_at_kst': self.now.isoformat()})
        self.initial = {'schema_version': 2, 'initialized': True, 'seen_keys': [], 'sent_urls': [], 'recent_titles': []}
        w.STATE_PATH.write_text(json.dumps(self.initial))

    def patch(self, target, name, *args, **kwargs):
        p = mock.patch.object(target, name, *args, **kwargs)
        value = p.start(); self.addCleanup(p.stop); return value

    def item(self, n=1, **overrides):
        result = {'key': f'key-{n}', 'kind': 'news', 'title': f'반도체 수혜지역 소비 테슬라 {n}% 증가',
                  'source': 'fixture-news', 'link': f'https://news.google.com/articles/{n}',
                  'pub_kst': self.now.isoformat()}
        result.update(overrides)
        return result

    def run_main(self, failed=False):
        with contextlib.redirect_stdout(io.StringIO()):
            if failed:
                with self.assertRaisesRegex(RuntimeError, 'incomplete'):
                    w.main()
            else:
                w.main()
        return json.loads(w.STATE_PATH.read_text())

    def test_only_verified_delivery_enters_seen_and_receipts(self):
        self.items = [self.item()]
        state = self.run_main()
        self.assertEqual(state['seen_keys'], ['key-1'])
        self.assertEqual(state['delivery_receipts']['key-1']['message_id'], 101)
        self.assertEqual(state['pending_items'], {})
        self.assertEqual(state['last_sent_count'], 1)
        self.assertEqual(state['last_outcome'], 'complete')
        self.assertIn('핵심 내용', self.send.call_args.args[2])
        self.assertIn('반도체', self.send.call_args.args[2])

    def test_cap_tail_survives_feed_rotation_and_is_sent_next_time(self):
        self.patch(w, 'MAX_ALERTS_PER_RUN', 1)
        self.items = [self.item(1), self.item(2), self.item(3)]
        state = self.run_main()
        self.assertEqual(state['seen_keys'], ['key-1'])
        self.assertEqual(set(state['pending_items']), {'key-2', 'key-3'})
        self.assertTrue(all(x['status'] == 'pending_cap' for x in state['pending_items'].values()))
        self.items = []
        state = self.run_main()
        self.assertEqual(state['seen_keys'], ['key-1', 'key-2'])
        self.assertEqual(set(state['pending_items']), {'key-3'})
        self.assertEqual(self.send.call_count, 2)

    def test_pre_send_extraction_failure_retries_without_seen_even_after_rotation(self):
        self.items = [self.item()]
        self.article.side_effect = requests.ReadTimeout('sensitive-response-detail')
        state = self.run_main(failed=True)
        self.assertEqual(state['seen_keys'], [])
        self.assertEqual(state['pending_items']['key-1']['status'], 'pending_pre_send')
        self.assertIn('article_extraction:ReadTimeout', state['last_errors'][0])
        self.assertNotIn('sensitive-response-detail', w.STATE_PATH.read_text())
        self.send.assert_not_called()
        self.items = []
        self.article.side_effect = None
        state = self.run_main()
        self.assertEqual(state['seen_keys'], ['key-1'])
        self.assertEqual(state['last_errors'], [])
        self.send.assert_called_once()

    def test_unresolved_publisher_is_visible_and_retryable(self):
        self.items = [self.item()]
        self.publisher.side_effect = None; self.publisher.return_value = ''
        state = self.run_main(failed=True)
        self.assertEqual(state['pending_items']['key-1']['stage'], 'publisher_resolution')
        self.assertEqual(state['last_error_count'], 1)
        self.assertEqual(state['seen_keys'], [])
        self.send.assert_not_called()

    def test_formatter_failure_is_pre_send_pending(self):
        self.items = [self.item()]
        self.format.side_effect = ValueError('bad formatting')
        state = self.run_main(failed=True)
        self.assertEqual(state['pending_items']['key-1']['stage'], 'alert_formatting')
        self.send.assert_not_called()

    def test_unknown_send_is_held_and_never_blindly_retried(self):
        self.items = [self.item()]
        self.send.side_effect = requests.ReadTimeout('fixture-token must not be logged')
        state = self.run_main(failed=True)
        self.assertEqual(state['seen_keys'], [])
        self.assertEqual(state['pending_items']['key-1']['status'], 'delivery_unknown')
        self.assertEqual(state['delivery_receipts'], {})
        self.assertNotIn('fixture-token', w.STATE_PATH.read_text())
        self.items = []
        self.run_main(failed=True)
        self.assertEqual(self.send.call_count, 1)

    def test_unknown_url_alias_is_held_not_confirmed_or_resent(self):
        self.items = [self.item(1)]
        self.publisher.side_effect = None; self.publisher.return_value = 'https://publisher.example/same'
        self.send.side_effect = w.TelegramDeliveryUnknown('unverified')
        self.run_main(failed=True)
        self.items = [self.item(2)]
        state = self.run_main(failed=True)
        self.assertEqual(state['pending_items']['key-2']['status'], 'delivery_unknown_duplicate')
        self.assertEqual(state['pending_items']['key-2']['stage'], 'uncertain_url_match')
        self.assertEqual(state['seen_keys'], [])
        self.assertEqual(self.send.call_count, 1)

    def test_unknown_fuzzy_title_alias_is_held_without_resolving_again(self):
        original = self.item(1)
        self.items = [original]
        self.send.side_effect = w.TelegramDeliveryUnknown('unverified')
        self.run_main(failed=True)
        alias = self.item(1, key='alias', source='other-fixture')
        self.items = [alias]
        state = self.run_main(failed=True)
        self.assertEqual(state['pending_items']['alias']['stage'], 'uncertain_title_match')
        self.assertEqual(self.publisher.call_count, 1)
        self.assertEqual(self.send.call_count, 1)

    def test_verified_permanent_rejection_is_visible_hold_not_seen(self):
        self.items = [self.item()]
        self.send.side_effect = w.TelegramRejected(403)
        state = self.run_main(failed=True)
        self.assertEqual(state['pending_items']['key-1']['status'], 'delivery_rejected')
        self.assertEqual(state['pending_items']['key-1']['http_status'], 403)
        self.assertEqual(state['seen_keys'], [])
        self.run_main(failed=True)
        self.assertEqual(self.send.call_count, 1)

    def test_rate_limit_wait_respects_deadline_then_retries(self):
        self.items = [self.item()]
        self.send.side_effect = w.TelegramRejected(429, 120)
        state = self.run_main(failed=True)
        self.assertEqual(state['pending_items']['key-1']['status'], 'pending_retry_after')
        self.now += dt.timedelta(seconds=119)
        self.run_main(failed=True)
        self.assertEqual(self.send.call_count, 1)
        self.now += dt.timedelta(seconds=2)
        self.send.side_effect = None
        state = self.run_main()
        self.assertEqual(state['seen_keys'], ['key-1'])
        self.assertEqual(self.send.call_count, 2)

    def test_confirmed_first_item_survives_later_failure_and_is_not_resent(self):
        self.items = [self.item(1), self.item(2)]
        self.send.side_effect = [{'message_id': 11}, w.TelegramDeliveryUnknown('timeout')]
        state = self.run_main(failed=True)
        self.assertEqual(state['seen_keys'], ['key-1'])
        self.assertEqual(state['delivery_receipts']['key-1']['message_id'], 11)
        self.assertEqual(state['pending_items']['key-2']['status'], 'delivery_unknown')
        self.run_main(failed=True)
        self.assertEqual(self.send.call_count, 2)

    def test_stale_and_fuzzy_suppression_remain_explicit_terminal_dispositions(self):
        stale = self.item(1, pub_kst=(self.now - dt.timedelta(hours=73)).isoformat())
        duplicate = self.item(2)
        state = copy.deepcopy(self.initial)
        state['recent_titles'] = [{'title_norm': w.normalize_title(duplicate['title'], duplicate['source']), 'numbers': list(w.extract_numeric_tokens(duplicate['title']))}]
        w.STATE_PATH.write_text(json.dumps(state))
        self.items = [stale, duplicate]
        state = self.run_main()
        self.assertEqual(state['last_item_outcomes']['key-1']['status'], 'suppressed_stale')
        self.assertEqual(state['last_item_outcomes']['key-2']['status'], 'suppressed_fuzzy_duplicate')
        self.assertEqual(state['last_duplicate_suppressed'], 1)
        self.send.assert_not_called()

    def test_confirmed_url_duplicate_never_sends(self):
        state = copy.deepcopy(self.initial); state['sent_urls'] = ['https://publisher.example/key-1']
        w.STATE_PATH.write_text(json.dumps(state)); self.items = [self.item()]
        state = self.run_main()
        self.assertEqual(state['last_item_outcomes']['key-1']['status'], 'suppressed_confirmed_url')
        self.send.assert_not_called()

    def test_history_43_is_not_reset_or_replayed(self):
        state = copy.deepcopy(self.initial); state['seen_keys'] = [f'historical-{n}' for n in range(43)]
        w.STATE_PATH.write_text(json.dumps(state))
        self.items = [self.item(1, key=f'historical-{n}') for n in range(43)]
        after = self.run_main()
        self.assertEqual(after['seen_keys'], state['seen_keys'])
        self.assertEqual(after['last_new_count'], 0)
        self.send.assert_not_called()

    def test_corrupt_existing_state_fails_closed_without_reset(self):
        w.STATE_PATH.write_text('{broken')
        with self.assertRaisesRegex(RuntimeError, 'refusing to reset'):
            w.main()
        self.assertEqual(w.STATE_PATH.read_text(), '{broken')
        self.assertFalse(w.CHECKPOINT_PATH.exists())
        self.send.assert_not_called()

    def test_pre_send_checkpoint_contains_unknown_attempt_and_unprocessed_tail(self):
        self.items = [self.item(1), self.item(2)]
        def inspect(*args):
            state = json.loads(w.STATE_PATH.read_text())
            self.assertEqual(state['pending_items']['key-1']['status'], 'delivery_unknown')
            self.assertIn('key-2', state['pending_items'])
            marker = json.loads(w.CHECKPOINT_PATH.read_text())
            self.assertEqual(marker['state_sha256'], hashlib.sha256(w.STATE_PATH.read_bytes()).hexdigest())
            raise w.TelegramDeliveryUnknown('interrupted')
        self.send.side_effect = inspect
        self.patch(w, 'MAX_ALERTS_PER_RUN', 1)
        # Unknown sends may have been delivered, so they consume the attempt cap.
        state = self.run_main(failed=True)
        self.assertIn('key-1', state['pending_items'])
        self.assertNotIn('key-1', state['seen_keys'])
        self.assertEqual(self.send.call_count, 1)
        self.assertEqual(state['pending_items']['key-2']['status'], 'pending_cap')

    def test_ambiguous_attempts_are_bounded_and_cap_tail_is_retained(self):
        self.patch(w, 'MAX_ALERTS_PER_RUN', 2)
        self.items = [self.item(n) for n in range(1, 8)]
        self.send.side_effect = w.TelegramDeliveryUnknown('timeout')
        state = self.run_main(failed=True)
        self.assertEqual(self.send.call_count, 2)
        self.assertEqual(state['last_send_attempt_count'], 2)
        self.assertEqual(state['last_sent_count'], 0)
        self.assertEqual(len(state['pending_items']), 7)
        self.assertEqual(sum(x['status'] == 'pending_cap' for x in state['pending_items'].values()), 5)

    def test_429_stops_other_items_and_fresh_keys_until_persisted_bot_deadline(self):
        self.items = [self.item(1), self.item(2)]
        self.send.side_effect = w.TelegramRejected(429, 120)
        state = self.run_main(failed=True)
        self.assertEqual(self.send.call_count, 1)
        self.assertEqual(state['pending_items']['key-2']['stage'], 'bot_rate_limit_wait')
        self.items = [self.item(3)]
        self.now += dt.timedelta(seconds=30)
        state = self.run_main(failed=True)
        self.assertEqual(self.send.call_count, 1)
        self.assertEqual(state['pending_items']['key-3']['status'], 'pending_retry_after')

    def test_uncertain_fuzzy_alias_checks_beyond_last_300_holds(self):
        original = self.item(1)
        state = copy.deepcopy(self.initial)
        state['pending_items'] = {
            f'key-{n}': {'item': self.item(n), 'status': 'delivery_unknown'}
            for n in range(1, 303)
        }
        w.STATE_PATH.write_text(json.dumps(state))
        self.items = [dict(original, key='alias-oldest', source='different-source')]
        state = self.run_main(failed=True)
        self.assertEqual(state['pending_items']['alias-oldest']['status'], 'delivery_unknown_duplicate')
        self.send.assert_not_called()

    def test_retry_transition_does_not_mislabel_timeout_with_old_429(self):
        self.items = [self.item()]
        self.send.side_effect = w.TelegramRejected(429, 1)
        self.run_main(failed=True)
        self.now += dt.timedelta(seconds=2)
        self.send.side_effect = w.TelegramDeliveryUnknown('timeout')
        state = self.run_main(failed=True)
        self.assertEqual(state['pending_items']['key-1']['status'], 'delivery_unknown')
        self.assertNotIn('http_status', state['pending_items']['key-1'])
        self.assertNotIn('retry_after_kst', state['pending_items']['key-1'])

    def test_missing_sender_receipt_never_marks_item_seen(self):
        self.items = [self.item()]; self.send.return_value = None
        state = self.run_main(failed=True)
        self.assertEqual(state['pending_items']['key-1']['status'], 'delivery_unknown')
        self.assertEqual(state['last_sent_count'], 0)

    def test_durable_failure_or_invalid_ack_stops_all_sends_and_blocks_final_persistence(self):
        self.items = [self.item(1), self.item(2)]
        for failure in (RuntimeError('private transport detail'), None, False, 'bad-commit'):
            w.STATE_PATH.write_text(json.dumps(self.initial))
            self.durable.side_effect = failure if isinstance(failure, Exception) else None
            self.durable.return_value = failure
            state = self.run_main(failed=True)
            self.send.assert_not_called()
            self.assertEqual(state['last_send_attempt_count'], 0)
            self.assertEqual(len(state['pending_items']), 2)
            self.assertIs(json.loads(w.CHECKPOINT_PATH.read_text())['persistence_blocked'], True)
            self.assertNotIn('private transport detail', w.STATE_PATH.read_text())

    def test_remote_barrier_precedes_send_and_binds_exact_payload_destination(self):
        self.items = [self.item()]
        order = []
        def persist(state_path, marker_path, key, message_digest, destination_digest):
            order.append('barrier')
            intent = json.loads(state_path.read_text())['pending_items'][key]
            self.assertEqual(intent['message_sha256'], message_digest)
            self.assertEqual(destination_digest, hashlib.sha256((w.EXPECTED_BOT+'\0fixture-chat').encode()).hexdigest())
            return 'a' * 40
        def send(*args):
            order.append('send')
            self.assertEqual(order, ['barrier', 'send'])
            intent = json.loads(w.STATE_PATH.read_text())['pending_items']['key-1']
            self.assertEqual(intent['message_sha256'], hashlib.sha256(args[2][:3900].encode()).hexdigest())
            return {'message_id': 7}
        self.durable.side_effect = persist; self.send.side_effect = send
        state = self.run_main()
        self.assertEqual(state['delivery_receipts']['key-1']['checkpoint_commit'], 'a'*40)

    def test_collection_error_is_degraded_even_with_confirmed_item(self):
        self.collect.side_effect = None; self.collect.return_value = ([self.item()], ['source_query_2: Timeout'])
        state = self.run_main(failed=True)
        self.assertEqual(state['last_sent_count'], 1)
        self.assertEqual(state['last_outcome'], 'degraded')
        self.assertTrue(state['last_errors'])


class TelegramAcknowledgementTests(unittest.TestCase):
    def response(self, payload, status=200):
        r = mock.Mock(status_code=status)
        if isinstance(payload, Exception): r.json.side_effect = payload
        else: r.json.return_value = payload
        if status >= 400: r.raise_for_status.side_effect = requests.HTTPError('sensitive URL', response=r)
        return r

    def call(self, payload, status=200):
        with mock.patch.object(requests, 'post', return_value=self.response(payload, status)):
            return w.send_telegram('fixture-token', 'fixture-chat', '한국어 알림')

    def test_exact_true_valid_message_id_and_matching_chat_confirm(self):
        receipt = self.call({'ok': True, 'result': {'message_id': 7, 'chat': {'id': 'fixture-chat'}}})
        self.assertEqual(receipt['message_id'], 7)
        self.assertNotIn('chat', receipt)

    def test_false_success_bad_id_or_wrong_destination_remain_unknown(self):
        cases = [
            {'ok': 'true', 'result': {'message_id': 7, 'chat': {'id': 'fixture-chat'}}},
            {'ok': 1, 'result': {'message_id': 7, 'chat': {'id': 'fixture-chat'}}},
            {'ok': True, 'result': {}},
            {'ok': True, 'result': {'message_id': 7, 'chat': {'id': 'wrong-fixture'}}},
            ValueError('sensitive body'), [],
        ]
        for bad in (0, -1, True, '7'):
            cases.append({'ok': True, 'result': {'message_id': bad, 'chat': {'id': 'fixture-chat'}}})
        for payload in cases:
            with self.subTest(payload=repr(payload)):
                with self.assertRaises(w.TelegramDeliveryUnknown): self.call(payload)

    def test_explicit_4xx_rejection_is_distinct(self):
        with self.assertRaises(w.TelegramRejected) as caught:
            self.call({'ok': False, 'error_code': 429, 'parameters': {'retry_after': 120}}, 429)
        self.assertEqual(caught.exception.code, 429)
        self.assertEqual(caught.exception.retry_after, 120)

    def test_5xx_or_transport_timeout_is_ambiguous_not_verified_rejection(self):
        with self.assertRaises(w.TelegramDeliveryUnknown):
            self.call({'ok': False, 'error_code': 500}, 500)
        with mock.patch.object(requests, 'post', side_effect=requests.ReadTimeout('sensitive body')):
            with self.assertRaises(w.TelegramDeliveryUnknown) as caught:
                w.send_telegram('fixture-token', 'fixture-chat', '한국어 알림')
        self.assertNotIn('sensitive body', str(caught.exception))


class WorkflowSafetyTests(unittest.TestCase):
    def test_proposal_is_read_only_and_production_is_serial_with_failure_persistence(self):
        raw = WORKFLOW.read_text(); cfg = yaml.load(raw, Loader=yaml.BaseLoader)
        self.assertEqual(cfg['on']['push']['branches'], ['main'])
        self.assertIn('pull_request', cfg['on'])
        for event in ('push', 'pull_request'):
            self.assertIn('tesla_state_checkpoint.py', cfg['on'][event]['paths'])
        pr = cfg['jobs']['offline-regressions']
        self.assertEqual(pr['permissions'], {'contents': 'read'})
        self.assertEqual(pr['steps'][0]['with']['persist-credentials'], 'false')
        self.assertNotIn('secrets.', json.dumps(pr))
        self.assertIn("'-pr-{0}'", cfg['concurrency']['group'])
        self.assertEqual(cfg['concurrency']['cancel-in-progress'], 'false')
        monitor = cfg['jobs']['monitor']
        self.assertEqual(monitor['if'], "github.event_name != 'pull_request' && github.ref == 'refs/heads/main'")
        self.assertEqual(monitor['steps'][0]['with']['ref'], 'main')
        persistence = next(s for s in monitor['steps'] if s.get('name') == '감시 상태 저장')
        self.assertIn('always()', persistence['if'])
        self.assertIn('checkpoint', persistence['if'])
        self.assertEqual(persistence['run'], 'python tesla_state_checkpoint.py')
        helper = (ROOT/'tesla_state_checkpoint.py').read_text()
        self.assertIn("commit = persist_final_state(STATE_NAME, 'out/semiconductor_consumption_checkpoint.json')", helper)
        self.assertNotIn('--force', helper)
        self.assertNotIn('reset', helper)
        self.assertIn('Refusing unrelated worktree or staged changes', helper)
        for job in cfg['jobs'].values():
            for step in job['steps']:
                if 'run' in step:
                    subprocess.run(['bash', '-n'], input=step['run'], text=True, check=True, capture_output=True)

    def run_git_fixture(self, remote_change=None, bad_marker=None, stage_extra=False):
        cfg = yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)
        script = next(s['run'] for s in cfg['jobs']['monitor']['steps'] if s.get('name') == '감시 상태 저장')
        script = script.replace('sleep 3', ': # no delay in offline conflict fixture')
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); remote = root/'remote.git'; a = root/'a'; b = root/'b'
            env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
            env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL='/dev/null',
                       GIT_ALLOW_PROTOCOL='file', HOME=str(root), GITHUB_RUN_ID='fixture-run',
                       GITHUB_REPOSITORY=durable.EXPECTED_REPOSITORY, GITHUB_REF='refs/heads/main',
                       GITHUB_EVENT_NAME='schedule', GITHUB_WORKSPACE=str(a), PYTHONPATH=str(ROOT))
            def git(*args, cwd=None):
                return subprocess.run(['git', *args], cwd=cwd, env=env, text=True, capture_output=True, check=True)
            def configure(path):
                git('config', 'user.name', 'Fixture', cwd=path)
                git('config', 'user.email', 'fixture@example.invalid', cwd=path)
                git('config', 'core.hooksPath', '/dev/null', cwd=path)
            git('init', '--bare', '--initial-branch=main', str(remote))
            git('clone', str(remote), str(a)); configure(a)
            (a/'semiconductor_consumption_state.json').write_text('{"schema_version":2,"owner":"baseline"}\n')
            git('add', '.', cwd=a); git('commit', '-m', 'baseline', cwd=a); git('push', 'origin', 'main', cwd=a)
            git('clone', str(remote), str(b)); configure(b)
            if remote_change:
                path = 'semiconductor_consumption_state.json' if remote_change == 'same_state' else 'unrelated.txt'
                (b/path).write_text('{"schema_version":2,"owner":"newer remote receipt"}\n')
                git('add', '.', cwd=b); git('commit', '-m', 'remote update', cwd=b); git('push', 'origin', 'main', cwd=b)
            expected = git('rev-parse', 'main', cwd=b).stdout.strip()
            candidate = a/'semiconductor_consumption_state.json'; candidate.write_text('{"schema_version":2,"owner":"local checkpoint"}\n')
            marker = {'run_id': 'fixture-run', 'schema_version': 2,
                      'state_sha256': hashlib.sha256(candidate.read_bytes()).hexdigest()}
            if bad_marker == 'run': marker['run_id'] = 'wrong-run'
            if bad_marker == 'hash': marker['state_sha256'] = 'wrong-hash'
            (a/'out').mkdir(); (a/'out/semiconductor_consumption_checkpoint.json').write_text(json.dumps(marker))
            if stage_extra:
                (a/'other.txt').write_text('unrelated staged work')
                git('add', 'other.txt', cwd=a)
            command = 'import sys; from tesla_state_checkpoint import persist_final_state; persist_final_state(sys.argv[1], sys.argv[2], expected_remote=sys.argv[3])'
            result = subprocess.run(['python', '-c', command, str(candidate), str(a/'out/semiconductor_consumption_checkpoint.json'), str(remote)], cwd=a, env=env, text=True, capture_output=True)
            blocked = remote_change == 'same_state' or bad_marker or stage_extra
            if blocked:
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(git('rev-parse', 'refs/heads/main', cwd=remote).stdout.strip(), expected)
                expected_state = '{"schema_version":2,"owner":"newer remote receipt"}\n' if remote_change == 'same_state' else '{"schema_version":2,"owner":"baseline"}\n'
                self.assertEqual(git('show', 'main:semiconductor_consumption_state.json', cwd=remote).stdout, expected_state)
            else:
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(git('show', 'main:semiconductor_consumption_state.json', cwd=remote).stdout, '{"schema_version":2,"owner":"local checkpoint"}\n')
                if remote_change == 'unrelated':
                    self.assertEqual(git('show', 'main:unrelated.txt', cwd=remote).stdout, '{"schema_version":2,"owner":"newer remote receipt"}\n')
            self.assertEqual(candidate.read_text(), '{"schema_version":2,"owner":"local checkpoint"}\n')

    def test_same_state_conflict_does_not_overwrite_remote_checkpoint(self):
        self.run_git_fixture(remote_change='same_state')

    def test_unrelated_main_update_rebases_without_losing_checkpoint(self):
        self.run_git_fixture(remote_change='unrelated')

    def test_wrong_run_checkpoint_cannot_be_committed(self):
        self.run_git_fixture(bad_marker='run')

    def test_wrong_hash_checkpoint_cannot_be_committed(self):
        self.run_git_fixture(bad_marker='hash')

    def test_unrelated_staged_file_prevents_state_commit(self):
        self.run_git_fixture(stage_extra=True)

class DurableCheckpointTests(unittest.TestCase):
    """Real Git, isolated local-file remotes; no GitHub or Telegram access."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.remote = self.root/'remote.git'; self.a = self.root/'a'
        env = {k: v for k, v in os.environ.items() if not k.startswith(('GIT_', 'GITHUB_'))}
        env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL='/dev/null', GIT_ALLOW_PROTOCOL='file',
                   HOME=str(self.root), GITHUB_RUN_ID='fixture-run', GITHUB_REPOSITORY=durable.EXPECTED_REPOSITORY,
                   GITHUB_REF='refs/heads/main', GITHUB_EVENT_NAME='schedule', GITHUB_WORKSPACE=str(self.a))
        p = mock.patch.dict(os.environ, env, clear=True); p.start(); self.addCleanup(p.stop)
        self.git('init', '--bare', '--initial-branch=main', str(self.remote))
        self.clone(self.a)
        self.state = self.a/durable.STATE_NAME; self.marker = self.a/'out/checkpoint.json'
        self.baseline = {'schema_version': 2, 'initialized': True, 'seen_keys': ['historical-1'],
                         'sent_urls': [], 'recent_titles': [], 'other_writer_marker': 'original'}
        self.state.write_text(json.dumps(self.baseline, indent=2)+'\n')
        self.git('add', '.', cwd=self.a); self.git('commit', '-m', 'baseline', cwd=self.a)
        self.git('push', 'origin', 'main', cwd=self.a)
        self.initial_commit = self.git('rev-parse', 'main', cwd=self.remote).stdout.strip()
        self.now = dt.datetime(2026, 10, 12, 16, 0, tzinfo=w.KST)
        self.item = {'key': 'key-1', 'kind': 'news', 'title': '테슬라 반도체 수혜지역 소비 11% 증가',
                     'source': 'fixture-news', 'link': 'https://news.google.com/articles/1', 'pub_kst': self.now.isoformat()}
        self.message_digest = hashlib.sha256('한국어 알림'.encode()).hexdigest()
        self.destination_digest = hashlib.sha256(b'fixture destination').hexdigest()
        state = copy.deepcopy(self.baseline)
        state['pending_items'] = {'key-1': {'item': self.item, 'status': 'delivery_unknown', 'stage': 'send_prepared',
            'canonical_url': 'https://publisher.example/1', 'message_sha256': self.message_digest,
            'destination_sha256': self.destination_digest}}
        self.state.write_text(json.dumps(state, indent=2)+'\n')
        self.write_marker()

    def git(self, *args, cwd=None):
        return subprocess.run(['git', *args], cwd=cwd, text=True, capture_output=True, check=True)

    def clone(self, path):
        self.git('clone', str(self.remote), str(path))
        self.git('config', 'user.name', 'Fixture', cwd=path)
        self.git('config', 'user.email', 'fixture@example.invalid', cwd=path)
        self.git('config', 'core.hooksPath', '/dev/null', cwd=path)

    def write_marker(self, **overrides):
        marker = {'run_id': 'fixture-run', 'schema_version': 2,
                  'state_sha256': hashlib.sha256(self.state.read_bytes()).hexdigest(), **overrides}
        self.marker.parent.mkdir(exist_ok=True); self.marker.write_text(json.dumps(marker))

    def persist(self):
        return durable.persist_intent(self.state, self.marker, 'key-1', self.message_digest,
                                      self.destination_digest, expected_remote=str(self.remote))

    def remote_state(self):
        return json.loads(self.git('show', 'main:'+durable.STATE_NAME, cwd=self.remote).stdout)

    def remote_update(self, state_change):
        b = self.root/'b'; self.clone(b)
        if state_change:
            changed = copy.deepcopy(self.baseline); changed['other_writer_marker'] = 'must-preserve'
            (b/durable.STATE_NAME).write_text(json.dumps(changed, indent=2)+'\n')
        else:
            (b/'unrelated.txt').write_text('must-preserve')
        self.git('add', '.', cwd=b); self.git('commit', '-m', 'other writer', cwd=b)
        self.git('push', 'origin', 'main', cwd=b)
        return self.git('rev-parse', 'main', cwd=self.remote).stdout.strip()

    def test_verified_remote_bytes_contain_exact_intent_before_authorization(self):
        commit = self.persist()
        self.assertEqual(commit, self.git('rev-parse', 'main', cwd=self.remote).stdout.strip())
        intent = self.remote_state()['pending_items']['key-1']
        self.assertEqual(intent['message_sha256'], self.message_digest)
        self.assertEqual(intent['destination_sha256'], self.destination_digest)
        self.assertEqual(intent['status'], 'delivery_unknown')

    def test_unrelated_remote_commit_is_preserved(self):
        self.remote_update(False); self.persist()
        self.assertEqual(self.git('show', 'main:unrelated.txt', cwd=self.remote).stdout, 'must-preserve')
        self.assertIn('key-1', self.remote_state()['pending_items'])

    def test_nonconflicting_remote_state_change_fails_before_rebase(self):
        expected = self.remote_update(True)
        with self.assertRaisesRegex(durable.DurableCheckpointError, 'Remote state changed'): self.persist()
        self.assertEqual(self.git('rev-parse', 'main', cwd=self.remote).stdout.strip(), expected)
        self.assertEqual(self.remote_state()['other_writer_marker'], 'must-preserve')
        self.assertEqual(json.loads(self.state.read_text())['other_writer_marker'], 'original')

    def test_ambiguous_push_is_resolved_only_by_fresh_matching_readback(self):
        original = durable._git
        def lost_ack(root, *args, **kwargs):
            result = original(root, *args, **kwargs)
            if args[0] == 'push': return subprocess.CompletedProcess(result.args, 1, b'', b'ack lost')
            return result
        with mock.patch.object(durable, '_git', side_effect=lost_ack): commit = self.persist()
        self.assertEqual(commit, self.git('rev-parse', 'main', cwd=self.remote).stdout.strip())

    def test_rejected_push_never_authorizes(self):
        original = durable._git; pushes = []
        def reject(root, *args, **kwargs):
            if args[0] == 'push':
                pushes.append(args); return subprocess.CompletedProcess(args, 1, b'', b'rejected')
            return original(root, *args, **kwargs)
        with mock.patch.object(durable, '_git', side_effect=reject):
            with self.assertRaisesRegex(durable.DurableCheckpointError, 'not verified'): self.persist()
        self.assertEqual(len(pushes), 3)
        self.assertEqual(self.git('rev-parse', 'main', cwd=self.remote).stdout.strip(), self.initial_commit)

    def test_published_hold_with_unavailable_readback_never_authorizes(self):
        original = durable._git; pushed = []
        def no_readback(root, *args, **kwargs):
            if args[0] == 'fetch' and pushed: return subprocess.CompletedProcess(args, 1, b'', b'unavailable')
            result = original(root, *args, **kwargs)
            if args[0] == 'push': pushed.append(True)
            return result
        with mock.patch.object(durable, '_git', side_effect=no_readback):
            with self.assertRaisesRegex(durable.DurableCheckpointError, 'readback unavailable'): self.persist()
        self.assertEqual(self.remote_state()['pending_items']['key-1']['status'], 'delivery_unknown')

    def test_wrong_context_push_url_unrelated_staging_and_commits_are_blocked(self):
        for field, value in [('GITHUB_REPOSITORY', 'wrong/repo'), ('GITHUB_REF', 'refs/heads/review'),
                             ('GITHUB_WORKSPACE', str(self.root)), ('GITHUB_EVENT_NAME', 'pull_request')]:
            with self.subTest(field=field), mock.patch.dict(os.environ, {field: value}):
                with self.assertRaises(durable.DurableCheckpointError): self.persist()
        self.git('remote', 'set-url', '--add', '--push', 'origin', str(self.root/'wrong.git'), cwd=self.a)
        with self.assertRaisesRegex(durable.DurableCheckpointError, 'remote'): self.persist()
        self.git('config', '--unset-all', 'remote.origin.pushurl', cwd=self.a)
        (self.a/'unrelated.txt').write_text('do not publish')
        self.git('add', 'unrelated.txt', cwd=self.a)
        with self.assertRaisesRegex(durable.DurableCheckpointError, 'staged'): self.persist()
        self.git('commit', '-m', 'unrelated local commit', cwd=self.a)
        with self.assertRaisesRegex(durable.DurableCheckpointError, 'unpublished'): self.persist()
        self.assertEqual(self.git('rev-parse', 'main', cwd=self.remote).stdout.strip(), self.initial_commit)

    def test_final_saver_rejects_unrelated_history_and_nonconflicting_remote_state(self):
        expected = self.remote_update(True)
        with self.assertRaisesRegex(durable.DurableCheckpointError, 'Remote state changed'):
            durable.persist_final_state(self.state, self.marker, expected_remote=str(self.remote))
        self.assertEqual(self.git('rev-parse', 'main', cwd=self.remote).stdout.strip(), expected)
        self.assertEqual(self.remote_state()['other_writer_marker'], 'must-preserve')
        (self.a/'unrequested.py').write_text('do not publish')
        self.git('add', 'unrequested.py', cwd=self.a)
        self.git('commit', '-m', 'unrelated local code', cwd=self.a)
        with self.assertRaisesRegex(durable.DurableCheckpointError, 'unpublished'):
            durable.persist_final_state(self.state, self.marker, expected_remote=str(self.remote))
        self.assertEqual(self.git('rev-parse', 'main', cwd=self.remote).stdout.strip(), expected)

    def test_git_transport_timeout_is_bounded_and_sanitized(self):
        with mock.patch.object(subprocess, 'run', side_effect=subprocess.TimeoutExpired('private remote URL', 60)) as run:
            with self.assertRaisesRegex(durable.DurableCheckpointError, 'timed out') as caught:
                durable._git(self.a, 'push', 'origin', 'main')
            self.assertEqual(run.call_args.kwargs['timeout'], 60)
            self.assertNotIn('private', str(caught.exception))

    def test_unrelated_commit_then_revert_is_still_not_publishable_history(self):
        (self.a/'unrelated.txt').write_text('must not publish')
        self.git('add', 'unrelated.txt', cwd=self.a)
        self.git('commit', '-m', 'unrelated', cwd=self.a)
        self.git('rm', 'unrelated.txt', cwd=self.a)
        self.git('commit', '-m', 'revert unrelated', cwd=self.a)
        with self.assertRaisesRegex(durable.DurableCheckpointError, 'unpublished'): self.persist()
        self.assertEqual(self.git('rev-parse', 'main', cwd=self.remote).stdout.strip(), self.initial_commit)

    def test_unpublished_merge_commit_is_rejected(self):
        prepared = self.state.read_text()
        self.git('add', durable.STATE_NAME, cwd=self.a)
        self.git('commit', '-m', 'state only', cwd=self.a)
        self.git('checkout', '-b', 'state-branch', cwd=self.a)
        state = json.loads(prepared); state['branch_note'] = 'state only'
        self.state.write_text(json.dumps(state, indent=2)+'\n')
        self.git('add', durable.STATE_NAME, cwd=self.a)
        self.git('commit', '-m', 'branch state', cwd=self.a)
        self.git('checkout', 'main', cwd=self.a)
        self.git('merge', '--no-ff', 'state-branch', '-m', 'state merge', cwd=self.a)
        self.state.write_text(prepared)
        with self.assertRaisesRegex(durable.DurableCheckpointError, 'merge commits'): self.persist()
        self.assertEqual(self.git('rev-parse', 'main', cwd=self.remote).stdout.strip(), self.initial_commit)

    def test_bad_marker_or_wrong_exact_intent_is_blocked(self):
        for overrides in ({'run_id': 'wrong-run'}, {'state_sha256': 'wrong-hash'}, {'persistence_blocked': True}):
            self.write_marker(**overrides)
            with self.assertRaises(durable.DurableCheckpointError): self.persist()
        self.write_marker()
        with self.assertRaisesRegex(durable.DurableCheckpointError, 'Exact prepared'):
            durable.persist_intent(self.state, self.marker, 'key-1', 'wrong-message', self.destination_digest,
                                   expected_remote=str(self.remote))
        with self.assertRaisesRegex(durable.DurableCheckpointError, 'Exact prepared'):
            durable.persist_intent(self.state, self.marker, 'key-1', self.message_digest, 'wrong-destination',
                                   expected_remote=str(self.remote))

    @contextlib.contextmanager
    def main_fixture(self, path):
        with contextlib.ExitStack() as stack:
            for name, value in [('STATE_PATH', path/durable.STATE_NAME), ('CHECKPOINT_PATH', path/'out/semiconductor_consumption_checkpoint.json'),
                                ('DIAGNOSTICS_PATH', path/'out/diagnostics.json')]:
                stack.enter_context(mock.patch.object(w, name, value))
            stack.enter_context(mock.patch.dict(os.environ, {'GITHUB_WORKSPACE': str(path), 'KHS887900_BOT_TOKEN': 'fixture-token', 'KHS887900_CHAT_ID': 'fixture-chat'}))
            stack.enter_context(mock.patch.object(requests.sessions.Session, 'request', side_effect=AssertionError('network forbidden')))
            stack.enter_context(mock.patch.object(w, 'now_kst', return_value=self.now))
            stack.enter_context(mock.patch.object(w, 'validate_bot'))
            stack.enter_context(mock.patch.object(w, 'resolve_chat_id', return_value='fixture-chat'))
            stack.enter_context(mock.patch.object(w, 'collect_items', return_value=([self.item], [])))
            stack.enter_context(mock.patch.object(w, 'publisher_url', return_value='https://publisher.example/1'))
            stack.enter_context(mock.patch.object(w, 'article_sentences', return_value=['반도체 수혜지역 소비와 테슬라 등록이 늘었다.']))
            stack.enter_context(mock.patch.object(w, 'build_alert', side_effect=v6.build_alert))
            stack.enter_context(mock.patch.object(w, 'durable_checkpoint', side_effect=lambda *args: durable.persist_intent(*args, expected_remote=str(self.remote))))
            sender = stack.enter_context(mock.patch.object(w, 'send_telegram'))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            yield sender

    def test_runner_loss_after_send_intent_survives_fresh_clone_and_blocks_retry(self):
        self.state.write_text(json.dumps(self.baseline, indent=2)+'\n')
        class RunnerLost(BaseException): pass
        with self.main_fixture(self.a) as sender:
            def crash(*args):
                self.assertEqual(self.remote_state()['pending_items']['key-1']['status'], 'delivery_unknown')
                raise RunnerLost()
            sender.side_effect = crash
            with self.assertRaises(RunnerLost): w.main()
            sender.assert_called_once()
        c = self.root/'fresh-runner'; self.clone(c)
        with self.main_fixture(c) as sender:
            with self.assertRaisesRegex(RuntimeError, 'incomplete'): w.main()
            sender.assert_not_called()
        state = json.loads((c/durable.STATE_NAME).read_text())
        self.assertEqual(state['pending_items']['key-1']['status'], 'delivery_unknown')
        self.assertEqual(state['seen_keys'], ['historical-1'])

    def test_runner_loss_after_checkpoint_before_send_also_requires_reconciliation(self):
        self.state.write_text(json.dumps(self.baseline, indent=2)+'\n')
        class RunnerLost(BaseException): pass
        with self.main_fixture(self.a) as sender:
            def persisted_then_crashed(*args):
                durable.persist_intent(*args, expected_remote=str(self.remote))
                raise RunnerLost()
            with mock.patch.object(w, 'durable_checkpoint', side_effect=persisted_then_crashed):
                with self.assertRaises(RunnerLost): w.main()
            sender.assert_not_called()
        c = self.root/'fresh-runner'; self.clone(c)
        with self.main_fixture(c) as sender:
            with self.assertRaisesRegex(RuntimeError, 'incomplete'): w.main()
            sender.assert_not_called()
        self.assertEqual(self.remote_state()['pending_items']['key-1']['status'], 'delivery_unknown')

    def test_divergence_blocks_main_send_and_final_workflow_overwrite(self):
        expected = self.remote_update(True)
        self.state.write_text(json.dumps(self.baseline, indent=2)+'\n')
        with self.main_fixture(self.a) as sender:
            with self.assertRaisesRegex(RuntimeError, 'incomplete'): w.main()
            sender.assert_not_called()
        marker = json.loads((self.a/'out/semiconductor_consumption_checkpoint.json').read_text())
        self.assertIs(marker['persistence_blocked'], True)
        with self.assertRaisesRegex(durable.DurableCheckpointError, 'Durable checkpoint failed'):
            durable.persist_final_state(self.state, self.a/'out/semiconductor_consumption_checkpoint.json',
                                        expected_remote=str(self.remote))
        self.assertEqual(self.git('rev-parse', 'main', cwd=self.remote).stdout.strip(), expected)
        self.assertEqual(self.remote_state()['other_writer_marker'], 'must-preserve')


    def test_review_actual_push_race_preserves_other_writer(self):
        original = durable._git
        pushes = []

        def race(root, *args, **kwargs):
            if args[0] == 'push':
                pushes.append(True)
                if len(pushes) == 1:
                    self.remote_update(False)
            return original(root, *args, **kwargs)
        with mock.patch.object(durable, '_git', side_effect=race):
            commit = self.persist()
        self.assertEqual(len(pushes), 2)
        self.assertEqual(commit, self.git('rev-parse', 'main', cwd=self.remote).stdout.strip())
        self.assertEqual(self.git('show', 'main:unrelated.txt', cwd=self.remote).stdout, 'must-preserve')

    def test_review_two_receipt_states_survive_runner_loss_with_tail(self):
        self.state.write_text(json.dumps(self.baseline, indent=2) + '\n')
        items = [dict(self.item, key=f'key-{n}', title=f'테슬라 반도체 수혜지역 소비 {n * 11}% 증가') for n in range(1, 4)]

        class Lost(BaseException):
            pass
        with self.main_fixture(self.a) as sender:
            sender.side_effect = [{'message_id': 41}, Lost()]
            with mock.patch.object(w, 'collect_items', return_value=(items, [])), mock.patch.object(w, 'publisher_url', side_effect=lambda i: 'https://publisher.example/' + i['key']):
                with self.assertRaises(Lost):
                    w.main()
            self.assertEqual(sender.call_count, 2)
        remote = self.remote_state()
        self.assertIn('key-1', remote['seen_keys'])
        self.assertEqual(remote['delivery_receipts']['key-1']['message_id'], 41)
        self.assertEqual(remote['pending_items']['key-2']['status'], 'delivery_unknown')
        self.assertEqual(remote['pending_items']['key-3']['status'], 'pending_pre_send')
        c = self.root / 'new-runner'
        self.clone(c)
        with self.main_fixture(c) as sender:
            sender.return_value = {'message_id': 43}
            with mock.patch.object(w, 'collect_items', return_value=([], [])), mock.patch.object(w, 'publisher_url', side_effect=lambda i: 'https://publisher.example/' + i['key']):
                with self.assertRaisesRegex(RuntimeError, 'incomplete'):
                    w.main()
            sender.assert_called_once()
        resumed = json.loads((c / durable.STATE_NAME).read_text())
        self.assertEqual(resumed['delivery_receipts']['key-1']['message_id'], 41)
        self.assertEqual(resumed['delivery_receipts']['key-3']['message_id'], 43)
        self.assertEqual(resumed['pending_items']['key-2']['status'], 'delivery_unknown')

    def test_review_post_push_concurrent_state_change_blocks_send_and_persistence(self):
        self.state.write_text(json.dumps(self.baseline, indent=2) + '\n')
        original = durable._git
        advanced = []

        def race(root, *args, **kwargs):
            result = original(root, *args, **kwargs)
            if args[0] == 'push' and (not advanced):
                b = self.root / 'post-push-writer'
                self.clone(b)
                s = json.loads((b / durable.STATE_NAME).read_text())
                s['other_writer_marker'] = 'post-push-preserved'
                (b / durable.STATE_NAME).write_text(json.dumps(s, indent=2) + '\n')
                self.git('add', durable.STATE_NAME, cwd=b)
                self.git('commit', '-m', 'concurrent state', cwd=b)
                self.git('push', 'origin', 'main', cwd=b)
                advanced.append(True)
            return result
        with self.main_fixture(self.a) as sender, mock.patch.object(durable, '_git', side_effect=race):
            with self.assertRaisesRegex(RuntimeError, 'incomplete'):
                w.main()
            sender.assert_not_called()
        self.assertEqual(self.remote_state()['other_writer_marker'], 'post-push-preserved')
        marker = json.loads((self.a / 'out/semiconductor_consumption_checkpoint.json').read_text())
        self.assertIs(marker['persistence_blocked'], True)

if __name__ == '__main__':
    unittest.main()
