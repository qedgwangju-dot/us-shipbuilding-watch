#!/usr/bin/env python3
import datetime as dt
import hashlib
import html
import json
import os
import pathlib
import re
import urllib.parse
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from deep_translator import GoogleTranslator
from googlenewsdecoder import gnewsdecoder

KST = ZoneInfo("Asia/Seoul")
STATE_PATH = pathlib.Path("semiconductor_consumption_state.json")
EXPECTED_BOT = "khs887900_bot"
BOK_LIST_URL = "https://www.bok.or.kr/portal/bbs/P0002353/list.do?menuNo=200433"

QUERIES = [
    '"반도체 수혜지역" 소비',
    '반도체 성과급 소비 이천 화성 청주',
    '반도체 호황 소비 자동차 백화점 테슬라',
    '이천 화성 청주 테슬라 인도량 반도체',
    '삼성전자 SK하이닉스 성과급 소비 지역',
    '반도체 수혜지역 백화점 자동차 온라인 소비',
    '반도체 수혜지역 생활소비 서비스 소비',
    '반도체 수혜지역 주택 소비',
    'site:bok.or.kr 반도체 소비 성과급',
    'site:kaida.co.kr 테슬라 신규등록 지역',
]

SEMICON_TERMS = (
    "반도체", "삼성전자", "sk하이닉스", "sk hynix", "semiconductor",
)
CONSUMPTION_TERMS = (
    "소비", "성과급", "자동차", "백화점", "가전", "가구", "온라인",
    "테슬라", "tesla", "인도량", "신규등록", "등록대수", "내수",
    "서비스", "생활소비", "주택", "부동산", "이천", "화성", "청주",
    "평택", "기흥",
)
HIGH_VALUE_TERMS = ("자동차", "백화점", "가전", "가구", "테슬라", "tesla", "고가", "재량")
BROADENING_TERMS = ("생활소비", "서비스", "외식", "의료", "교육", "편의점", "대형마트")
HOUSING_TERMS = ("주택", "부동산", "아파트", "매매")
REGIONAL_TERMS = ("이천", "화성", "청주", "평택", "기흥", "반도체 수혜지역", "반도체 고노출")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
    )
}

ERROR_MARKERS = (
    "error 500", "server error", "internal server error", "service unavailable",
    "temporarily unavailable", "access denied", "request blocked", "captcha",
    "cloudflare", "enable javascript", "오류 500", "서버 오류", "접근이 거부",
)


def now_kst():
    return dt.datetime.now(KST)


def load_state():
    if not STATE_PATH.exists():
        return {"initialized": False, "seen": [], "connected_notified": False, "last_run_kst": None}
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        data.setdefault("initialized", False)
        data.setdefault("seen", [])
        data.setdefault("connected_notified", False)
        data.setdefault("last_run_kst", None)
        return data
    except Exception:
        return {"initialized": False, "seen": [], "connected_notified": False, "last_run_kst": None}


def save_state(state):
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def tg_api(token, method, payload=None):
    url = f"https://api.telegram.org/bot{token}/{method}"
    if payload is None:
        r = requests.get(url, timeout=25)
    else:
        r = requests.post(url, data=payload, timeout=25)
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram API error: {data}")
    return data


def validate_bot(token):
    data = tg_api(token, "getMe")
    username = str((data.get("result") or {}).get("username") or "").lstrip("@")
    if username.lower() != EXPECTED_BOT.lower():
        raise RuntimeError(f"Wrong Telegram bot: expected @{EXPECTED_BOT}, got @{username or 'unknown'}")
    return username


def resolve_chat_id(token, configured=""):
    configured = str(configured or "").strip()
    if configured:
        return configured
    data = tg_api(token, "getUpdates")
    updates = data.get("result") or []
    for update in reversed(updates):
        msg = update.get("message") or update.get("edited_message") or {}
        chat = msg.get("chat") or {}
        if chat.get("id") is not None and chat.get("type") == "private":
            return str(chat["id"])
    raise RuntimeError(
        f"@{EXPECTED_BOT}에서 먼저 /start 를 보낸 뒤 다시 실행하세요. "
        "KHS887900_CHAT_ID secret을 직접 넣어도 됩니다."
    )


def split_message(text, limit=3700):
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
            chunks.append(paragraph[:limit])
            paragraph = paragraph[limit:]
        current = paragraph
    if current:
        chunks.append(current)
    return chunks


def send_telegram(token, chat_id, text):
    for chunk in split_message(text):
        tg_api(token, "sendMessage", {
            "chat_id": chat_id,
            "text": chunk,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        })


def clean(text):
    return " ".join((text or "").replace("\xa0", " ").split()).strip()


def is_error_text(text):
    low = clean(text).lower()
    return any(marker in low for marker in ERROR_MARKERS)


