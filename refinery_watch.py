#!/usr/bin/env python3
import datetime as dt
import hashlib
import html
import json
import os
import pathlib
import re
import urllib.parse
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo
import xml.etree.ElementTree as ET

import requests

KST = ZoneInfo("Asia/Seoul")
STATE_PATH = pathlib.Path("refinery_watch_state.json")
EXPECTED_BOT = "khs88798879_bot"

QUERIES = [
    'Trump refinery executives September 1 gasoline refinery capacity RFS',
    'Trump refiners gasoline diesel refinery capacity',
    'RFS small refinery exemption RIN Trump EPA',
    'Marathon Petroleum Valero Chevron PBF Delek Trump refinery meeting',
    'site:whitehouse.gov Trump refinery gasoline RFS',
    'site:epa.gov RFS small refinery exemption refinery gasoline',
]

STRONG_TERMS = (
    "trump", "refinery", "refiner", "refiners", "gasoline", "diesel",
    "renewable fuel standard", "rfs", "small refinery exemption", "rin",
    "marathon petroleum", "valero", "chevron", "pbf", "delek",
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; khs-refinery-watch/1.0; +https://github.com/qedgwangju-dot/us-shipbuilding-watch)"
}


def now_kst():
    return dt.datetime.now(KST)


def load_state():
    if not STATE_PATH.exists():
        return {"initialized": False, "seen": [], "last_run_kst": None}
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        data.setdefault("initialized", False)
        data.setdefault("seen", [])
        return data
    except Exception:
        return {"initialized": False, "seen": [], "last_run_kst": None}


def save_state(state):
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def tg_api(token, method, payload=None):
    url = f"https://api.telegram.org/bot{token}/{method}"
    r = requests.post(url, data=payload or {}, timeout=25) if payload is not None else requests.get(url, timeout=25)
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram API error: {data}")
    return data


def validate_telegram(token):
    data = tg_api(token, "getMe")
    username = str((data.get("result") or {}).get("username") or "").lstrip("@")
    if username.lower() != EXPECTED_BOT.lower():
        raise RuntimeError(f"Wrong Telegram bot: expected @{EXPECTED_BOT}, got @{username or 'unknown'}")
    return username


def send_telegram(token, chat_id, text):
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": "true",
    }
    return tg_api(token, "sendMessage", payload)


