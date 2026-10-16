"""Offline-only future-delivery regressions; HTTP and production Git are forbidden."""
import copy
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

import readable_energy_alert_runner as runner
import readable_energy_alert_runner_v2 as v2
import energy_delivery as delivery
import energy_state_checkpoint as durable

ROOT = Path(__file__).resolve().parents[1]


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        runner.rw.reset_source_circuits()
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.now = dt.datetime(2026, 10, 12, 17, tzinfo=runner.KST)
        self.initial_ref = {'initialized': True, 'seen': ['historical-confirmed', '426ae25ce530731f6b7cf620'], 'events': {}}
        self.initial_spr = {'initialized': True, 'seen_news': ['historical-spr'], 'events': {},
            'spr': {'date': '2026-10-02', 'value_kbbl': 400000},
            'venezuela_imports': {'date': '2026-10-02', 'value_kbd': 200}}
        for name, data in zip(durable.STATE_NAMES, (self.initial_ref, self.initial_spr)):
            delivery.atomic_json(self.root / name, data)
        self.patch(runner, 'now_kst', side_effect=lambda: self.now)
        self.patch(runner.rw.requests.sessions.Session, 'request', side_effect=AssertionError('live HTTP forbidden'))
        self.persist = self.patch(durable, 'persist', return_value='a' * 40)
        self.collect_ref = self.patch(runner.rw, 'collect_items', return_value=([], []))
        self.collect_spr = self.patch(runner.sv, 'collect_news', return_value=([], []))
        self.fetch_spr = self.patch(runner.sv, 'fetch_spr_latest', return_value=self.data('spr'))
        self.fetch_ven = self.patch(runner.sv, 'fetch_venezuela_imports_latest', return_value=self.data('ven'))
        self.enrich = self.patch(runner.rw, 'enrich_korean', side_effect=self.enriched)
        self.post = self.patch(runner.rw.requests, 'post', return_value=self.response())
        self.engine = delivery.Outbox(self.root, now=lambda: self.now)

    def patch(self, obj, key, **kwargs):
        p = mock.patch.object(obj, key, **kwargs); value = p.start(); self.addCleanup(p.stop); return value

    def item(self, n='a', **kw):
        out = {'id': n, 'title': 'Trump refinery policy change ' + n, 'source': 'Reuters',
               'link': 'https://news.google.com/' + n, 'source_url': 'https://reuters.com',
               'pub_kst': self.now.isoformat()}
        out.update(kw); return out

    def enriched(self, item):
        return dict(item, title_ko='새로운 정유업계 정책 변화', body_ko=['정부는 정유업계 규제를 검토하고 있다고 밝혔습니다.'],
                    original_url='https://publisher.example/article')

    def data(self, kind='spr', date='2026-10-09'):
        return {'date': date, ('value_kbbl' if kind == 'spr' else 'value_kbd'): 400100 if kind == 'spr' else 201,
                ('prev_kbbl' if kind == 'spr' else 'prev_kbd'): 400000 if kind == 'spr' else 200,
                'source': 'https://www.eia.gov/fixture'}

    def response(self, payload=None, status=200):
        return mock.Mock(status_code=status, json=mock.Mock(return_value=payload if payload is not None else {
            'ok': True, 'result': {'message_id': 101, 'chat': {'id': 42}}}))

    def refinery(self, items):
        self.collect_ref.return_value = (items, [])
        return runner.process_refinery('fixture-token', '42', self.engine)

    def restart(self):
        self.engine = delivery.Outbox(self.root, now=lambda: self.now)

    def test_receipt_advances_news_only_after_remote_intent(self):
        def send(*args, **kwargs):
            remote = json.loads((self.root / durable.STATE_NAMES[2]).read_text())
            self.assertEqual(next(iter(remote['outbox'].values()))['status'], 'delivery_unknown')
            self.assertNotIn('a', json.loads((self.root / durable.STATE_NAMES[0]).read_text())['seen'])
            return self.response()
        self.post.side_effect = send
        self.refinery([self.item()])
        self.assertIn('a', self.engine.refinery['seen'])
        self.assertEqual(self.engine.state['outbox'], {})
        self.assertEqual(self.engine.state['receipts'][0]['chunks'][0]['message_id'], 101)
        self.assertTrue(self.persist.call_args_list[1].kwargs['intent'])

    def test_enrichment_failure_retains_full_item_without_false_seen(self):
        self.enrich.side_effect = RuntimeError('기사 본문을 정상적으로 추출하지 못함')
        self.refinery([self.item()]); self.assertNotIn('a', self.engine.refinery['seen'])
        self.assertEqual(next(iter(self.engine.state['outbox'].values()))['items'][0]['link'], self.item()['link'])
        self.assertTrue(self.engine.errors); self.post.assert_not_called()
        self.restart(); self.enrich.side_effect = self.enriched
        self.refinery([]); self.assertIn('a', self.engine.refinery['seen'])

    def test_high_failed_low_confirmed_retains_upgrade_after_feed_rotation(self):
        high = self.item('high', title='Trump refinery capacity', source='EPA', source_url='https://epa.gov')
        low = self.item('low', title='Trump refinery capacity', source='Reuters')
        self.enrich.side_effect = lambda item: (_ for _ in ()).throw(ValueError()) if item['id'] == 'high' else self.enriched(item)
        self.refinery([high, low])
        self.assertIn('low', self.engine.refinery['seen']); self.assertNotIn('high', self.engine.refinery['seen'])
        self.assertEqual(self.engine.refinery['events'][runner.refinery_event_key(low)]['priority'], 4)
        self.restart(); self.enrich.side_effect = self.enriched
        self.refinery([]); self.assertIn('high', self.engine.refinery['seen'])
        self.assertEqual(self.engine.refinery['events'][runner.refinery_event_key(high)]['priority'], 5)

    def test_partial_chunks_hold_ambiguous_successor(self):
        self.patch(runner.rw, 'split_message', return_value=['첫번째', '두번째'])
        self.post.side_effect = [self.response(), TimeoutError('token must not appear')]
        self.refinery([self.item()]); self.assertNotIn('a', self.engine.refinery['seen'])
        entry = next(iter(self.engine.state['outbox'].values()))
        self.assertEqual([x['status'] for x in entry['chunks']], ['confirmed', 'delivery_unknown'])
        self.assertEqual(entry['chunks'][0]['message_id'], 101)
        self.assertNotIn('token must not appear', json.dumps(self.engine.errors))
        self.restart(); self.refinery([]); self.assertEqual(self.post.call_count, 2)

    def test_strict_receipts_reject_false_missing_bool_zero_wrong_chat(self):
        cases = [{'ok': 1, 'result': {'message_id': 1, 'chat': {'id': 42}}},
                 {'ok': True, 'result': {'message_id': True, 'chat': {'id': 42}}},
                 {'ok': True, 'result': {'message_id': 0, 'chat': {'id': 42}}},
                 {'ok': True, 'result': {'message_id': 1, 'chat': {'id': 99}}},
                 {'ok': True, 'result': {}}, []]
        for i, payload in enumerate(cases):
            with self.subTest(payload=payload):
                self.post.return_value = self.response(payload)
                self.refinery([self.item(str(i))])
                self.assertNotIn(str(i), self.engine.refinery['seen'])
                self.assertEqual(self.engine.state['outbox'][f'refinery:news:refinery_article_{i}']['status'], 'delivery_unknown')

    def test_rate_limit_is_shared_across_refinery_spr_and_restart(self):
        self.post.return_value = self.response({'ok': False, 'error_code': 429, 'parameters': {'retry_after': 3600}}, 429)
        self.refinery([self.item()]); runner.process_spr('fixture-token', '42', self.engine)
        self.assertEqual(self.post.call_count, 1); self.assertEqual(self.engine.spr['spr']['date'], '2026-10-02')
        self.restart(); runner.process_spr('fixture-token', '42', self.engine)
        self.assertEqual(self.post.call_count, 1)
        self.now += dt.timedelta(hours=2); self.post.return_value = self.response()
        self.refinery([]); runner.process_spr('fixture-token', '42', self.engine)
        self.assertEqual(self.engine.spr['spr']['date'], '2026-10-09')

    def test_explicit_rejection_is_held(self):
        self.post.return_value = self.response({'ok': False, 'error_code': 400}, 400)
        self.refinery([self.item()]); self.restart(); self.refinery([])
        self.assertEqual(self.post.call_count, 1)
        self.assertEqual(next(iter(self.engine.state['outbox'].values()))['status'], 'rejected')

    def test_checkpoint_failure_never_calls_telegram_or_final_overwrite(self):
        self.persist.side_effect = durable.DurableCheckpointError('conflict')
        with self.assertRaises(delivery.DeliveryError): self.refinery([self.item()])
        self.assertTrue(self.engine.blocked); self.post.assert_not_called()
        with self.assertRaises(delivery.DeliveryError): self.engine.checkpoint()
        self.assertEqual(self.persist.call_count, 1)

    def test_unknown_news_alias_is_retained_and_not_resent(self):
        self.post.side_effect = TimeoutError()
        self.refinery([self.item('first', title='Trump refinery capacity')])
        self.restart(); self.refinery([self.item('alias', title='Trump refinery capacity')])
        self.assertEqual(self.post.call_count, 1)
        self.assertEqual(next(iter(self.engine.state['outbox'].values()))['held_items'][0]['id'], 'alias')

    def test_cap_tail_persists_and_delivers_after_feed_rotation(self):
        self.refinery([self.item(str(i)) for i in range(8)])
        self.assertEqual(self.post.call_count, 6); self.assertEqual(len(self.engine.state['outbox']), 2)
        self.restart(); self.refinery([]); self.assertEqual(self.post.call_count, 8)

    def test_stale_invalid_and_future_news_do_not_send(self):
        self.refinery([self.item('stale', pub_kst=(self.now-dt.timedelta(days=4)).isoformat()),
                       self.item('bad', pub_kst='bad'), self.item('future', pub_kst=(self.now+dt.timedelta(days=1)).isoformat())])
        self.post.assert_not_called(); self.assertIn('stale', self.engine.refinery['seen'])
        self.assertNotIn('bad', self.engine.refinery['seen']); self.assertTrue(self.engine.errors)

    def test_retry_does_not_refresh_expired_news(self):
        self.enrich.side_effect = ValueError(); self.refinery([self.item()])
        self.now += dt.timedelta(days=4); self.restart(); self.enrich.side_effect = self.enriched
        self.refinery([]); self.post.assert_not_called()
        self.assertEqual(self.engine.state['dispositions'][-1]['reason'], 'news_outside_three_day_window')

    def test_access_block_not_refetched(self):
        self.enrich.side_effect = RuntimeError('원문 접속 실패 HTTP 403')
        self.refinery([self.item()]); self.restart(); self.refinery([])
        self.assertEqual(self.enrich.call_count, 1)
        self.assertEqual(next(iter(self.engine.state['outbox'].values()))['status'], 'source_blocked')

    def test_priority_suppression_preserved(self):
        item = self.item(); key = runner.refinery_event_key(item)
        self.engine.refinery['events'][key] = {'priority': 5}
        self.refinery([item]); self.enrich.assert_not_called(); self.post.assert_not_called()
        self.assertEqual(self.engine.state['dispositions'][-1]['reason'], 'existing_event_priority')

    def test_historical_keys_never_replayed(self):
        self.refinery([self.item('historical-confirmed'), self.item('426ae25ce530731f6b7cf620')])
        self.post.assert_not_called(); self.enrich.assert_not_called()
        self.assertEqual(self.engine.refinery['seen'], self.initial_ref['seen'])

    def test_corrupt_and_uninitialized_states_fail_closed(self):
        for raw in ('broken', '{}', '[]'):
            (self.root / durable.STATE_NAMES[0]).write_text(raw)
            with self.assertRaises(delivery.DeliveryError): delivery.Outbox(self.root)
        self.post.assert_not_called()

    def test_official_unknown_does_not_advance_and_new_observation_is_independent(self):
        self.post.side_effect = TimeoutError()
        runner.process_spr('fixture-token', '42', self.engine)
        self.assertEqual(self.engine.spr['spr']['date'], '2026-10-02')
        self.assertEqual(self.engine.state['observed_data']['spr']['date'], '2026-10-09')
        self.restart(); self.post.return_value = self.response(); self.post.side_effect = None
        self.fetch_spr.return_value = self.data('spr', '2026-10-16')
        runner.process_spr('fixture-token', '42', self.engine)
        self.assertEqual(self.engine.spr['spr']['date'], '2026-10-16')
        self.assertTrue(any(x['status'] == 'delivery_unknown' for x in self.engine.state['outbox'].values()))

    def test_older_queued_official_data_cannot_regress_or_send_as_current(self):
        self.engine.queue_data('spr', self.data('spr', '2026-10-09'), '이전 수치')
        self.engine.queue_data('spr', self.data('spr', '2026-10-16'), '새 수치')
        runner._deliver_topic(self.engine, 'spr', 'fixture-token', '42')
        self.assertEqual(self.post.call_count, 1)
        self.assertEqual(self.engine.spr['spr']['date'], '2026-10-16')
        self.assertIn('official_observation_superseded', [x['reason'] for x in self.engine.state['dispositions']])

    def test_opinion_proposal_never_uses_fixed_confirmed_policy_headline(self):
        item = self.item(title='Should the Trump Administration Use the Defense Production Act to Expand Refineries and Pipelines?')
        value = dict(self.enriched(item), title_ko='트럼프 정부가 국방물자생산법을 활용해야 할까?',
            body_ko=['기존 권한과 배정은 발표됐지만 정유시설 지원 확정은 확인되지 않았다는 분석입니다.',
                     '향후 활용 가능성을 검토하자는 제안입니다.'])
        # Minimal paraphrase fixture; source verified by parent, not exact Telegram payload:
        # https://energynewsbeat.co/big-oil-companies/should-the-trump-administration-use-the-defense-production-act-to-expand-refineries-and-pipelines/
        text = v2.concise_news_alert('refinery', value, 'refinery_capacity_policy_202609', [item])
        self.assertIn('[의견·제안]', text); self.assertIn('기존 권한과 배정', text)
        self.assertNotIn('<b>미국 정제능력·허가 정책 변화</b>', text)
        self.assertIn('향후 제안', text)

    def test_mixed_english_quality_check_is_not_weakened(self):
        self.assertFalse(v2._korean_quality_ok('The refinery should expand production today 한국어'))
        with self.assertRaises(RuntimeError):
            v2.concise_news_alert('refinery', dict(self.enriched(self.item()), title_ko='The refinery should expand'),
                                  'refinery_article_a', [self.item()])

    def test_repository_legacy_state_snapshots_are_not_seeded_or_changed(self):
        if (ROOT.parent / 'base').is_dir():
            for name in durable.STATE_NAMES[:2]:
                self.assertEqual((ROOT / name).read_bytes(), (ROOT.parent / 'base' / name).read_bytes())
            self.assertFalse((ROOT / durable.STATE_NAMES[2]).exists())
        self.assertIn('426ae25ce530731f6b7cf620', self.initial_ref['seen'])



