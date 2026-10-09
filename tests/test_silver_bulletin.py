"""Regression tests for the live Silver Bulletin -> Telegram decision path."""
import datetime as dt
import json
import os
import pathlib
import tempfile
import unittest
from unittest import mock

import silver_bulletin_watch as sw


class FakeResponse:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class SilverBulletinTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = pathlib.Path(self.folder.name)
        self.old_paths = (sw.STATE, sw.OUT, sw.ALERT, sw.PENDING, sw.STATUS, sw.DEBUG)
        sw.STATE = self.root / "state.json"
        sw.OUT = self.root / "out"
        sw.ALERT = sw.OUT / "alert.txt"
        sw.PENDING = sw.OUT / "pending.json"
        sw.STATUS = sw.OUT / "status.txt"
        sw.DEBUG = sw.OUT / "debug.json"
        self.addCleanup(self._restore_paths)
        self.today = dt.datetime.now(dt.timezone.utc).date()
        self.old_date = (self.today - dt.timedelta(days=1)).isoformat()

    def _restore_paths(self):
        (sw.STATE, sw.OUT, sw.ALERT, sw.PENDING, sw.STATUS, sw.DEBUG) = self.old_paths

    @staticmethod
    def values(cost=-48.0, economy=-33.0, immigration=-12.0, trade=-28.0):
        return {
            "cost_of_living": {"value": cost},
            "economy": {"value": economy},
            "immigration": {"value": immigration},
            "trade": {"value": trade},
        }

    def seed(self, observed=None, alerted=None, version=4):
        state = {
            "parser_version": version,
            "data_date": self.old_date,
            "datawrapper_revision": 7800,
            "values": observed if observed is not None else self.values(),
        }
        if alerted is not None:
            state["last_alert_values"] = alerted
            state["last_alert_data_date"] = self.old_date
        sw.STATE.write_text(json.dumps(state), encoding="utf-8")

    def run_watch(self, cost=-48, economy=-33, immigration=-12, trade=-28,
                  data_date=None, official_chart=True):
        date = data_date or self.today.isoformat()
        csv_text = (
            "modeldate,cost,econ,immg,trade\n"
            f"{date},{cost},{economy},{immigration},{trade}\n"
        )
        chart_id = "RFXsV" if official_chart else "WRONG"
        page = FakeResponse(
            '<html><iframe src="https://datawrapper.dwcdn.net/'
            f'{chart_id}/7800/"></iframe></html>'
        )
        with mock.patch.object(sw.SESSION, "get", return_value=page), \
             mock.patch.object(sw, "dataset_text", return_value=csv_text), \
             mock.patch.object(sw, "latest_revision", return_value=7845):
            return sw.main()

    def test_strict_numbers_reject_partial_strings(self):
        self.assertEqual(sw.num("-47.9%"), -47.9)
        self.assertEqual(sw.num("+0.0"), 0.0)
        for bad in ("garbage -47.9%", "-47.9 xyz", "2026-10-08", "101%", ""):
            self.assertIsNone(sw.num(bad), bad)

    def test_extract_all_four_issues_from_latest_dated_row(self):
        headers, rows = sw.parse_csv(
            "modeldate,cost,econ,immg,trade\n"
            "2026-10-07,-48.3,-34,-12,-28\n"
            "2026-10-08,-48.0,-33,-11,-29\n"
        )
        item = sw.extract_issue_average(headers, rows)
        self.assertEqual(item["date"], "2026-10-08")
        self.assertEqual(item["values"]["economy"], -33)
        self.assertIsNone(sw.extract_issue_average(["date", "cost", "econ"], rows))

    def test_no_small_change_alert_but_refresh_date(self):
        self.seed()
        result = self.run_watch(economy=-32.6)
        self.assertEqual(result, 0)
        self.assertFalse(sw.ALERT.exists())
        self.assertTrue(sw.PENDING.exists())
        state = json.loads(sw.PENDING.read_text())
        self.assertEqual(state["data_date"], self.today.isoformat())
        self.assertEqual(state["values"]["economy"]["value"], -32.6)
        self.assertEqual(state["last_alert_values"]["economy"]["value"], -33.0)

    def test_one_check_two_point_threshold(self):
        self.seed()
        self.assertEqual(self.run_watch(economy=-31.0), 0)
        self.assertIn("+2.0%p", sw.ALERT.read_text(encoding="utf-8"))
        state = json.loads(sw.PENDING.read_text())
        self.assertEqual(state["last_alert_values"]["economy"]["value"], -31.0)

    def test_cumulative_three_point_threshold(self):
        observed = self.values(economy=-31.0)
        alerted = self.values(economy=-34.0)
        self.seed(observed=observed, alerted=alerted)
        self.assertEqual(self.run_watch(economy=-30.9), 0)
        self.assertIn("+3.1%p", sw.ALERT.read_text(encoding="utf-8"))

    def test_no_repeat_after_ack(self):
        self.seed()
        self.assertEqual(self.run_watch(economy=-31.0), 0)
        sw.STATE.write_text(sw.PENDING.read_text(), encoding="utf-8")
        self.assertEqual(self.run_watch(economy=-31.0), 0)
        self.assertFalse(sw.ALERT.exists())

    def test_legacy_state_migration_does_not_spam(self):
        self.seed(version=3)
        self.assertEqual(self.run_watch(economy=-32.9), 0)
        self.assertFalse(sw.ALERT.exists())
        self.assertEqual(json.loads(sw.PENDING.read_text())["parser_version"], 4)

    def test_future_or_old_date_is_rejected(self):
        self.seed()
        old = (self.today - dt.timedelta(days=10)).isoformat()
        self.assertEqual(self.run_watch(data_date=old), 2)
        self.assertFalse(sw.ALERT.exists())
        self.assertFalse(sw.PENDING.exists())

    def test_reversed_source_date_is_rejected(self):
        self.seed()
        older = (self.today - dt.timedelta(days=2)).isoformat()
        self.assertEqual(self.run_watch(data_date=older), 2)
        self.assertFalse(sw.ALERT.exists())

    def test_missing_official_issue_iframe_fails_closed(self):
        self.seed()
        self.assertEqual(self.run_watch(official_chart=False), 2)
        self.assertFalse(sw.ALERT.exists())
        self.assertFalse(sw.PENDING.exists())

    def test_exactly_same_values_new_date_updates_state_not_telegram(self):
        self.seed()
        self.assertEqual(self.run_watch(), 0)
        self.assertTrue(sw.PENDING.exists())
        self.assertFalse(sw.ALERT.exists())
        self.assertEqual(json.loads(sw.PENDING.read_text())["data_date"], self.today.isoformat())


    def test_telegram_source_link_has_no_emoji_or_exposed_url(self):
        raw = (
            "[Silver Bulletin 트럼프 이슈 지지율 감시] 변화 감지\n"
            "- 경제: -35.0%p → -32.0%p (+3.0%p)\n"
            "- 원문: " + sw.PAGE_URL
        )
        msg = sw.format_telegram_html(raw)
        self.assertTrue(msg.startswith("<b>[Silver Bulletin"))
        self.assertIn('<a href="' + sw.PAGE_URL + '">원문</a>', msg)
        self.assertNotIn("🔗", msg)
        self.assertNotIn("- 원문: ", msg)

    def test_telegram_html_escapes_user_text_and_rejects_other_sources(self):
        raw = "검증 <오류> & 경고\n- 원문: " + sw.PAGE_URL
        msg = sw.format_telegram_html(raw)
        self.assertIn("&lt;오류&gt; &amp;", msg)
        with self.assertRaises(ValueError):
            sw.format_telegram_html("제목\n- 원문: https://invalid.example/")

    def test_invalid_page_response_suppresses_alert(self):
        self.seed()
        with mock.patch.object(sw.SESSION, "get", return_value=FakeResponse("", 503)):
            self.assertEqual(sw.main(), 2)
        self.assertFalse(sw.ALERT.exists())
        self.assertFalse(sw.PENDING.exists())

    def test_failed_upstream_dataset_is_not_treated_as_missing_revision(self):
        with mock.patch.object(sw.SESSION, "get", return_value=FakeResponse("oops", 429)):
            with self.assertRaises(RuntimeError):
                sw.dataset_text("RANDOMFAILID", 1)


if __name__ == "__main__":
    unittest.main()
