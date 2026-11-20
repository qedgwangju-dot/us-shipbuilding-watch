"""Single-owner AI remote-access watch: verified stages, semantic dedupe, durable Telegram receipts."""
from __future__ import annotations

import datetime as dt
import hashlib
import html
import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

import requests
from bs4 import BeautifulSoup

import ai_remote_access_watch as base
import ai_remote_access_watch_v4 as v4

STATE = Path("ai_remote_state.json")
OUT = Path("out")
OUT.mkdir(exist_ok=True)
READY = OUT / "ai_remote_send_ready.flag"
REPORT = OUT / "ai_remote_status.json"
RULE_API = "https://www.federalregister.gov/api/v1/documents/2026-23529.json"
RULE_NO = "2026-23529"
RULE_TITLE = "addressing remote access to advanced semiconductors"
MAX_NEWS_AGE_DAYS = 7
KST = dt.timezone(dt.timedelta(hours=9))
TRUSTED_ORIGINAL_HOSTS = {"www.federalregister.gov", "federalregister.gov",
                          "www.govinfo.gov", "govinfo.gov"}

# Preserve the existing seven-day discovery period, but do not turn each URL into a new event.
_original_fetch_news_rss = base.fetch_news_rss


def fetch_news_rss_fresh(source):
    from email.utils import parsedate_to_datetime
    items = _original_fetch_news_rss(source)
    now = dt.datetime.now(dt.timezone.utc)
    passed = {}
    for key, item in items.items():
        raw = str(item.get("rss_pub_date") or "")
        try:
            published = parsedate_to_datetime(raw)
            if published.tzinfo is None:
                published = published.replace(tzinfo=dt.timezone.utc)
            age = now - published.astimezone(dt.timezone.utc)
            fresh = dt.timedelta(days=-1) <= age <= dt.timedelta(days=MAX_NEWS_AGE_DAYS)
        except (TypeError, ValueError):
            fresh = False
        if fresh:
            passed[key] = item
        else:
            print("[AI STALE] RSS publication too old or unverified; no new event")
    return passed


base.fetch_news_rss = fetch_news_rss_fresh


def clock():
    return dt.datetime.now(dt.timezone.utc)


def load_state(path=STATE):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError("ai_remote_state.json unreadable: fail closed, never reset prior delivery") from exc
    if not isinstance(value, dict) or not isinstance(value.get("listings"), dict) or not isinstance(value.get("tracked"), dict):
        raise RuntimeError("AI remote state missing legacy listings/tracked; fail closed")
    if not isinstance(value.get("events", {}), dict):
        raise RuntimeError("AI remote state event history invalid; fail closed")
    return value


def write_state(value, path=STATE):
    tmp = path.with_name(path.name + ".pending-write")
    try:
        tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def read_public_url(url, timeout=15):
    u = urlsplit(str(url))
    if u.scheme != "https" or u.hostname not in TRUSTED_ORIGINAL_HOSTS or u.username or u.password:
        raise RuntimeError("Unapproved official source URL")
    try:
        response = requests.get(url, timeout=timeout, headers={
            "User-Agent": "AI-Remote-Access-Watch/2.0", "Accept": "application/json,text/plain,text/html"})
        response.raise_for_status()
        return response.text
    except requests.RequestException as exc:
        raise RuntimeError("Official first-party source unavailable; not a no-change decision") from None


def verify_rule_metadata(meta, now):
    if not isinstance(meta, dict) or str(meta.get("document_number") or "") != RULE_NO:
        raise RuntimeError("BIS Federal Register document identity mismatch")
    agency = " ".join(str(x.get("name") or "") for x in meta.get("agencies", []) if isinstance(x, dict)).lower()
    title = " ".join(str(meta.get("title") or "").lower().split())
    if RULE_TITLE not in title or "industry and security" not in agency:
        raise RuntimeError("BIS Federal Register title or agency mismatch")
    if str(meta.get("type") or "").lower() != "rule":
        raise RuntimeError("BIS document is not a published rule")
    publication = str(meta.get("publication_date") or "")[:10]
    try:
        date = dt.date.fromisoformat(publication)
    except ValueError as exc:
        raise RuntimeError("BIS publication date missing") from exc
    if date > now.astimezone(dt.timezone(dt.timedelta(hours=-5))).date():
        raise RuntimeError("BIS publication date not yet reached in U.S.")
    if date != dt.date(2026, 11, 19):
        raise RuntimeError("Unexpected BIS official publication date")
    source = str(meta.get("raw_text_url") or "")
    if not source:
        raise RuntimeError("BIS publication raw text missing")
    return date, source