class BoundaryTests(unittest.TestCase):
    setUp = DeliveryTests.setUp
    patch = DeliveryTests.patch
    item = DeliveryTests.item
    enriched = DeliveryTests.enriched
    data = DeliveryTests.data
    response = DeliveryTests.response
    refinery = DeliveryTests.refinery
    restart = DeliveryTests.restart
    def test_http500_json429_and_bool_error_code_stay_unknown(self):
        for n, status, payload in [('five', 500, {'ok': False, 'error_code': 429}),
                                   ('bool', 400, {'ok': False, 'error_code': True}),
                                   ('server', 500, {'ok': False, 'error_code': 500})]:
            self.post.return_value = self.response(payload, status)
            self.refinery([self.item(n)])
            self.assertEqual(self.engine.state['outbox'][f'refinery:news:refinery_article_{n}']['status'], 'delivery_unknown')
        self.assertNotIn('retry_after', self.engine.state)

    def test_missing_false_or_malformed_checkpoint_ack_blocks_transmission(self):
        for ack in (None, False, 'x' * 40, 'abc'):
            self.persist.return_value = ack
            with self.assertRaises(delivery.DeliveryError): self.engine.checkpoint()
            self.engine.blocked = False
        self.post.assert_not_called()

    def test_persisted_boolean_chunk_receipt_rejected_on_reload(self):
        key = self.engine.queue_news('refinery', 'a', [self.item()]); runner._prepare_news(self.engine, key)
        entry = self.engine.state['outbox'][key]
        entry.update(destination_sha256='a'*64, chunks=[{'text': entry['message'], 'message_sha256': delivery.digest(entry['message']),
                     'status': 'confirmed', 'message_id': False}])
        self.engine.save_local()
        with self.assertRaises(delivery.DeliveryError): self.restart()
        self.post.assert_not_called()

    def test_http403_exception_blocks_source(self):
        import requests
        self.enrich.side_effect = requests.HTTPError('denied', response=mock.Mock(status_code=403))
        self.refinery([self.item()]); self.restart(); self.refinery([])
        self.assertEqual(self.enrich.call_count, 1)
        self.assertEqual(next(iter(self.engine.state['outbox'].values()))['status'], 'source_blocked')

    def test_denied_original_flag_survives_raw_feed_merge_and_alternative(self):
        high = self.item('high', title='Trump refinery capacity', source='EPA', source_url='https://epa.gov')
        low = self.item('low', title='Trump refinery capacity')
        self.enrich.side_effect = RuntimeError('HTTP 403 access denied'); self.refinery([high]); self.restart()
        self.enrich.reset_mock(); self.enrich.side_effect = self.enriched
        self.refinery([high, low])
        self.assertEqual([c.args[0]['id'] for c in self.enrich.call_args_list], ['low'])
        self.assertNotIn('high', self.engine.refinery['seen']); self.assertIn('low', self.engine.refinery['seen'])

    def test_historical_priority_not_downgraded_after_failed_upgrade(self):
        high = self.item('high', title='Trump refinery capacity', source='EPA', source_url='https://epa.gov')
        low = self.item('low', title='Trump refinery capacity', source='small outlet', source_url='https://example.org')
        event = runner.refinery_event_key(high); self.engine.refinery['events'][event] = {'priority': 4}
        self.enrich.side_effect = lambda x: (_ for _ in ()).throw(ValueError()) if x['id'] == 'high' else self.enriched(x)
        self.refinery([high, low]); self.post.assert_not_called()
        self.assertEqual(self.engine.refinery['events'][event]['priority'], 4)

    def test_ready_alias_survives_original_expiry(self):
        old = self.item('old', title='Trump refinery capacity', pub_kst=(self.now-dt.timedelta(days=2)).isoformat())
        key = self.engine.queue_news('refinery', runner.refinery_event_key(old), [old]); runner._prepare_news(self.engine, key)
        self.now += dt.timedelta(days=2)
        new = self.item('new', title='Trump refinery capacity', source='EPA', source_url='https://epa.gov')
        self.engine.queue_news('refinery', runner.refinery_event_key(new), [new])
        runner._deliver_topic(self.engine, 'refinery', 'fixture', '42'); self.engine.checkpoint(); self.restart()
        self.refinery([]); self.assertIn('new', self.engine.refinery['seen'])

    def test_legacy_error_counters_are_current(self):
        self.engine.refinery.update(last_source_error_count=0, last_translation_error_count=0)
        self.collect_ref.return_value = ([self.item()], [runner.rw.QUERIES[2] + ': HTTPError'])
        self.enrich.side_effect = ValueError()
        runner.process_refinery('fixture', '42', self.engine)
        self.assertEqual(self.engine.refinery['last_source_error_count'], 1)
        self.assertEqual(self.engine.refinery['last_translation_error_count'], 1)
        self.assertIn('refinery_source_failed:query-2', self.engine.errors)

    def test_invalid_date_sibling_does_not_block_valid_official_or_mark_invalid_seen(self):
        invalid = self.item('bad', title='Trump refinery capacity', source='small source', source_url='https://example.org', pub_kst='')
        valid = self.item('good', title='Trump refinery capacity', source='EPA', source_url='https://epa.gov')
        self.refinery([invalid, valid])
        self.assertIn('good', self.engine.refinery['seen']); self.assertNotIn('bad', self.engine.refinery['seen'])
        self.assertEqual(self.post.call_count, 1)
        self.assertEqual(next(iter(self.engine.state['outbox'].values()))['items'][0]['id'], 'bad')
        self.restart(); self.refinery([]); self.assertEqual(self.post.call_count, 1)

    def test_main_is_degraded_when_enrichment_fails(self):
        self.collect_ref.return_value = ([self.item()], [])
        self.enrich.side_effect = RuntimeError('extraction failed')
        self.fetch_spr.return_value = self.data('spr', '2026-10-02')
        self.fetch_ven.return_value = self.data('ven', '2026-10-02')
        with mock.patch.object(runner, 'Outbox', return_value=self.engine), \
             mock.patch.object(runner.rw, 'validate_telegram', return_value=runner.rw.EXPECTED_BOT), \
             mock.patch.dict(os.environ, {'TELEGRAM_BOT_TOKEN': 'fixture', 'TELEGRAM_CHAT_ID': '42'}):
            with self.assertRaisesRegex(RuntimeError, 'Energy delivery incomplete'): runner.main()
        self.assertFalse(self.engine.state['last_result']['ok'])
        self.assertEqual(self.engine.state['last_result']['errors'], 1)
        self.assertEqual(self.engine.state['last_result']['pending'], 1)

    def test_main_reports_healthy_only_when_no_errors_or_pending(self):
        self.fetch_spr.return_value = self.data('spr', '2026-10-02')
        self.fetch_ven.return_value = self.data('ven', '2026-10-02')
        with mock.patch.object(runner, 'Outbox', return_value=self.engine), \
             mock.patch.object(runner.rw, 'validate_telegram', return_value=runner.rw.EXPECTED_BOT), \
             mock.patch.dict(os.environ, {'TELEGRAM_BOT_TOKEN': 'fixture', 'TELEGRAM_CHAT_ID': '42'}):
            runner.main()
        self.assertTrue(self.engine.state['last_result']['ok']); self.post.assert_not_called()

    def test_partial429_retry_sends_only_unconfirmed_chunk(self):
        self.patch(runner.rw, 'split_message', return_value=['첫번째', '두번째'])
        self.post.side_effect = [self.response(), self.response({'ok': False, 'error_code': 429, 'parameters': {'retry_after': 60}}, 429)]
        self.refinery([self.item()]); self.restart(); self.now += dt.timedelta(seconds=61)
        self.post.side_effect = None; self.post.return_value = self.response()
        self.refinery([]); self.assertEqual(self.post.call_count, 3)
        self.assertEqual(len(self.engine.state['receipts'][0]['chunks']), 2)