def google_news_rss(query):
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode({
        "q": query,
        "hl": "en-US",
        "gl": "US",
        "ceid": "US:en",
    })
    r = requests.get(url, headers=HEADERS, timeout=25)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    out = []
    for item in root.findall("./channel/item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub_raw = (item.findtext("pubDate") or "").strip()
        source_el = item.find("source")
        source = (source_el.text or "").strip() if source_el is not None else ""
        try:
            pub = parsedate_to_datetime(pub_raw)
            if pub.tzinfo is None:
                pub = pub.replace(tzinfo=dt.timezone.utc)
            pub_kst = pub.astimezone(KST)
        except Exception:
            pub_kst = now_kst()
        key = hashlib.sha256((title + "|" + link).encode("utf-8")).hexdigest()[:24]
        out.append({
            "id": key,
            "title": title,
            "link": link,
            "source": source,
            "pub_kst": pub_kst.isoformat(timespec="minutes"),
        })
    return out


def relevant(item):
    text = f"{item['title']} {item['source']}".lower()
    hits = sum(1 for term in STRONG_TERMS if term in text)
    # Require at least two topic signals, or one signal plus a key official/business source.
    key_source = any(x in text for x in ("white house", "epa", "reuters", "bloomberg", "marathon petroleum", "valero", "chevron", "pbf", "delek"))
    return hits >= 2 or (hits >= 1 and key_source)


def classify(title):
    t = title.lower()
    labels = []
    if any(x in t for x in ("meeting", "executive", "white house", "trump")):
        labels.append("회동·정책")
    if any(x in t for x in ("rfs", "renewable fuel", "small refinery", "rin")):
        labels.append("RFS·RIN")
    if any(x in t for x in ("gasoline", "diesel", "fuel price", "pump price")):
        labels.append("유가·소매가격")
    if any(x in t for x in ("capacity", "utilization", "shutdown", "outage", "maintenance", "refinery")):
        labels.append("정제능력")
    if any(x in t for x in ("marathon", "valero", "chevron", "pbf", "delek")):
        labels.append("관련기업")
    return " · ".join(dict.fromkeys(labels)) or "정유업계"


def collect_items():
    merged = {}
    errors = []
    for query in QUERIES:
        try:
            for item in google_news_rss(query):
                if relevant(item):
                    merged[item["id"]] = item
        except Exception as exc:
            errors.append(f"{query}: {exc}")
    items = list(merged.values())
    items.sort(key=lambda x: x["pub_kst"], reverse=True)
    return items, errors


def format_alert(items):
    stamp = now_kst().strftime("%Y-%m-%d %H:%M KST")
    lines = [
        "[트럼프 정유업계 고유가 대응 웹감시]",
        f"새로운 변화 {len(items)}건 | 조회 {stamp}",
        "",
    ]
    for idx, item in enumerate(items[:6], 1):
        lines.extend([
            f"{idx}. [{classify(item['title'])}] {item['title']}",
            f"출처: {item['source'] or '확인 필요'} | 발표: {item['pub_kst'].replace('T', ' ')}",
            item["link"],
            "",
        ])
    lines.extend([
        "감시 포인트: 9월 1일 회동의 공식 확정·개최 결과, 휘발유·경유 가격 대책, 정제능력 확대·허가 완화, RFS·소형 정유사 면제·RIN 변화, Marathon Petroleum·Valero·Chevron·PBF·Delek의 구체적 설비투자 또는 정책 수혜 여부.",
        "새로운 변화가 없으면 알림하지 않습니다.",
    ])
    return "\n".join(lines).strip()


def main():
    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()
    if not token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID secret is missing")

    bot_username = validate_telegram(token)
    state = load_state()
    items, errors = collect_items()
    seen = set(state.get("seen") or [])

    # First run: establish a baseline without replaying old headlines, then prove the exact Telegram route once.
    if not state.get("initialized"):
        state["initialized"] = True
        state["seen"] = [x["id"] for x in items[:300]]
        state["last_run_kst"] = now_kst().isoformat(timespec="seconds")
        save_state(state)
        setup = (
            "[트럼프 정유업계 고유가 대응 웹감시 시작]\n"
            f"발송 봇: @{bot_username}\n"
            "감시 주기: 약 10분\n"
            "대상: 9월 1일 정유업계 회동, 휘발유·경유 가격 대책, 정제능력 확대, RFS·SRE·RIN, "
            "Marathon Petroleum·Valero·Chevron·PBF·Delek 관련 정책·설비 변화\n"
            "기존 기사 재전송 없이 지금부터 새 변화가 생길 때만 알립니다."
        )
        result = send_telegram(token, chat_id, setup)
        print(f"initialized=true bot=@{bot_username} message_id={(result.get('result') or {}).get('message_id')}")
        if errors:
            print("source_errors=" + " | ".join(errors))
        return

    new_items = [x for x in items if x["id"] not in seen]
    # Keep only reasonably fresh newly discovered items to avoid stale index churn.
    cutoff = now_kst() - dt.timedelta(days=3)
    fresh = []
    for item in new_items:
        try:
            pub = dt.datetime.fromisoformat(item["pub_kst"])
            if pub >= cutoff:
                fresh.append(item)
        except Exception:
            fresh.append(item)

    if fresh:
        text = format_alert(fresh)
        result = send_telegram(token, chat_id, text)
        print(f"alert_sent=true bot=@{bot_username} message_id={(result.get('result') or {}).get('message_id')} new={len(fresh)}")
    else:
        print(f"no_meaningful_change=true bot=@{bot_username}")

    state["seen"] = list(dict.fromkeys([x["id"] for x in items[:300]] + list(seen)))[:600]
    state["last_run_kst"] = now_kst().isoformat(timespec="seconds")
    state["last_source_error_count"] = len(errors)
    save_state(state)
    if errors:
        print("source_errors=" + " | ".join(errors))


if __name__ == "__main__":
    main()