def google_news_rss(query):
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode({
        "q": query,
        "hl": "ko",
        "gl": "KR",
        "ceid": "KR:ko",
    })
    r = requests.get(url, headers=HEADERS, timeout=25)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    out = []
    for node in root.findall("./channel/item"):
        title = clean(node.findtext("title"))
        link = clean(node.findtext("link"))
        pub_raw = clean(node.findtext("pubDate"))
        source_el = node.find("source")
        source = clean(source_el.text) if source_el is not None else ""
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
            "kind": "news",
        })
    return out


def relevant_text(text):
    low = clean(text).lower()
    semicon = any(t.lower() in low for t in SEMICON_TERMS)
    consumption = any(t.lower() in low for t in CONSUMPTION_TERMS)
    regional = any(t.lower() in low for t in REGIONAL_TERMS)
    # 지역·소비 데이터는 반도체 단어가 제목에 빠지는 경우가 있어 지역+소비 조합도 허용
    return (semicon and consumption) or (regional and consumption)


def collect_bok_notes():
    out = []
    r = requests.get(BOK_LIST_URL, headers=HEADERS, timeout=25)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    seen_urls = set()
    for a in soup.select("a[href]"):
        title = clean(a.get_text(" ", strip=True))
        href = clean(a.get("href"))
        if not title or "P0002353" not in href or "view.do" not in href:
            continue
        if not relevant_text(title):
            continue
        url = urllib.parse.urljoin(BOK_LIST_URL, href)
        if url in seen_urls:
            continue
        seen_urls.add(url)
        key = hashlib.sha256((title + "|" + url).encode("utf-8")).hexdigest()[:24]
        out.append({
            "id": key,
            "title": title,
            "link": url,
            "source": "한국은행",
            "pub_kst": now_kst().isoformat(timespec="minutes"),
            "kind": "bok",
        })
    return out


def collect_items():
    merged = {}
    errors = []
    try:
        for item in collect_bok_notes():
            merged[item["id"]] = item
    except Exception as exc:
        errors.append(f"한국은행: {exc}")
    for query in QUERIES:
        try:
            for item in google_news_rss(query):
                if relevant_text(f"{item['title']} {item['source']}"):
                    merged[item["id"]] = item
        except Exception as exc:
            errors.append(f"{query}: {exc}")
    items = list(merged.values())
    items.sort(key=lambda x: x["pub_kst"], reverse=True)
    return items, errors


def publisher_url(item):
    if item.get("kind") == "bok":
        return item["link"]
    try:
        decoded = gnewsdecoder(item["link"], interval=0.2)
        if isinstance(decoded, dict) and decoded.get("status"):
            url = clean(decoded.get("decoded_url"))
            if url.startswith("http") and "news.google.com" not in urllib.parse.urlparse(url).netloc:
                return url
    except Exception:
        pass
    return ""


def article_sentences(url):
    r = requests.get(url, headers=HEADERS, timeout=30, allow_redirects=True)
    r.raise_for_status()
    if is_error_text(r.text[:5000]):
        raise RuntimeError("오류 페이지 감지")
    soup = BeautifulSoup(r.text, "html.parser")
    for tag in soup(["script", "style", "noscript", "nav", "header", "footer", "aside", "form", "svg"]):
        tag.decompose()
    paragraphs = []
    for selector in ("article p", "[class*='article'] p", "[class*='content'] p", "main p", "p"):
        for p in soup.select(selector):
            text = clean(p.get_text(" ", strip=True))
            if len(text) < 45 or is_error_text(text):
                continue
            if text not in paragraphs:
                paragraphs.append(text)
        if len(paragraphs) >= 8:
            break
    if not paragraphs:
        raise RuntimeError("본문 추출 실패")
    sentences = []
    for p in paragraphs[:12]:
        for s in re.split(r"(?<=[.!?。])\s+|(?<=다\.)\s+", p):
            s = clean(s)
            if len(s) >= 35 and relevant_text(s):
                sentences.append(s)
    if not sentences:
        sentences = paragraphs[:3]
    return sentences[:4]


def to_korean(text):
    text = clean(text)
    if not text:
        return text
    if re.search(r"[가-힣]", text):
        return text
    try:
        translated = GoogleTranslator(source="auto", target="ko").translate(text)
        translated = clean(translated)
        if translated and re.search(r"[가-힣]", translated) and not is_error_text(translated):
            return translated
    except Exception:
        pass
    return text


def extract_numbers(text):
    text = clean(text)
    patterns = [
        r"[+-]?\d+(?:\.\d+)?\s*%p",
        r"[+-]?\d+(?:\.\d+)?\s*%",
        r"\d+(?:\.\d+)?\s*배",
        r"\d+(?:\.\d+)?\s*조원",
        r"\d[\d,]*(?:\.\d+)?\s*억원",
        r"\d[\d,]*\s*대",
        r"\d{4}년\s*\d{1,2}월",
    ]
    found = []
    for pattern in patterns:
        for m in re.findall(pattern, text):
            value = clean(m)
            if value not in found:
                found.append(value)
    return found[:8]