class SourceBoundaryTests(unittest.TestCase):
    def setUp(self):
        runner.rw.reset_source_circuits()
        block = mock.patch.object(runner.rw.requests.sessions.Session, 'request', side_effect=AssertionError('live HTTP forbidden'))
        block.start(); self.addCleanup(block.stop)

    def response(self, text):
        return mock.Mock(text=text, content=text.encode(), status_code=200, raise_for_status=mock.Mock())

    def test_block_page_and_wrong_xml_shape_are_not_healthy_empty_rss(self):
        for module in (runner.rw, runner.sv):
            for raw in ('<html><body>Access denied</body></html>', '<html/>', '<rss/>'):
                runner.rw.reset_source_circuits()
                with self.subTest(module=module.__name__, raw=raw), mock.patch.object(module.requests, 'get', return_value=self.response(raw)):
                    with self.assertRaises(RuntimeError): module.google_news_rss('fixture')

    def test_harmless_captcha_script_or_metadata_is_not_a_denial(self):
        raw = '<html><head><title>Energy article</title><script src="https://example.org/recaptcha.js"></script><meta content="access denied study"></head><body><article>Energy policy changed today.</article></body></html>'
        self.assertFalse(runner.rw.source_block_page(raw))
        self.assertTrue(runner.rw.source_block_page('<html><title>Just a moment...</title><form id="challenge-form"></form></html>'))
        transport = mock.Mock(get=mock.Mock(return_value=self.response(raw)))
        guarded = runner.rw._SourceTransport(transport)
        self.assertEqual(guarded.get('https://fixture.invalid').status_code, 200)
        self.assertFalse(guarded.denied)

    def test_genuine_empty_rss_is_healthy(self):
        for module in (runner.rw, runner.sv):
            with mock.patch.object(module.requests, 'get', return_value=self.response('<rss><channel/></rss>')):
                self.assertEqual(module.google_news_rss('fixture'), [])

    def test_missing_or_invalid_rss_dates_remain_unknown(self):
        for module in (runner.rw, runner.sv):
            for date in ('', '<pubDate>bad date</pubDate>'):
                raw = '<rss><channel><item><title>Trump refinery Venezuela</title><link>https://example.org/a</link>' + date + '</item></channel></rss>'
                with mock.patch.object(module.requests, 'get', return_value=self.response(raw)):
                    self.assertEqual(module.google_news_rss('fixture')[0]['pub_kst'], '')

    def test_translation_explicit_denial_or_rate_limit_has_no_retry_or_fallback(self):
        import requests
        for status in (401, 403, 429):
            runner.rw.reset_source_circuits()
            exc = requests.HTTPError('denied', response=mock.Mock(status_code=status))
            with mock.patch.object(runner.rw.GoogleTranslator, 'translate', side_effect=exc) as translator, \
                 mock.patch.object(runner.rw, 'google_translate_http') as fallback, \
                 mock.patch.object(runner.rw.time, 'sleep'):
                with self.assertRaises(runner.rw.SourceAccessDenied):
                    v2._original_translate_ko('This text needs Korean translation.')
                self.assertEqual(translator.call_count, 1); fallback.assert_not_called()

    def test_v2_does_not_use_other_translator_after_denial(self):
        import requests
        with mock.patch.object(v2, '_original_translate_ko', side_effect=requests.HTTPError(response=mock.Mock(status_code=403))), \
             mock.patch.object(v2, '_translate_with_mymemory') as fallback:
            with self.assertRaises(runner.rw.SourceAccessDenied): v2.translate_ko_resilient('A general English sentence requiring translation')
            fallback.assert_not_called()

    def test_ordinary_translation_failure_still_allows_existing_fallback(self):
        with mock.patch.object(runner.rw.GoogleTranslator, 'translate', side_effect=RuntimeError('temporary failure')) as translator, \
             mock.patch.object(runner.rw, 'google_translate_http', return_value='임시 장애 뒤 한국어로 번역한 문장입니다.') as fallback, \
             mock.patch.object(runner.rw.time, 'sleep'):
            self.assertIn('한국어', v2._original_translate_ko('A general English sentence requiring translation'))
            self.assertEqual(translator.call_count, 2); fallback.assert_called_once()

    def test_denied_decoder_never_uses_redirect_or_search_fallback(self):
        import requests
        for result in ('structured', 'dictionary'):
            runner.rw.reset_source_circuits()
            kwargs = {'side_effect': requests.HTTPError(response=mock.Mock(status_code=403))} if result == 'structured' else {'return_value': {'status': False, 'message': 'HTTP 403 access denied'}}
            with mock.patch.object(runner.rw, 'gnewsdecoder', **kwargs), mock.patch.object(runner.rw.requests, 'get') as get:
                with self.assertRaises(runner.rw.SourceAccessDenied): runner.rw.decode_google_news_url('https://news.google.com/x')
                get.assert_not_called()

    def test_real_pinned_decoder_never_retries_explicit_denial(self):
        import requests
        for status in (401, 403, 429):
            runner.rw.reset_source_circuits()
            response = requests.Response()
            response.status_code = status
            response.url = 'https://news.google.com/articles/fixture'
            response._content = b'provider denied'
            original_transport = runner.rw.decoder_runtime.requests
            with mock.patch.object(requests.sessions.Session, 'request', return_value=response) as request:
                with self.assertRaises(runner.rw.SourceAccessDenied):
                    runner.rw.decode_google_news_url('https://news.google.com/articles/fixture')
                self.assertEqual(request.call_count, 1)
            self.assertIs(runner.rw.decoder_runtime.requests, original_transport)

    def test_real_pinned_translators_stop_on_denial_before_status_is_lost(self):
        import requests
        for translate in (v2._original_translate_ko, v2._translate_with_mymemory):
            for status in (401, 403, 429):
                runner.rw.reset_source_circuits()
                response = requests.Response(); response.status_code = status
                response.url = 'https://translate.example/fixture'; response._content = b'provider denied'
                with mock.patch.object(requests.sessions.Session, 'request', return_value=response) as request, \
                     mock.patch.object(runner.rw.time, 'sleep'):
                    with self.assertRaises(runner.rw.SourceAccessDenied):
                        translate('This general English sentence requires translation.')
                    self.assertEqual(request.call_count, 1)

    def test_bot_identity_rejects_truthy_nonboolean_ok(self):
        for ok in ('true', 1, False):
            with mock.patch.object(runner.rw, 'tg_api', return_value={'ok': ok, 'result': {'username': runner.rw.EXPECTED_BOT}}):
                with self.assertRaises(RuntimeError): runner.rw.validate_telegram('fixture')
        with mock.patch.object(runner.rw, 'tg_api', return_value={'ok': True, 'result': []}):
            with self.assertRaises(RuntimeError): runner.rw.validate_telegram('fixture')

    def test_both_topic_collectors_share_one_provider_denial_circuit(self):
        response = self.response('denied'); response.status_code = 403
        with mock.patch.object(runner.rw.requests, 'get', return_value=response) as get:
            _, ref_errors = runner.rw.collect_items()
            _, spr_errors = runner.sv.collect_news()
        self.assertEqual(get.call_count, 1)
        self.assertEqual(len(ref_errors), 1); self.assertEqual(len(spr_errors), 1)

    def test_rss_denial_also_blocks_retained_article_decoder_and_direct_news_path(self):
        response = self.response('denied'); response.status_code = 403
        with mock.patch.object(runner.rw.requests, 'get', return_value=response) as get:
            runner.rw.collect_items()
            with self.assertRaises(runner.rw.SourceAccessDenied):
                runner.rw.decode_google_news_url('https://news.google.com/articles/fixture')
            with mock.patch.object(runner.rw, 'gnewsdecoder', return_value={'status': False}):
                with self.assertRaises(runner.rw.SourceAccessDenied):
                    runner.rw.decode_google_news_url('https://news.google.com/unknown-shape')
            self.assertEqual(get.call_count, 1)

    def test_dependency_transport_denial_is_sticky_before_next_request(self):
        response = self.response('denied'); response.status_code = 403
        transport = mock.Mock(get=mock.Mock(return_value=response))
        guarded = runner.rw._SourceTransport(transport)
        for _ in range(2):
            with self.assertRaises(runner.rw.SourceAccessDenied): guarded.get('https://fixture.invalid/a')
        self.assertEqual(transport.get.call_count, 1)

    def test_collectors_stop_same_provider_after_denial(self):
        import requests
        for module, method in ((runner.rw, 'collect_items'), (runner.sv, 'collect_news')):
            for status in (403, 429):
                runner.rw.reset_source_circuits()
                with mock.patch.object(module, 'google_news_rss', side_effect=requests.HTTPError(response=mock.Mock(status_code=status))) as fetch:
                    items, errors = getattr(module, method)()
                    self.assertFalse(items); self.assertEqual(len(errors), 1); self.assertEqual(fetch.call_count, 1)