def official_rule(now=None, getter=read_public_url):
    now = now or clock()
    meta = json.loads(getter(RULE_API))
    publication, source = verify_rule_metadata(meta, now)
    raw = getter(source)
    body = " ".join(BeautifulSoup(raw, "html.parser").get_text(" ", strip=True).lower().split())
    required = (RULE_TITLE, "country group d:5", "macau",
                "indonesia", "malaysia", "singapore", "thailand", "brazil")
    if not all(x in body for x in required):
        raise RuntimeError("BIS published legal text does not verify controlling scope")
    effective_raw = str(meta.get("effective_on") or meta.get("effective_date") or "")[:10]
    try:
        effective = dt.date.fromisoformat(effective_raw) if effective_raw else None
    except ValueError:
        effective = None
    us_today = now.astimezone(dt.timezone(dt.timedelta(hours=-5))).date()
    active = bool(effective and effective <= us_today)
    stage = "발효" if active else "관보 게재·효력일 추가 확인"
    key = "bis-remote-access:" + RULE_NO + (":effective:"+str(effective) if active else ":published")
    url = str(meta.get("html_url") or "https://www.federalregister.gov/d/" + RULE_NO)
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in TRUSTED_ORIGINAL_HOSTS:
        raise RuntimeError("BIS rule canonical link unverified")
    return {"key": key, "type": "official-rule", "stage": stage,
            "date": publication.isoformat(), "effective_date": effective.isoformat() if effective else "",
            "url": url, "document_no": RULE_NO,
            "details_verified": True}


def headline_for(rule):
    safe = html.escape(rule["url"], quote=True)
    stage = "발효 확인" if rule["stage"] == "발효" else "정식 관보 게재"
    extra = ("• 발효일: " + html.escape(rule["effective_date"]) + " (미국 현지 기준)\n"
             if rule["effective_date"] else "• 발효일: 공식 확정치 확인 필요\n")
    body = (
        "미국 AI 칩·원격접근 규제 중요 변화\n\n"
        "<b>미 BIS, GPU 원격접근 수출통제 잠정 최종규칙 " + stage + "</b>\n"
        "• 단계: BIS 공식 규칙 문서 2026-23529 (RIN 0694-AK50)\n"
        "• 관보 게재일: 2026-11-19 (미국 현지 기준)\n" + extra +
        "• 대상: D:5 국가·마카오 관련 기업의 원격 연산 및 제3국 첨단 AI 칩 수출 허가\n"
        "• 추가 허가 대상: 인도네시아·말레이시아·싱가포르·태국·브라질\n"
        "• 의미: 법률 시행·허가 조건과 과거 언론의 검토 보도를 구분\n"
        "• 한국 영향: 실제 GPU 주문·HBM 출하·고객 이전 여부를 별도 확인\n"
        f'• <a href="{safe}">BIS 연방관보 공식 원문</a>'
    )
    if len(body) > 4096:
        raise ValueError("Telegram message too long; do not truncate official conditions")
    return body


def bill_milestone(state, tracked, now):
    """Existing congressional alerts survive, but require a newly dated actual action."""
    for page in base.TRACKED_PAGES:
        if page["kind"] != "bill":
            continue
        name = page["name"]
        current = tracked.get(name)
        before = (state.get("tracked") or {}).get(name)
        if not current or not before or current.get("digest") == before.get("digest"):
            continue
        if v4._same_semantic_bill_status(str(before.get("snapshot") or ""), str(current.get("snapshot") or "")):
            continue
        snapshot = str(current.get("snapshot") or "")
        match = re.search(r"last_action=([A-Za-z]+ \d{1,2}, 20\d{2})", snapshot)
        if not match or "status_parse_unavailable" in snapshot:
            continue
        try:
            when = dt.datetime.strptime(match.group(1), "%B %d, %Y").date()
        except ValueError:
            continue
        age = (now.date() - when).days
        if age < 0 or age > 3:
            continue
        key = "ai-remote-bill:" + name + ":" + hashlib.sha256(snapshot.encode()).hexdigest()[:16]
        item = dict(current["item"])
        message = v4.build_message(item)
        if len(message) > 4096:
            raise RuntimeError("Legislative notice would exceed Telegram limit")
        return {"key":key, "type":"bill", "stage":"의회 신규 공식 행동",
                "date":when.isoformat(), "url":item["url"], "message":message}
    return None