def classify(text):
    low = clean(text).lower()
    labels = []
    if any(t.lower() in low for t in HIGH_VALUE_TERMS):
        labels.append("고가 재량소비")
    if any(t.lower() in low for t in BROADENING_TERMS):
        labels.append("생활·서비스 확산")
    if "테슬라" in low or "tesla" in low or "인도량" in low or "신규등록" in low:
        labels.append("Tesla·자동차")
    if "성과급" in low or "임금" in low:
        labels.append("성과급·소득")
    if any(t.lower() in low for t in HOUSING_TERMS):
        labels.append("주택·자산흡수")
    if any(t.lower() in low for t in REGIONAL_TERMS):
        labels.append("반도체 수혜지역")
    return " · ".join(dict.fromkeys(labels)) or "반도체 소비파급"


def build_alert(item, url, sentences):
    title_ko = to_korean(item["title"])
    sentence_ko = [to_korean(s) for s in sentences]
    joined = " ".join([title_ko] + sentence_ko)
    nums = extract_numbers(joined)
    label = classify(joined)
    source = html.escape(item.get("source") or urllib.parse.urlparse(url).netloc)
    pub = item.get("pub_kst") or ""
    try:
        pub_text = dt.datetime.fromisoformat(pub).astimezone(KST).strftime("%Y-%m-%d %H:%M KST")
    except Exception:
        pub_text = pub
    bullets = []
    for s in sentence_ko[:3]:
        s = clean(s)
        if len(s) > 330:
            s = s[:327] + "…"
        bullets.append("• " + html.escape(s))
    number_line = " · ".join(nums) if nums else "기사에서 즉시 추출 가능한 핵심 수치 없음"
    return (
        "📊 <b>[반도체 수혜지역 소비 웹감시]</b>\n"
        f"<b>{html.escape(title_ko)}</b>\n\n"
        f"• 출처: {source}\n"
        f"• 시각: {html.escape(pub_text)}\n"
        f"• 분류: {html.escape(label)}\n"
        f"• 핵심 수치: {html.escape(number_line)}\n\n"
        + "\n".join(bullets)
        + f"\n\n• 투자축: 돈 버는 능력·시간표 변화 우선 확인\n"
        + f"• 판정 기준: 고가 소비 편중 → 생활·서비스 소비 확산 여부 추적\n"
        + f"<a href=\"{html.escape(url, quote=True)}\">원문</a>"
    )


def connection_message(baseline_count):
    return (
        "✅ <b>[반도체 수혜지역 소비 웹감시 연결 완료]</b>\n\n"
        f"• Telegram: @{EXPECTED_BOT}\n"
        "• 감시: 한국은행 BOK 이슈노트 + 관련 최신 보도\n"
        "• 핵심: 반도체 성과급 → 지역 소비 → 자동차·백화점·가전·온라인 → 생활·서비스 확산\n"
        "• 별도 추적: Tesla 지역 인도·등록, 주택·부동산으로의 자금 흡수\n"
        "• 신규 자료만 전송, 기존 자료는 기준선으로 저장\n"
        f"• 기준선 저장 건수: {baseline_count}건"
    )


def main():
    token = clean(os.getenv("KHS887900_BOT_TOKEN"))
    configured_chat_id = clean(os.getenv("KHS887900_CHAT_ID"))
    if not token:
        raise RuntimeError("KHS887900_BOT_TOKEN secret이 없습니다")

    validate_bot(token)
    chat_id = resolve_chat_id(token, configured_chat_id)
    state = load_state()
    items, errors = collect_items()
    seen = set(state.get("seen") or [])

    if not state.get("initialized"):
        state["initialized"] = True
        state["seen"] = [item["id"] for item in items[:500]]
        if not state.get("connected_notified"):
            send_telegram(token, chat_id, connection_message(len(items)))
            state["connected_notified"] = True
        state["last_run_kst"] = now_kst().isoformat(timespec="seconds")
        state["last_errors"] = errors[:10]
        save_state(state)
        return

    new_items = [item for item in items if item["id"] not in seen]
    sent_ids = []
    for item in reversed(new_items[:12]):
        try:
            url = publisher_url(item)
            if not url:
                continue
            sentences = article_sentences(url)
            send_telegram(token, chat_id, build_alert(item, url, sentences))
            sent_ids.append(item["id"])
        except Exception:
            # 본문·원문 검증 실패 시 오보 방지를 위해 전송하지 않음
            continue

    all_seen = list(dict.fromkeys((state.get("seen") or []) + [i["id"] for i in items]))[-1000:]
    state["seen"] = all_seen
    state["last_run_kst"] = now_kst().isoformat(timespec="seconds")
    state["last_new_count"] = len(new_items)
    state["last_sent_count"] = len(sent_ids)
    state["last_errors"] = errors[:10]
    save_state(state)


if __name__ == "__main__":
    main()