class WorkflowTests(unittest.TestCase):
    def test_guarded_main_and_secretless_readonly_pr_tests(self):
        text = (ROOT / '.github/workflows/refinery-policy-telegram-watch.yml').read_text()
        self.assertIn('cancel-in-progress: false', text)
        self.assertIn("|| 'refinery-policy-telegram-watch'", text)
        self.assertIn("if: github.ref == 'refs/heads/main' && github.event_name != 'pull_request'", text)
        test_job = text.split('  offline-tests:')[1].split('  monitor:')[0]
        self.assertNotIn('secrets.', test_job); self.assertIn('contents: read', test_job)
        self.assertIn('persist-credentials: false', test_job)
        self.assertNotIn('git push', text); self.assertIn('actions/upload-artifact@v4', text)

class GitBarrierTests(unittest.TestCase):
    """Real disposable local file remotes, including fresh-clone crash recovery."""
    def setUp(self):
        runner.rw.reset_source_circuits()
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name); self.remote = self.folder / 'origin.git'; self.root = self.folder / 'work'
        clean_env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        clean_env.update(HOME=str(self.folder), GIT_CONFIG_GLOBAL='/dev/null', GIT_CONFIG_NOSYSTEM='1',
                         GIT_ALLOW_PROTOCOL='file', GIT_CONFIG_COUNT='1', GIT_CONFIG_KEY_0='core.hooksPath',
                         GIT_CONFIG_VALUE_0='/dev/null')
        isolated = mock.patch.dict(os.environ, clean_env, clear=True)
        isolated.start(); self.addCleanup(isolated.stop)
        self.git(self.folder, 'init', '--bare', str(self.remote))
        self.git(self.folder, 'init', '-b', 'main', str(self.root))
        self.git(self.root, 'config', 'user.email', 'offline@example.invalid')
        self.git(self.root, 'config', 'user.name', 'Offline Test')
        self.now = dt.datetime(2026, 10, 12, 17, tzinfo=runner.KST)
        # Keep the production three-day publication gate tied to the fixture's
        # controlled clock, not the wall clock. Otherwise this test begins
        # failing permanently 3 days after its fixed 2026-10-12 fixture.
        simulated_clock = mock.patch.object(runner, 'now_kst', side_effect=lambda: self.now)
        simulated_clock.start(); self.addCleanup(simulated_clock.stop)
        delivery.atomic_json(self.root / durable.STATE_NAMES[0], {'initialized': True, 'seen': [], 'events': {}})
        delivery.atomic_json(self.root / durable.STATE_NAMES[1], {'initialized': True, 'seen_news': [], 'events': {},
                            'spr': {'date': '2026-10-02'}, 'venezuela_imports': {'date': '2026-10-02'}})
        (self.root / 'program.py').write_text('original\n')
        self.git(self.root, 'add', '.'); self.git(self.root, 'commit', '-m', 'Fixture baseline')
        self.git(self.root, 'remote', 'add', 'origin', str(self.remote)); self.git(self.root, 'push', '-u', 'origin', 'main')
        self.env = mock.patch.dict(os.environ, {'GITHUB_REPOSITORY': durable.EXPECTED_REPOSITORY,
            'GITHUB_REF': 'refs/heads/main', 'GITHUB_EVENT_NAME': 'workflow_dispatch',
            'GITHUB_RUN_ID': 'offline-run', 'GITHUB_WORKSPACE': str(self.root)})
        self.env.start(); self.addCleanup(self.env.stop)
        self.engine = delivery.Outbox(self.root, now=lambda: self.now)
        original = durable.persist
        self.patcher = mock.patch.object(durable, 'persist', side_effect=lambda *a, **kw: original(*a, **kw, expected_remote=str(self.remote)))
        self.persist = self.patcher.start(); self.addCleanup(self.patcher.stop)
        network = mock.patch.object(runner.rw.requests.sessions.Session, 'request', side_effect=AssertionError('live HTTP forbidden'))
        network.start(); self.addCleanup(network.stop)

    def git(self, root, *args):
        result = subprocess.run(['git', *args], cwd=root, capture_output=True, text=True, timeout=15)
        if result.returncode:
            raise AssertionError(result.stderr)
        return result.stdout.strip()

    def queue(self, n='a'):
        item = {'id': n, 'title': 'Trump refinery policy ' + n, 'source': 'Reuters',
                'pub_kst': self.now.isoformat(), 'link': 'https://fixture.invalid/' + n}
        key = self.engine.queue_news('refinery', 'event-' + n, [item])
        self.engine.state['outbox'][key].update(status='ready', message='정유업계의 정책 변화입니다.',
            accepted_ids=[n], retry_items=[], event={'priority': 4, 'last_pub': item['pub_kst'], 'title': item['title']})
        return key

    def response(self):
        return mock.Mock(status_code=200, json=mock.Mock(return_value={'ok': True, 'result': {'message_id': 101, 'chat': {'id': 42}}}))

    def remote_state(self, name=durable.STATE_NAMES[2]):
        return json.loads(self.git(self.folder, '--git-dir=' + str(self.remote), 'show', 'main:' + name))

    def clone_restart(self):
        clone = self.folder / 'restart'
        self.git(self.folder, 'clone', '-b', 'main', str(self.remote), str(clone))
        os.environ['GITHUB_WORKSPACE'] = str(clone)
        self.engine = delivery.Outbox(clone, now=lambda: self.now)
        return self.engine

    def other_writer(self, name):
        clone = self.folder / 'writer'
        self.git(self.folder, 'clone', '-b', 'main', str(self.remote), str(clone))
        self.git(clone, 'config', 'user.name', 'Other Writer'); self.git(clone, 'config', 'user.email', 'other@example.invalid')
        path = clone / name
        if name.endswith('.json'):
            value = json.loads(path.read_text()) if path.exists() else {}
            value['other_writer'] = True; delivery.atomic_json(path, value)
        else:
            path.write_text('unrelated remote change\n')
        self.git(clone, 'add', name); self.git(clone, 'commit', '-m', 'Other writer'); self.git(clone, 'push', 'origin', 'main')

    def test_real_three_file_checkpoint_and_exact_readback(self):
        key = self.queue(); self.engine.checkpoint()
        self.assertIn(key, self.remote_state()['outbox'])
        for name in durable.STATE_NAMES:
            raw = subprocess.run(['git', '--git-dir=' + str(self.remote), 'show', 'main:' + name], capture_output=True).stdout
            self.assertEqual(raw, (self.root / name).read_bytes())

    def test_crash_at_send_boundary_is_held_in_fresh_clone(self):
        key = self.queue()
        with mock.patch.object(runner.rw.requests, 'post', side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt): self.engine.deliver(key, 'fixture', '42', runner.rw)
        self.assertEqual(self.remote_state()['outbox'][key]['status'], 'delivery_unknown')
        self.clone_restart()
        with mock.patch.object(runner.rw.requests, 'post') as post:
            runner._deliver_topic(self.engine, 'refinery', 'fixture', '42')
            post.assert_not_called()
        self.assertEqual(self.engine.refinery['seen'], [])

    def test_crash_before_transmission_after_checkpoint_is_held(self):
        key = self.queue(); original = self.engine.checkpoint
        def checkpoint(intent=None):
            result = original(intent)
            if intent is not None: raise KeyboardInterrupt()
            return result
        self.engine.checkpoint = checkpoint
        with mock.patch.object(runner.rw.requests, 'post') as post:
            with self.assertRaises(KeyboardInterrupt): self.engine.deliver(key, 'fixture', '42', runner.rw)
            post.assert_not_called()
        self.clone_restart()
        self.assertEqual(self.engine.state['outbox'][key]['status'], 'delivery_unknown')

    def test_crash_after_receipt_checkpoint_finalizes_without_resending(self):
        key = self.queue(); original = self.engine.checkpoint
        def checkpoint(intent=None):
            result = original(intent)
            chunks = self.engine.state['outbox'].get(key, {}).get('chunks', [])
            if chunks and all(x['status'] == 'confirmed' for x in chunks): raise KeyboardInterrupt()
            return result
        self.engine.checkpoint = checkpoint
        with mock.patch.object(runner.rw.requests, 'post', return_value=self.response()):
            with self.assertRaises(KeyboardInterrupt): self.engine.deliver(key, 'fixture', '42', runner.rw)
        self.clone_restart(); self.now += dt.timedelta(days=4)
        with mock.patch.object(runner.rw.requests, 'post') as post:
            runner._deliver_topic(self.engine, 'refinery', 'fixture', '42'); post.assert_not_called()
        self.assertIn('a', self.engine.refinery['seen']); self.assertEqual(self.engine.state['outbox'], {})

    def test_crash_after_receipt_before_persist_keeps_remote_hold(self):
        key = self.queue(); original = self.engine.checkpoint
        def checkpoint(intent=None):
            chunks = self.engine.state['outbox'][key].get('chunks', [])
            if chunks and all(x['status'] == 'confirmed' for x in chunks): raise KeyboardInterrupt()
            return original(intent)
        self.engine.checkpoint = checkpoint
        with mock.patch.object(runner.rw.requests, 'post', return_value=self.response()):
            with self.assertRaises(KeyboardInterrupt): self.engine.deliver(key, 'fixture', '42', runner.rw)
        self.clone_restart(); self.assertEqual(self.engine.state['outbox'][key]['status'], 'delivery_unknown')

    def test_confirmed_unknown_and_unattempted_tail_survive_fresh_clone(self):
        keys = [self.queue(x) for x in ('a', 'b', 'c')]
        self.engine.attempts = 4
        self.engine.checkpoint()
        with mock.patch.object(runner.rw.requests, 'post', side_effect=[self.response(), TimeoutError()]):
            runner._deliver_topic(self.engine, 'refinery', 'fixture', '42')
        self.engine.checkpoint(); self.clone_restart()
        with mock.patch.object(runner.rw.requests, 'post', return_value=self.response()) as post:
            runner._deliver_topic(self.engine, 'refinery', 'fixture', '42'); self.assertEqual(post.call_count, 1)
        self.assertIn('a', self.engine.refinery['seen']); self.assertIn('c', self.engine.refinery['seen'])
        self.assertNotIn('b', self.engine.refinery['seen']); self.assertEqual(self.engine.state['outbox'][keys[1]]['status'], 'delivery_unknown')

    def conflict(self, name):
        self.engine.checkpoint(); self.queue(); self.other_writer(name)
        with self.assertRaises(delivery.DeliveryError): self.engine.checkpoint()
        self.assertTrue(self.engine.blocked)
        self.assertTrue(self.remote_state(name)['other_writer'])
        with self.assertRaises(delivery.DeliveryError): self.engine.checkpoint()
        self.assertTrue(self.remote_state(name)['other_writer'])

    def test_remote_refinery_change_blocks_all_sends_and_final_save(self): self.conflict(durable.STATE_NAMES[0])
    def test_remote_spr_change_blocks_all_sends_and_final_save(self): self.conflict(durable.STATE_NAMES[1])
    def test_remote_outbox_change_blocks_all_sends_and_final_save(self): self.conflict(durable.STATE_NAMES[2])

    def test_unrelated_remote_change_is_preserved(self):
        self.queue(); self.other_writer('other.txt'); self.engine.checkpoint()
        self.assertEqual((self.root / 'other.txt').read_text(), 'unrelated remote change\n')

    def test_unknown_push_ack_is_accepted_only_with_exact_remote_readback(self):
        self.queue(); original = durable._git
        def git(root, *args, **kw):
            result = original(root, *args, **kw)
            if args[0] == 'push': return subprocess.CompletedProcess([], 1, result.stdout, b'uncertain')
            return result
        with mock.patch.object(durable, '_git', side_effect=git): self.engine.checkpoint()
        self.assertEqual(len(self.remote_state()['outbox']), 1)

    def test_rejected_push_and_readback_missing_blocks(self):
        self.queue(); original = durable._git
        def git(root, *args, **kw):
            if args[0] == 'push': return subprocess.CompletedProcess([], 1, b'', b'rejected')
            return original(root, *args, **kw)
        with mock.patch.object(durable, '_git', side_effect=git):
            with self.assertRaises(delivery.DeliveryError): self.engine.checkpoint()
        self.assertTrue(self.engine.blocked)

    def test_one_mismatched_remote_bundle_hash_blocks(self):
        self.queue(); original = durable._git
        def git(root, *args, **kw):
            result = original(root, *args, **kw)
            if args == ('show', 'FETCH_HEAD:' + durable.STATE_NAMES[1]):
                return subprocess.CompletedProcess([], 0, b'{"different":true}', b'')
            return result
        with mock.patch.object(durable, '_git', side_effect=git):
            with self.assertRaises(delivery.DeliveryError): self.engine.checkpoint()
        self.assertTrue(self.engine.blocked)

    def test_readback_failure_never_authorizes_send(self):
        key = self.queue(); original = durable._git; pushed = False
        def git(root, *args, **kw):
            nonlocal pushed
            if pushed and args[0] == 'fetch': return subprocess.CompletedProcess([], 1, b'', b'fail')
            result = original(root, *args, **kw)
            if args[0] == 'push': pushed = True
            return result
        with mock.patch.object(durable, '_git', side_effect=git), mock.patch.object(runner.rw.requests, 'post') as post:
            with self.assertRaises(delivery.DeliveryError): self.engine.deliver(key, 'fixture', '42', runner.rw)
            post.assert_not_called()

    def test_unrelated_dirty_file_is_rejected(self):
        (self.root / 'program.py').write_text('dirty\n')
        with self.assertRaises(delivery.DeliveryError): self.engine.checkpoint()

    def test_unpublished_source_commit_and_revert_are_rejected(self):
        (self.root / 'program.py').write_text('changed\n'); self.git(self.root, 'add', '.'); self.git(self.root, 'commit', '-m', 'Source change')
        self.git(self.root, 'revert', '--no-edit', 'HEAD')
        with self.assertRaises(delivery.DeliveryError): self.engine.checkpoint()

    def test_bad_context_and_push_remote_are_rejected(self):
        self.queue()
        with mock.patch.dict(os.environ, {'GITHUB_REF': 'refs/heads/test'}):
            with self.assertRaises(delivery.DeliveryError): self.engine.checkpoint()
        self.engine.blocked = False
        self.git(self.root, 'remote', 'set-url', '--push', 'origin', str(self.folder / 'wrong.git'))
        with self.assertRaises(delivery.DeliveryError): self.engine.checkpoint()

    def test_marker_hash_mismatch_blocks(self):
        self.engine.save_local(); (self.root / durable.STATE_NAMES[1]).write_text('{}')
        with self.assertRaises(durable.DurableCheckpointError):
            durable._validate_checkpoint(self.root, self.engine.marker, str(self.remote))

    def test_tampered_intent_digest_blocks(self):
        key = self.queue(); entry = self.engine.state['outbox'][key]
        entry.update(status='delivery_unknown', destination_sha256='destination', chunks=[
            {'text': '실제 본문', 'message_sha256': 'false-digest', 'status': 'delivery_unknown'}])
        self.engine.save_local()
        with self.assertRaisesRegex(durable.DurableCheckpointError, 'Exact prepared delivery intent is missing'):
            durable.persist(self.root, self.engine.marker, intent=(key, 0, 'false-digest', 'destination'))