def other_official_event(state, listings, now):
    """Keep monitoring new BIS official notices without replaying historic URLs."""
    names = ("BIS 공식 뉴스", "BIS 집행·가이던스", "미 연방관보 BIS")
    for name in names:
        entries = listings.get(name) or {}
        old_urls = set((state.get("listings") or {}).get(name) or [])
        for url, item in entries.items():
            if url in old_urls or str(url).find(RULE_NO) >= 0:
                continue
            parsed = urlsplit(url)
            if parsed.scheme != "https" or not (parsed.hostname or "").endswith(
                (".bis.gov", ".federalregister.gov")
            ):
                continue
            title = str(item.get("title") or "").strip()
            low = title.lower()
            if not any(term in low for term in ("remote access", "ai chip", "advanced computing",
                                               "cloud", "advanced semiconductor")):
                continue
            try:
                response = requests.get(url, timeout=12, headers={"User-Agent":"AI-Remote-Access-Watch/2.0"})
                response.raise_for_status()
                html_text = response.text
                plain = base.clean(html_text).lower()
                if not any(t in plain for t in ("remote access", "advanced computing", "ai chip")):
                    continue
                date_hit = re.search(
                    r'(?:datePublished|article:published_time|pubdate|datetime)[^\n<>]{0,140}?'
                    r'(20\d{2}-\d{2}-\d{2})',
                    html_text, re.I
                )
                if not date_hit:
                    continue
                published = dt.date.fromisoformat(date_hit.group(1))
                if not 0 <= (now.date() - published).days <= 3:
                    continue
                identity = hashlib.sha256(url.encode()).hexdigest()[:20]
                key = "ai-remote-other-official:" + identity
                if key in (state.get("events") or {}):
                    continue
                safe = html.escape(url, quote=True)
                text = (
                    "미국 AI 칩·원격접근 규제 중요 변화\n\n"
                    "<b>미국 BIS, 원격접근·첨단칩 관련 공식 후속자료 공개</b>\n"
                    "• 문서: " + html.escape(title) + "\n"
                    "• 단계: 정부 공식 발표 확인. 구체적 시행·계약 내용은 원문 기준\n"
                    "• 공개일: " + published.isoformat() + "\n"
                    "• 다음 확인: 변경된 허가 조건·유효일·실제 GPU 주문·HBM 출하\n"
                    f'• <a href="{safe}">공식 원문</a>'
                )
                if len(text) > 4096:
                    continue
                return {"key": key, "type": "official-followup", "stage":"공식 후속 발표",
                        "date":published.isoformat(), "url":url, "message":text}
            except (requests.RequestException, ValueError):
                print("ai_remote_official_followup_unverified=true")
                continue
    return None

