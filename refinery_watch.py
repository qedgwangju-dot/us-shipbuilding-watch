#!/usr/bin/env python3
import datetime as dt
import hashlib
import json
import os
import pathlib
import urllib.parse
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup
from deep_translator import GoogleTranslator
from googlenewsdecoder import gnewsdecoder

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
    "User-Agent": "Mozilla/5.0 (compatible; khs-refinery-watch/1.1; +https://github.com/qedgwangju-dot/us-shipbuilding-watch)"
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


def split_message(text, limit=3800):
    chunks, current = [], ""
    for paragraph in text.split("\n\n"):
        candidate = paragraph if not current else current + "\n\n" + paragraph
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
            current = ""
        while len(paragraph) > limit:
            cut = paragraph.rfind("\n", 0, limit)
            if cut < 1:
                cut = limit
            chunks.append(paragraph[:cut])
            paragraph = paragraph[cut:].lstrip()
        current = paragraph
    if current:
        chunks.append(current)
    return chunks


def send_telegram(token, chat_id, text):
    results = []
    for chunk in split_message(text):
        payload = {
            "chat_id": chat_id,
            "text": chunk,
            "disable_web_page_preview": "true",
        }
        results.append(tg_api(token, "sendMessage", payload))
    return results


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
    for node in root.findall("./channel/item"):
        title = (node.findtext("title") or "").strip()
        link = (node.findtext("link") or "").strip()
        pub_raw = (node.findtext("pubDate") or "").strip()
        source_el = node.find("source")
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
    key_source = any(x in text for x in (
        "white house", "epa", "reuters", "bloomberg", "marathon petroleum",
        "valero", "chevron", "pbf", "delek",
    ))
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


def decode_article_url(news_url):
    try:
        decoded = gnewsdecoder(news_url, interval=0.2)
        if isinstance(decoded, dict) and decoded.get("status") and decoded.get("decoded_url"):
            return decoded["decoded_url"]
    except Exception:
        pass
    return news_url


def extract_article_body(url):
    r = requests.get(url, headers=HEADERS, timeout=30, allow_redirects=True)
    r.raise_for_status()
    final_url = r.url
    soup = BeautifulSoup(r.text, "html.parser")
    for tag in soup(["script", "style", "noscript", "nav", "header", "footer", "aside", "form", "svg"]):
        tag.decompose()

    containers = []
    article = soup.find("article")
    if article:
        containers.append(article)
    main = soup.find("main")
    if main and main not in containers:
        containers.append(main)
    containers.append(soup)

    paragraphs = []
    seen = set()
    for container in containers:
        for p in container.find_all("p"):
            text = " ".join(p.get_text(" ", strip=True).split())
            if len(text) < 55:
                continue
            low = text.lower()
            if any(x in low for x in (
                "sign up", "subscribe", "cookie", "privacy policy", "all rights reserved",
                "advertisement", "read more", "reporting by", "editing by",
            )):
                continue
            if text in seen:
                continue
            seen.add(text)
            paragraphs.append(text)
        if len(paragraphs) >= 5:
            break

    # Copyright-safe alerting: use a limited set of representative body paragraphs,
    # translated into Korean, and always preserve the original source link.
    selected = []
    total = 0
    for p in paragraphs:
        if total >= 2400 or len(selected) >= 7:
            break
        piece = p[:520]
        selected.append(piece)
        total += len(piece)
    return final_url, selected


def has_hangul(text):
    return any("가" <= ch <= "힣" for ch in text)


