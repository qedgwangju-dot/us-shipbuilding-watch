"""Regression tests: Iran election-policy monitor, independent evidence, dedupe."""
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import iran_midterm_commitment_watch as m

NOW = dt.datetime(2026, 10, 9, 15, 0, tzinfo=dt.timezone.utc)


def ev(kind, source, title, hours=0, official=False, url="https://news.google.com/articles/a"):
    return m.Evidence(
        kind=kind, source=source, title=title,
        published=NOW + dt.timedelta(hours=hours),
        url=(m.CANONICAL_PLEDGE_URL if official else url + source.replace(" ", "")),
        raw_text=title, official_voice=official,
    )


class IranMidtermTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.old_paths = (m.STATE, m.OUT, m.PENDING, m.MESSAGE, m.REPORT, m.DEBUG)
        root = Path(self.temp.name)
        m.STATE = root / "state.json"
        m.OUT = root / "out"
        m.PENDING = m.OUT / "pending.json"
        m.MESSAGE = m.OUT / "telegram.html"
        m.REPORT = m.OUT / "status.md"
        m.DEBUG = m.OUT / "debug.json"
        self.addCleanup(self.reset_paths)

    def reset_paths(self):
        m.STATE, m.OUT, m.PENDING, m.MESSAGE, m.REPORT, m.DEBUG = self.old_paths

    def test_known_non_attack_promise_not_reversal(self):
        p = ("We will not be attacking Iran at any time prior to the "
             "Midterm Elections on November 3rd")
        self.assertIsNone(m.classify_official(p))

    def test_archive_intent_explicit_reversal(self):
        self.assertEqual(
            m.classify_official("We will attack Iran before the elections."),
            "policy_reversal",
        )
        self.assertIsNone(m.classify_official("Iran nuclear file is discussed."))
        self.assertIsNone(m.classify_official("We will attack Yemen."))

    def test_press_rejects_rumors_and_negation(self):
        titles = [
            "Trump could attack Iran before the midterms",
            "Trump considering US strikes in Iran after election",
            "Trump says US will not attack Iran before Nov. 3",
            "Trump Says U.S. Won’t Resume Strikes on Iran Before Midterms - WSJ",
            "Trump says U.S. won't resume strikes on Iran before the elections",
            "United States will not strike Iran until after November 3",
            "Trump rules out US air strikes on Iran before midterms",
            "Trump postpones U.S. attack on Iran",
            "Will Trump attack Iran before the midterm election?",
            "Iran attacks US positions in gulf",
            "Israel strikes Iran near capital",
        ]
        for title in titles:
            self.assertIsNone(m.classify_press(title, title), title)

    def test_press_categorization_definite_changes(self):
        self.assertEqual(
            m.classify_press("Trump reverses Iran attack pledge before midterms", ""),
            "policy_reversal",
        )
        self.assertEqual(
            m.classify_press("U.S. launches new strikes on Iranian military targets", ""),
            "us_strike",
        )

    def test_archive_alone_policy_reversal_is_official_announcement(self):
        candidate = ev("policy_reversal", "트럼프 게시물 보존본",
                       "I will attack Iran", official=True)
        self.assertEqual(m.decide([candidate], NOW.date())[0], "policy_reversal")

    def test_one_publisher_does_not_verify_strikes(self):
        candidate = ev("us_strike", "Reuters", "US launches new strikes on Iran")
        self.assertIsNone(m.decide([candidate], NOW.date()))
        duplicate = ev("us_strike", "Reuters", "US conducts new strikes in Iran", url="https://news.google.com/articles/b")
        self.assertIsNone(m.decide([candidate, duplicate], NOW.date()))

    def test_two_different_publishers_verify_strike(self):
        candidates = [
            ev("us_strike", "Reuters", "US launches new strikes on Iran"),
            ev("us_strike", "Associated Press", "US launches new strikes on Iran"),
        ]
        self.assertEqual(m.decide(candidates, NOW.date())[0], "us_strike")

    def test_old_independent_stories_do_not_count_as_joint_verification(self):
        candidates = [
            ev("us_strike", "Reuters", "US launches new strikes on Iran", hours=-47),
            ev("us_strike", "Associated Press", "US launches new strikes on Iran"),
        ]
        self.assertIsNone(m.decide(candidates, NOW.date()))

    def test_trusted_provider_exact_no_lookalikes(self):
        self.assertEqual(m.provider("Reuters"), "Reuters")
        self.assertEqual(m.provider("Associated Press"), "Associated Press")
        self.assertIsNone(m.provider("Reuters Daily Blog"))
        self.assertIsNone(m.provider("Some personal account"))

    def test_no_false_clickable_link_or_emoji(self):
        body = m.format_message("policy_reversal", [
            ev("policy_reversal", "트럼프 게시물 보존본",
               "We changed our commitment about Iran", official=True)
        ], NOW)
        self.assertIn('>원문</a>', body)
        self.assertNotIn("🔗", body)
        self.assertNotIn("- 원문:", body)

    def test_url_restrictions(self):
        for url in ("http://truthsocial.com/a",
                    "https://evil.truthsocial.com/a",
                    "javascript:alert(1)"):
            with self.assertRaises(ValueError):
                m.validated_url(url)
        self.assertTrue(m.validated_url(m.CANONICAL_PLEDGE_URL).startswith("https://"))

    def test_old_promise_does_not_trigger_again(self):
        with mock.patch.object(m, "now_utc", return_value=NOW), \
             mock.patch.object(m, "read_archive", return_value=([], True, [])), \
             mock.patch.object(m, "read_press", return_value=([], 3, [])):
            self.assertEqual(m.main(), 0)
            self.assertFalse(m.MESSAGE.exists())
            self.assertFalse(m.PENDING.exists())

    def test_new_confirmed_event_then_after_ack_no_duplicate(self):
        rows = [
            ev("policy_reversal", "Reuters", "Trump reverses Iran pledge"),
            ev("policy_reversal", "Associated Press", "Trump reverses Iran pledge"),
        ]
        with mock.patch.object(m, "now_utc", return_value=NOW), \
             mock.patch.object(m, "read_archive", return_value=([], True, [])), \
             mock.patch.object(m, "read_press", return_value=(rows, 3, [])):
            self.assertEqual(m.main(), 0)
            self.assertTrue(m.MESSAGE.exists())
            self.assertTrue(m.PENDING.exists())
            m.STATE.write_text(m.PENDING.read_text(), encoding="utf-8")
            self.assertEqual(m.main(), 0)
            self.assertFalse(m.MESSAGE.exists())

    def test_all_sources_fail_closed(self):
        with mock.patch.object(m, "now_utc", return_value=NOW), \
             mock.patch.object(m, "read_archive", return_value=([], False, ["Archive inaccessible"])), \
             mock.patch.object(m, "read_press", return_value=([], 1, ["RSS down"])):
            self.assertEqual(m.main(), 2)
            self.assertFalse(m.MESSAGE.exists())

    def test_expired_watcher_does_not_alert(self):
        late = dt.datetime(2026, 11, 19, 15, 0, tzinfo=dt.timezone.utc)
        with mock.patch.object(m, "now_utc", return_value=late):
            self.assertEqual(m.main(), 0)
            self.assertFalse(m.MESSAGE.exists())

    def test_article_dates_must_be_fresh(self):
        self.assertFalse(m.recent(NOW - dt.timedelta(hours=53), NOW))
        self.assertTrue(m.recent(NOW - dt.timedelta(hours=47), NOW))
        self.assertFalse(m.recent(NOW + dt.timedelta(hours=1), NOW))

    def test_rss_timestamp_parser(self):
        stamp = m.parse_pubdate("Thu, 08 Oct 2026 16:17:00 GMT")
        self.assertEqual(stamp.date(), dt.date(2026, 10, 8))

    def test_baseline_state_corruption_stops_pipeline(self):
        m.STATE.write_text("{broken json", encoding="utf-8")
        with self.assertRaises(RuntimeError):
            m.load_state()


if __name__ == "__main__":
    unittest.main()