def emit_status(**values):
    REPORT.write_text(json.dumps(values, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("ai_remote_status=" + json.dumps(values, ensure_ascii=False, separators=(",", ":")))


def prepare():
    READY.unlink(missing_ok=True)
    state = load_state()
    if state.get("_ai_remote_pending"):
        # A crash or uncertain Telegram response cannot be retried automatically.
        emit_status(status="held", reason="prior_delivery_unconfirmed", telegram_sent=False)
        return
    now = clock()
    try:
        rule = official_rule(now)
    except Exception as exc:
        emit_status(status="degraded", reason="official_rule_unverified",
                    error_type=type(exc).__name__, telegram_sent=False)
        raise RuntimeError("BIS published/effective status unavailable; refuse false no-change") from None
    listings, tracked = base.collect()
    event = None
    if rule["key"] not in state.get("events", {}):
        event = {**rule, "message": headline_for(rule)}
    else:
        event = other_official_event(state, listings, now) or bill_milestone(state, tracked, now)
        if event and event["key"] in state.get("events", {}):
            event = None
    # Historical press reprints and opinion columns are NOT separate rule stages.
    # Keep all prior URL history and legacy congressional snapshots untouched.
    if not event:
        emit_status(status="no_new_verified_stage", official_rule=rule["stage"],
                    press_reprints_suppressed=True, telegram_sent=False)
        return
    run_id = os.environ.get("GITHUB_RUN_ID")
    if not run_id:
        raise RuntimeError("Refuse live delivery without a GitHub Actions run ID")
    new_state = dict(state)
    new_state["_ai_remote_pending"] = {
        "key": event["key"], "type": event["type"], "stage": event["stage"],
        "source_url": event["url"], "message": event["message"],
        "run_id": run_id, "created_at_utc": now.isoformat(),
        "status": "prepared_before_send"
    }
    write_state(new_state)
    READY.write_text(run_id + "\n", encoding="utf-8")
    emit_status(status="prepared", stage=event["stage"],
                event_key=event["key"], telegram_sent=False)


def check_receipt(answer, destination):
    result = (answer or {}).get("result") or {}
    message_id = result.get("message_id")
    actual = result.get("chat") or {}
    if answer.get("ok") is not True or type(message_id) is not int or message_id <= 0:
        raise RuntimeError("Telegram response lacks valid message_id")
    if not match_chat(actual, destination):
        raise RuntimeError("Telegram delivered to unexpected recipient; hold state")
    return message_id


def match_chat(actual, requested):
    target = str(requested or "").strip()
    if not isinstance(actual, dict):
        return False
    if target.lstrip("-").isdigit():
        return str(actual.get("id") or "") == target
    if target.startswith("@"):
        return str(actual.get("username") or "").lower() == target[1:].lower()
    return False


def send():
    state = load_state()
    pending = state.get("_ai_remote_pending")
    if not isinstance(pending, dict):
        emit_status(status="no_pending_event", telegram_sent=False)
        return
    run_id = os.environ.get("GITHUB_RUN_ID")
    if pending.get("run_id") != run_id or READY.read_text(encoding="utf-8").strip() != run_id:
        raise RuntimeError("Pending event belongs to another run: duplicate-prevention hold")
    token = os.environ.get("AI_REMOTE_TELEGRAM_BOT_TOKEN", "").strip()
    destination = os.environ.get("AI_REMOTE_TELEGRAM_CHAT_ID", "").strip()
    if not token or not destination:
        raise RuntimeError("Existing Telegram route credentials unavailable: do not mark seen")
    api = "https://api.telegram.org/bot" + token
    try:
        identity = requests.get(api + "/getMe", timeout=15).json()
        if identity.get("ok") is not True or not (identity.get("result") or {}).get("username"):
            raise RuntimeError("Telegram bot identity unverified")
        recipient = requests.post(api + "/getChat", json={"chat_id":destination}, timeout=15).json()
        if recipient.get("ok") is not True or not match_chat(recipient.get("result") or {}, destination):
            raise RuntimeError("Telegram destination mismatch")
    except requests.RequestException:
        raise RuntimeError("Telegram read-only route verification failed") from None
    try:
        resp = requests.post(api + "/sendMessage", json={
            "chat_id": destination, "text": pending["message"],
            "parse_mode": "HTML", "disable_web_page_preview": True
        }, timeout=25)
        resp.raise_for_status()
        acknowledgment = resp.json()
        mid = check_receipt(acknowledgment, destination)
    except Exception:
        # Do not automatically retry on network timeout, 5xx or ambiguous response.
        raise RuntimeError("Telegram send unconfirmed: durable pending held; no automatic replay") from None
    completed = dict(state)
    events = dict(completed.get("events") or {})
    if pending["key"] in events:
        raise RuntimeError("Event already acknowledged: do not overwrite state")
    events[pending["key"]] = {"stage": pending["stage"], "source_url": pending["source_url"],
                              "telegram_message_id": mid, "sent_at_utc": clock().isoformat()}
    completed["events"] = events
    completed["last_successful_delivery"] = {
        "event_key":pending["key"], "telegram_message_id":mid,
        "recipient_match":True, "sent_at_utc":clock().isoformat()
    }
    del completed["_ai_remote_pending"]
    write_state(completed)
    emit_status(status="delivered_and_pending_commit", event_key=pending["key"],
                telegram_message_id=mid, recipient_match=True, telegram_sent=True)


def selftest():
    """Network-free coverage of source identity, migration, dedupe, and Telegram receipts."""
    import tempfile
    from unittest.mock import patch
    sample = {"listings": {"legacy": ["https://news.google.com/rss/articles/abc"]},
              "tracked": {"Remote Access Security Act S.3519": {"digest": "old"}}}
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "seen.json"
        try:
            load_state(path)
            raise AssertionError("Missing state must not become a fresh baseline")
        except RuntimeError:
            pass
        path.write_text("bad json", encoding="utf-8")
        try:
            load_state(path)
            raise AssertionError("Corrupt state must fail closed")
        except RuntimeError:
            pass
        write_state(sample, path)
        assert load_state(path) == sample
    fixed = dt.datetime(2026,11,21,0,0,tzinfo=dt.timezone.utc)
    metadata = {"document_number":RULE_NO,"type":"Rule","title":"Addressing Remote Access to Advanced Semiconductors",
                "agencies":[{"name":"Industry and Security Bureau"}], "publication_date":"2026-11-19",
                "effective_on":"2026-11-19",
                "raw_text_url":"https://www.federalregister.gov/test-rule.txt",
                "html_url":"https://www.federalregister.gov/d/"+RULE_NO}
    body = "Addressing Remote Access to Advanced Semiconductors. Country Group D:5 Macau Indonesia Malaysia Singapore Thailand Brazil."
    def fake_get(url):
        return json.dumps(metadata) if url==RULE_API else body
    event = official_rule(fixed, getter=fake_get)
    assert event["stage"]=="발효" and ":effective:" in event["key"]
    assert '<a href="https://www.federalregister.gov/d/' in headline_for(event)
    assert len(headline_for(event))<=4096
    assert not match_chat({"id":"-1001"},"-1002")
    assert match_chat({"id":-1001},"-1001")
    assert check_receipt({"ok":True,"result":{"message_id":576,"chat":{"id":-1001}}},"-1001")==576
    for response in ({"ok":True,"result":{"message_id":True,"chat":{"id":-1001}}},
                     {"ok":True,"result":{"message_id":10,"chat":{"id":-1002}}},
                     {"ok":False,"result":{"message_id":10,"chat":{"id":-1001}}}):
        try:
            check_receipt(response,"-1001")
            raise AssertionError("Invalid Telegram acknowledgement accepted")
        except RuntimeError:
            pass
    assert not v4._same_semantic_bill_status("bill=H.R. 2683 | action=Passed House",
                                               "bill=H.R. 2683 | action=Passed Senate")
    assert v4._same_semantic_bill_status("bill=S.3519 | action=Referred to committee",
                                           "bill=S.3519 | action=Referred to committee")
    for invalid in ({"document_number":RULE_NO}, {**metadata,"type":"Proposed Rule"},
                    {**metadata,"document_number":"2026-00000"}):
        try:
            verify_rule_metadata(invalid,fixed)
            raise AssertionError("Invalid BIS legal document accepted")
        except RuntimeError:
            pass
    print("ai_remote_scoped_regressions=passed no_telegram=true")


def source_probe():
    """Live read-only official verification in GitHub's runner; no state or Telegram side effect."""
    result = official_rule()
    print("ai_remote_bis_official_probe=verified document=" + result["document_no"] +
          " stage=" + result["stage"] + " source=FederalRegister no_telegram=true")


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv)>1 else "prepare"
    if cmd == "prepare":
        prepare()
    elif cmd == "send":
        send()
    elif cmd == "selftest":
        selftest()
    elif cmd == "probe":
        source_probe()
    else:
        raise SystemExit("Unknown AI remote watch action")