def translate_ko(text):
    text = (text or "").strip()
    if not text:
        return ""
    if has_hangul(text) and sum(1 for ch in text if "가" <= ch <= "힣") >= max(4, len(text) // 8):
        return text
    translator = GoogleTranslator(source="auto", target="ko")
    return translator.translate(text=text)


def enrich_korean(item):
    original_url = decode_article_url(item["link"])
    final_url, paragraphs = extract_article_body(original_url)
    if not paragraphs:
        raise RuntimeError("기사 본문을 추출하지 못함")

    title_ko = translate_ko(item["title"])
    body_ko = []
    for paragraph in paragraphs:
        translated = translate_ko(paragraph)
        if translated:
            body_ko.append(translated)
    if not title_ko or not body_ko:
        raise RuntimeError("한국어 번역 결과가 비어 있음")

    enriched = dict(item)
    enriched["title_ko"] = title_ko
    enriched["body_ko"] = body_ko
    enriched["original_url"] = final_url
    return enriched


def format_article_alert(item):
    lines = [
        "[트럼프 정유업계 고유가 대응 웹감시]",
        f"[{classify(item['title'])}] {item['title_ko']}",
        f"출처: {item['source'] or '확인 필요'} | 발표: {item['pub_kst'].replace('T', ' ')}",
        "",
        "본문 한국어 번역",
    ]
    for paragraph in item["body_ko"]:
        lines.append(paragraph)
        lines.append("")
    lines.extend([
        "원문 링크",
        item["original_url"],
        "",
        "감시 포인트: 9월 1일 회동의 공식 확정·개최 결과, 휘발유·경유 가격 대책, 정제능력 확대·허가 완화, RFS·소형 정유사 면제·RIN 변화, Marathon Petroleum·Valero·Chevron·PBF·Delek의 구체적 설비투자 또는 정책 수혜 여부.",
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

    if not state.get("initialized"):
        state["initialized"] = True
        state["seen"] = [x["id"] for x in items[:300]]
        state["last_run_kst"] = now_kst().isoformat(timespec="seconds")
        save_state(state)
        setup = (
            "[트럼프 정유업계 고유가 대응 웹감시 시작]\n"
            f"발송 봇: @{bot_username}\n"
            "감시 주기: 약 10분\n"
            "영어 기사 처리: 제목과 본문을 한국어로 변환한 뒤 송출하며 영어 원문 본문은 Telegram에 그대로 내보내지 않습니다.\n"
            "대상: 9월 1일 정유업계 회동, 휘발유·경유 가격 대책, 정제능력 확대, RFS·SRE·RIN, Marathon Petroleum·Valero·Chevron·PBF·Delek 관련 정책·설비 변화"
        )
        results = send_telegram(token, chat_id, setup)
        ids = [(x.get("result") or {}).get("message_id") for x in results]
        print(f"initialized=true bot=@{bot_username} message_ids={ids}")
        if errors:
            print("source_errors=" + " | ".join(errors))
        return

    new_items = [x for x in items if x["id"] not in seen]
    cutoff = now_kst() - dt.timedelta(days=3)
    fresh = []
    for item in new_items:
        try:
            pub = dt.datetime.fromisoformat(item["pub_kst"])
            if pub >= cutoff:
                fresh.append(item)
        except Exception:
            fresh.append(item)

    delivered_ids = []
    translation_errors = []
    for item in fresh[:6]:
        try:
            enriched = enrich_korean(item)
            text = format_article_alert(enriched)
            results = send_telegram(token, chat_id, text)
            ids = [(x.get("result") or {}).get("message_id") for x in results]
            delivered_ids.append(item["id"])
            print(f"alert_sent=true bot=@{bot_username} message_ids={ids} article_id={item['id']}")
        except Exception as exc:
            # Never fall back to English. Leave the item unseen so a later run retries translation.
            translation_errors.append(f"{item['title']}: {exc}")
            print(f"translation_delivery_skipped=true article_id={item['id']} error={exc}")

    if not fresh:
        print(f"no_meaningful_change=true bot=@{bot_username}")

    state["seen"] = list(dict.fromkeys(delivered_ids + list(seen)))[:600]
    state["last_run_kst"] = now_kst().isoformat(timespec="seconds")
    state["last_source_error_count"] = len(errors)
    state["last_translation_error_count"] = len(translation_errors)
    save_state(state)

    if errors:
        print("source_errors=" + " | ".join(errors))
    if translation_errors:
        print("translation_errors=" + " | ".join(translation_errors))


if __name__ == "__main__":
    main()