class SourceDiagnosticTests(unittest.TestCase):
    def setUp(self):
        runner.rw.reset_source_circuits()
        p = mock.patch.object(runner.rw.requests.sessions.Session, 'request', side_effect=AssertionError('live HTTP forbidden'))
        p.start(); self.addCleanup(p.stop)

    def response(self, status, url='https://provider.example/article', text='denied'):
        return mock.Mock(status_code=status, url=url, text=text)

    def failure(self, status=403, stage='news_decode', url='https://news.google.com/articles/fixture'):
        transport = mock.Mock(get=mock.Mock(return_value=self.response(status, url)))
        with self.assertRaises(runner.rw.SourceAccessDenied) as ctx:
            runner.rw.source_request(transport, 'get', url, stage=stage)
        return ctx.exception

    def test_http_denial_keeps_actual_provider_status_stage_without_url(self):
        for status in (401, 403, 429):
            runner.rw.reset_source_circuits()
            failure = self.failure(status)
            details = runner.rw.source_failure_details(failure)
            self.assertEqual(details['http_status'], status)
            self.assertEqual(details['provider'], 'news.google.com')
            self.assertEqual(details['stage'], 'news_decode')
            self.assertEqual(details['reason'], 'http_rate_limit' if status == 429 else 'http_denial')
            self.assertFalse(details['blocked_before_request'])
            self.assertNotIn('url', details)

    def test_wrapper_reraise_preserves_diagnostic_identity(self):
        original = self.failure()
        with self.assertRaises(runner.rw.SourceAccessDenied) as ctx:
            runner.rw.raise_if_access_denied(original)
        self.assertIs(ctx.exception, original)
        self.assertEqual(ctx.exception.source_details['http_status'], 403)

    def test_circuit_hold_reports_earlier_status_not_new_http_response(self):
        self.failure(429, 'rss', 'https://news.google.com/rss/search?q=fixture')
        transport = mock.Mock(get=mock.Mock(side_effect=AssertionError('no second request')))
        with self.assertRaises(runner.rw.SourceAccessDenied) as ctx:
            runner.rw.source_request(transport, 'get', 'https://news.google.com/articles/other', stage='news_decode')
        details = runner.rw.source_failure_details(ctx.exception)
        self.assertEqual(details['reason'], 'in_run_circuit')
        self.assertEqual(details['origin_http_status'], 429)
        self.assertEqual(details['origin_stage'], 'rss')
        self.assertNotIn('http_status', details)
        self.assertTrue(details['blocked_before_request']); transport.get.assert_not_called()

    def test_real_decoder_swallowed_exception_preserves_observed_status(self):
        import requests
        response = requests.Response(); response.status_code = 403
        response.url = 'https://news.google.com/articles/fixture'; response._content = b'denied'
        with mock.patch.object(requests.sessions.Session, 'request', return_value=response) as request:
            with self.assertRaises(runner.rw.SourceAccessDenied) as ctx:
                runner.rw.decode_google_news_url(response.url)
        details = runner.rw.source_failure_details(ctx.exception)
        self.assertEqual(details['stage'], 'news_decode'); self.assertEqual(details['http_status'], 403)
        self.assertEqual(request.call_count, 1)

    def test_real_translators_preserve_provider_and_stage(self):
        import requests
        for translate, stage, host in (
            (v2._original_translate_ko, 'translation_google', 'translate.google.com'),
            (v2._translate_with_mymemory, 'translation_mymemory', 'api.mymemory.translated.net')):
            response = requests.Response(); response.status_code = 403
            response.url = 'https://' + host + '/private?secret=never-record'; response._content = b'denied'
            with mock.patch.object(requests.sessions.Session, 'request', return_value=response) as request:
                with self.assertRaises(runner.rw.SourceAccessDenied) as ctx:
                    translate('A general English sentence requiring translation.')
            details = runner.rw.source_failure_details(ctx.exception)
            self.assertEqual(details['stage'], stage); self.assertEqual(details['provider'], host)
            self.assertEqual(details['http_status'], 403); self.assertEqual(request.call_count, 1)
            self.assertNotIn('never-record', json.dumps(details))

    def test_article_denial_and_http200_challenge_are_distinguished(self):
        for status, text, reason in ((403, 'denied', 'http_denial'),
                                    (200, '<html><title>Access denied</title></html>', 'challenge_page')):
            with mock.patch.object(runner.rw.requests, 'get', return_value=self.response(status, text=text)):
                with self.assertRaises(runner.rw.SourceAccessDenied) as ctx:
                    runner.rw.extract_article_body('https://provider.example/article')
            details = runner.rw.source_failure_details(ctx.exception)
            self.assertEqual(details['stage'], 'article_fetch'); self.assertEqual(details['reason'], reason)
            self.assertEqual(details['http_status'], status)

    def test_direct_http_wrappers_keep_status_and_context(self):
        import requests
        response = self.response(403, 'https://html.duckduckgo.com/html/?q=private-query')
        response.raise_for_status.side_effect = requests.HTTPError(response=response)
        with mock.patch.object(runner.rw.requests, 'get', return_value=response):
            with self.assertRaises(runner.rw.SourceAccessDenied) as ctx:
                runner.rw.search_publisher_url({'title': 'Fixture', 'source_url': 'https://publisher.example'})
        details = runner.rw.source_failure_details(ctx.exception)
        self.assertEqual(details['stage'], 'publisher_search'); self.assertEqual(details['http_status'], 403)
        self.assertEqual(details['provider'], 'html.duckduckgo.com')
        response = self.response(403, 'https://translate.googleapis.com/translate_a/single?q=private-text')
        response.raise_for_status.side_effect = requests.HTTPError(response=response)
        with mock.patch.object(runner.rw.GoogleTranslator, 'translate', side_effect=RuntimeError('temporary failure')), \
             mock.patch.object(runner.rw.time, 'sleep'), mock.patch.object(runner.rw.requests, 'get', return_value=response):
            with self.assertRaises(runner.rw.SourceAccessDenied) as ctx:
                v2._original_translate_ko('A general sentence requiring translation.')
        details = runner.rw.source_failure_details(ctx.exception)
        self.assertEqual(details['stage'], 'translation_google_http'); self.assertEqual(details['http_status'], 403)
        self.assertEqual(details['provider'], 'translate.googleapis.com')
        self.assertNotIn('private-text', json.dumps(details))

    def test_unknown_exception_does_not_invent_provider_or_status(self):
        failure = runner.rw.SourceAccessDenied('HTTP 403 secret response text')
        details = runner.rw.source_failure_details(failure)
        self.assertEqual(details['stage'], 'unknown'); self.assertEqual(details['reason'], 'unknown')
        self.assertNotIn('provider', details); self.assertNotIn('http_status', details)
        self.assertNotIn('secret', json.dumps(details))

    def test_raw_userinfo_path_query_fragment_body_never_enter_diagnostics(self):
        url = 'https://user:password@provider.example/private-credential?token=api-secret#private-fragment'
        failure = self.failure(403, 'article_fetch', url)
        failure.source_details.update(response_body='do-not-record', exception='do-not-record', raw_url=url)
        details = runner.rw.source_failure_details(failure)
        text = json.dumps(details)
        for secret in ('password', 'private-credential', 'api-secret', 'private-fragment', 'do-not-record', 'user:'):
            self.assertNotIn(secret, text)
        self.assertEqual(details['provider'], 'provider.example')
        self.assertEqual(details['request_url_sha256'], hashlib.sha256(url.encode()).hexdigest())

    def test_mutated_metadata_fields_are_validated(self):
        failure = runner.rw.SourceAccessDenied()
        failure.source_details.update(provider='secret@invalid\n', request_url_sha256='raw-url', stage='secret',
                                      reason='secret', http_status=True, origin_http_status='403', ignored='secret')
        details = runner.rw.source_failure_details(failure)
        self.assertEqual(details, {'stage': 'unknown', 'reason': 'unknown', 'blocked_before_request': False})

    def test_malformed_diagnostic_metadata_degrades_to_unknown(self):
        for malformed in (None, [], {'stage': [], 'reason': {}, 'origin_stage': [], 'origin_reason': {}}):
            failure = runner.rw.SourceAccessDenied(stage=[], reason={})
            failure.source_details = malformed
            self.assertEqual(runner.rw.source_failure_details(failure),
                {'stage': 'unknown', 'reason': 'unknown', 'blocked_before_request': False})

    def test_redirect_denial_origin_provider_survives_next_circuit_hold(self):
        transport = mock.Mock(get=mock.Mock(return_value=self.response(403, 'https://publisher.example/redirect-target')))
        with self.assertRaises(runner.rw.SourceAccessDenied):
            runner.rw.source_request(transport, 'get', 'https://news.google.com/articles/first', stage='news_decode')
        with self.assertRaises(runner.rw.SourceAccessDenied) as ctx:
            runner.rw.source_request(transport, 'get', 'https://news.google.com/articles/second', stage='news_decode')
        details = runner.rw.source_failure_details(ctx.exception)
        self.assertEqual(details['provider'], 'news.google.com')
        self.assertEqual(details['origin_provider'], 'publisher.example')
        self.assertEqual(details['origin_http_status'], 403)
        self.assertEqual(transport.get.call_count, 1)

    def test_future_blocked_item_and_artifact_retain_metadata_after_restart_merge(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            delivery.atomic_json(root / 'refinery_watch_state.json', {'initialized': True, 'seen': [], 'events': {}})
            delivery.atomic_json(root / 'spr_venezuela_watch_state.json', {'initialized': True, 'seen_news': [], 'events': {}})
            now = dt.datetime(2026, 10, 12, 21, tzinfo=dt.timezone.utc)
            item = {'id': 'new', 'title': 'Trump refinery capacity', 'source': 'EPA', 'source_url': 'https://epa.gov', 'pub_kst': now.isoformat()}
            engine = delivery.Outbox(root, now=lambda: now)
            key = engine.queue_news('refinery', runner.refinery_event_key(item), [item])
            with mock.patch.object(runner, 'now_kst', return_value=now), \
                 mock.patch.object(runner.rw, 'enrich_korean', side_effect=self.failure()) as enrich, \
                 mock.patch.object(runner.rw.requests, 'post', side_effect=AssertionError('no Telegram')):
                runner._prepare_news(engine, key)
                engine.save_local()
            state = json.loads((root / 'energy_delivery_state.json').read_text())
            self.assertEqual(state['outbox'][key]['items'][0]['_source_failure']['http_status'], 403)
            diag = json.loads((root / 'out/energy_delivery_diagnostics.json').read_text())
            self.assertEqual(diag['source_failures'][0]['provider'], 'news.google.com')
            reloaded = delivery.Outbox(root, now=lambda: now)
            # A new same-event alternative must not erase the original metadata.
            reloaded.queue_news('refinery', runner.refinery_event_key(item), [item, dict(item, id='alternative')])
            original = next(x for x in reloaded.state['outbox'][key]['items'] if x['id'] == 'new')
            self.assertTrue(original['_source_blocked']); self.assertEqual(original['_source_failure']['http_status'], 403)
            self.assertEqual(reloaded.refinery['seen'], [])

    def test_existing_legacy_hold_does_not_get_inferred_metadata_or_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            delivery.atomic_json(root / 'refinery_watch_state.json', {'initialized': True, 'seen': ['historic'], 'events': {}})
            delivery.atomic_json(root / 'spr_venezuela_watch_state.json', {'initialized': True, 'seen_news': [], 'events': {}})
            item = {'id': '3732d92a79dc14aedccf4fe8', 'title': 'Existing blocked article', 'source': 'Energy News Beat',
                    'source_url': 'https://energynewsbeat.co', 'pub_kst': '2026-10-13T01:33+09:00', '_source_blocked': True}
            old = {'schema_version': 1, 'outbox': {'refinery:news:legacy': {'topic': 'refinery', 'kind': 'news',
                'event_key': 'legacy', 'status': 'source_blocked', 'items': [item], 'attempts': 1}},
                'receipts': [], 'dispositions': [], 'observed_data': {}}
            delivery.atomic_json(root / 'energy_delivery_state.json', old)
            engine = delivery.Outbox(root)
            with mock.patch.object(runner.rw, 'enrich_korean', side_effect=AssertionError('no refetch')), \
                 mock.patch.object(runner.rw.requests, 'post', side_effect=AssertionError('no Telegram')):
                runner._deliver_topic(engine, 'refinery', 'fixture', '42')
            self.assertEqual(engine.state, old); self.assertEqual(engine.source_failures, [])
            self.assertNotIn('_source_failure', engine.state['outbox']['refinery:news:legacy']['items'][0])

if __name__ == '__main__':
    unittest.main()
