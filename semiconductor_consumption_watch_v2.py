#!/usr/bin/env python3
import datetime as dt
import difflib
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
SCHEMA_VERSION = 2
BOK_LIST_URL = "https://www.bok.or.kr/portal/bbs/P0002353/list.do?menuNo=200433"
MAX_ALERTS_PER_RUN = 6
MAX_ITEM_AGE_HOURS = 72

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

SEMICON_TERMS = ("반도체", "삼성전자", "sk하이닉스", "sk hynix", "semiconductor")
CONSUMPTION_TERMS = (
    "소비", "성과급", "자동차", "백화점", "가전", "가구", "온라인", "테슬라", "tesla",
    "인도량", "신규등록", "등록대수", "내수", "서비스", "생활소비", "주택", "부동산",
    "이천", "화성", "청주", "평택", "기흥",
)
REGIONAL_TERMS = ("이천", "화성", "청주", "평택", "기흥", "반도체 수혜지역", "반도체 고노출")
HIGH_VALUE_TERMS = ("자동차", "백화점", "가전", "가구", "테슬라", "tesla", "고가", "재량")
BROADENING_TERMS = ("생활소비", "서비스", "외식", "의료", "교육", "편의점", "대형마트")
HOUSING_TERMS = ("주택", "부동산", "아파트", "매매")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
    )
}

ERROR_MARKERS = (
    "error 500", "server error", "internal server error", "service unavailable", "temporarily unavailable",
    "access denied", "request blocked", "captcha", "cloudflare", "enable javascript", "오류 500", "서버 오류",
)


def now_kst():
    return dt.datetime.now(KST)


def clean(text):
    return " ".join((text or "").replace("\xa0", " ").split()).strip()


def normalize_source(source):
    return re.sub(r"[^0-9a-z가-힣]+", "", clean(source).lower())


def normalize_title(title, source=""):
    t = clean(html.unescape(title)).lower()
    src = clean(source).lower()
    if src:
        # Google News가 제목 끝에 붙이는 '- 매체명'을 제거해 링크가 바뀌어도 같은 기사로 인식한다.
        t = re.sub(r"\s[-–—|]\s*" + re.escape(src) + r"\s*$", "", t, flags=re.I)
    t = re.sub(r"\[[^\]]{0,40}\]", " ", t)
    t = re.sub(r"\([^)]{0,30}(?:속보|단독|종합|업데이트)[^)]*\)", " ", t)
    t = re.sub(r"[^0-9a-z가-힣]+", " ", t)
    return " ".join(t.split())


def stable_key(item):
    base = normalize_title(item.get("title", ""), item.get("source", ""))
    source = normalize_source(item.get("source", ""))
    if item.get("kind") == "bok":
        source = "한국은행"
    return hashlib.sha256(f"{base}|{source}".encode("utf-8")).hexdigest()[:32]


def canonical_url(url):
    try:
        p = urllib.parse.urlsplit(url)
        host = (p.hostname or "").lower()
        path = re.sub(r"/+", "/", p.path or "/").rstrip("/") or "/"
        return urllib.parse.urlunsplit((p.scheme.lower(), host, path, "", ""))
    except Exception:
        return clean(url)


def extract_numeric_tokens(text):
    return tuple(sorted(set(re.findall(r"\d+(?:\.\d+)?(?:%|%p|배|억원|조원|대)?", clean(text)))))


def load_state():
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


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


def validate_bot(token):
    result = tg_api(token, "getMe").get("result") or {}
    username = str(result.get("username") or "").lstrip("@")
    if username.lower() != EXPECTED_BOT.lower():
        raise RuntimeError(f"Wrong Telegram bot: expected @{EXPECTED_BOT}, got @{username or 'unknown'}")


def resolve_chat_id(token, configured=""):
    configured = clean(configured)
    if configured:
        return configured
    updates = tg_api(token, "getUpdates").get("result") or []
    for update in reversed(updates):
        msg = update.get("message") or update.get("edited_message") or {}
        chat = msg.get("chat") or {}
        if chat.get("id") is not None and chat.get("type") == "private":
            return str(chat["id"])
    raise RuntimeError(f"@{EXPECTED_BOT}에서 /start를 먼저 보내야 합니다")


def send_telegram(token, chat_id, text):
    tg_api(token, "sendMessage", {
        "chat_id": chat_id,
        "text": text[:3900],
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    })


def relevant_text(text):
    low = clean(text).lower()
    semicon = any(t.lower() in low for t in SEMICON_TERMS)
    consumption = any(t.lower() in low for t in CONSUMPTION_TERMS)
    regional = any(t.lower() in low for t in REGIONAL_TERMS)
    return (semicon and consumption) or (regional and consumption)


def google_news_rss(query):
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode({
        "q": query, "hl": "ko", "gl": "KR", "ceid": "KR:ko",
    })
    r = requests.get(url, headers=HEADERS, timeout=25)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    out = []
    for node in root.findall("./channel/item"):
        title = clean(node.findtext("title"))
        link = clean(node.findtext("link"))
        source_el = node.find("source")
        source = clean(source_el.text) if source_el is not None else ""
        pub_raw = clean(node.findtext("pubDate"))
        try:
            pub = parsedate_to_datetime(pub_raw)
            if pub.tzinfo is None:
                pub = pub.replace(tzinfo=dt.timezone.utc)
            pub_kst = pub.astimezone(KST)
        except Exception:
            pub_kst = now_kst()
        item = {
            "title": title, "link": link, "source": source,
            "pub_kst": pub_kst.isoformat(timespec="minutes"), "kind": "news",
        }
        item["key"] = stable_key(item)
        out.append(item)
    return out


def collect_bok_notes():
    r = requests.get(BOK_LIST_URL, headers=HEADERS, timeout=25)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    out = []
    for a in soup.select("a[href]"):
        title = clean(a.get_text(" ", strip=True))
        href = clean(a.get("href"))
        if not title or "P0002353" not in href or "view.do" not in href or not relevant_text(title):
            continue
        item = {
            "title": title,
            "link": urllib.parse.urljoin(BOK_LIST_URL, href),
            "source": "한국은행",
            "pub_kst": now_kst().isoformat(timespec="minutes"),
            "kind": "bok",
        }
        item["key"] = stable_key(item)
        out.append(item)
    return out


def collect_items():
    merged = {}
    errors = []
    try:
        for item in collect_bok_notes():
            merged[item["key"]] = item
    except Exception as exc:
        errors.append(f"한국은행: {exc}")
    for query in QUERIES:
        try:
            for item in google_news_rss(query):
                if relevant_text(f"{item['title']} {item['source']}"):
                    merged[item["key"]] = item
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
        return ""
    return ""


def is_error_text(text):
    low = clean(text).lower()
    return any(marker in low for marker in ERROR_MARKERS)


def article_sentences(url):
    r = requests.get(url, headers=HEADERS, timeout=30, allow_redirects=True)
    r.raise_for_status()
    if is_error_text(r.text[:5000]):
        raise RuntimeError("오류 페이지 감지")
    soup = BeautifulSoup(r.text, "html.parser")
    for tag in soup(["script", "style", "noscript", "nav", "header", "footer", "aside", "form", "svg"]):
        tag.decompose()
    paras = []
    for selector in ("article p", "[class*='article'] p", "[class*='content'] p", "main p", "p"):
        for p in soup.select(selector):
            text = clean(p.get_text(" ", strip=True))
            if len(text) >= 45 and not is_error_text(text) and text not in paras:
                paras.append(text)
        if len(paras) >= 8:
            break
    if not paras:
        raise RuntimeError("본문 추출 실패")
    selected = []
    for p in paras[:12]:
        if relevant_text(p):
            selected.append(p)
        if len(selected) >= 3:
            break
    return selected or paras[:3]


def to_korean(text):
    text = clean(text)
    if not text or re.search(r"[가-힣]", text):
        return text
    try:
        out = clean(GoogleTranslator(source="auto", target="ko").translate(text))
        return out if re.search(r"[가-힣]", out or "") else text
    except Exception:
        return text


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
    return " · ".join(dict.fromkeys(labels)) or "반도체 소비파급"


def build_alert(item, url, sentences):
    title = to_korean(item["title"])
    body = [to_korean(s) for s in sentences[:3]]
    joined = " ".join([title] + body)
    nums = re.findall(r"[+-]?\d+(?:\.\d+)?\s*(?:%p|%|배|조원|억원|대)", joined)
    num_line = " · ".join(dict.fromkeys(nums)) if nums else "핵심 수치 추가 확인 필요"
    bullets = "\n".join("• " + html.escape(clean(s)[:360]) for s in body)
    pub = item.get("pub_kst", "")
    try:
        pub = dt.datetime.fromisoformat(pub).astimezone(KST).strftime("%Y-%m-%d %H:%M KST")
    except Exception:
        pass
    return (
        "📊 <b>[반도체 수혜지역 소비 웹감시]</b>\n"
        f"<b>{html.escape(title)}</b>\n\n"
        f"• 출처: {html.escape(item.get('source') or '')}\n"
        f"• 시각: {html.escape(pub)}\n"
        f"• 분류: {html.escape(classify(joined))}\n"
        f"• 핵심 수치: {html.escape(num_line)}\n\n"
        f"{bullets}\n\n"
        "• 중복 기준: 동일 기사 제목·매체·원문 주소 재전송 차단\n"
        "• 판정 기준: 고가 소비 편중 → 생활·서비스 소비 확산 여부\n"
        f"<a href=\"{html.escape(url, quote=True)}\">원문</a>"
    )


def is_recent(item):
    try:
        pub = dt.datetime.fromisoformat(item["pub_kst"])
        if pub.tzinfo is None:
            pub = pub.replace(tzinfo=KST)
        return now_kst() - pub.astimezone(KST) <= dt.timedelta(hours=MAX_ITEM_AGE_HOURS)
    except Exception:
        return True


def fuzzy_duplicate(item, recent_titles):
    title_norm = normalize_title(item.get("title", ""), item.get("source", ""))
    nums = extract_numeric_tokens(item.get("title", ""))
    for old in recent_titles[-300:]:
        old_title = old.get("title_norm", "")
        if not old_title:
            continue
        # 동일 수치가 포함된 거의 같은 제목은 매체가 달라도 같은 기사로 취급한다.
        if nums and tuple(old.get("numbers") or ()) != nums:
            continue
        ratio = difflib.SequenceMatcher(None, title_norm, old_title).ratio()
        if ratio >= 0.90:
            return True
    return False


def main():
    token = clean(os.getenv("KHS887900_BOT_TOKEN"))
    configured_chat_id = clean(os.getenv("KHS887900_CHAT_ID"))
    if not token:
        raise RuntimeError("KHS887900_BOT_TOKEN secret이 없습니다")

    validate_bot(token)
    chat_id = resolve_chat_id(token, configured_chat_id)
    items, errors = collect_items()
    state = load_state()

    # v1의 Google News URL 기반 ID가 매 실행마다 달라져 중복 알림이 발생했으므로,
    # v2 최초 실행에서는 현재 검색 결과 전체를 새 안정 키 기준으로 기준선 처리한다.
    if state.get("schema_version") != SCHEMA_VERSION:
        state["schema_version"] = SCHEMA_VERSION
        state["initialized"] = True
        state["seen_keys"] = [i["key"] for i in items][-3000:]
        state["sent_urls"] = []
        state["recent_titles"] = [
            {
                "title_norm": normalize_title(i.get("title", ""), i.get("source", "")),
                "numbers": list(extract_numeric_tokens(i.get("title", ""))),
                "seen_at": now_kst().isoformat(timespec="seconds"),
            }
            for i in items[:300]
        ]
        state["last_run_kst"] = now_kst().isoformat(timespec="seconds")
        state["last_new_count"] = 0
        state["last_sent_count"] = 0
        state["last_duplicate_suppressed"] = 0
        state["last_errors"] = errors[:10]
        save_state(state)
        return

    seen = set(state.get("seen_keys") or [])
    sent_urls = set(state.get("sent_urls") or [])
    recent_titles = list(state.get("recent_titles") or [])
    new_items = [i for i in items if i["key"] not in seen]
    sent_count = 0
    duplicate_suppressed = 0

    for item in reversed(new_items):
        if sent_count >= MAX_ALERTS_PER_RUN:
            break
        if not is_recent(item):
            continue
        if fuzzy_duplicate(item, recent_titles):
            duplicate_suppressed += 1
            continue
        try:
            url = publisher_url(item)
            if not url:
                continue
            c_url = canonical_url(url)
            if c_url in sent_urls:
                duplicate_suppressed += 1
                continue
            sentences = article_sentences(url)
            send_telegram(token, chat_id, build_alert(item, url, sentences))
            sent_count += 1
            sent_urls.add(c_url)
            recent_titles.append({
                "title_norm": normalize_title(item.get("title", ""), item.get("source", "")),
                "numbers": list(extract_numeric_tokens(item.get("title", ""))),
                "seen_at": now_kst().isoformat(timespec="seconds"),
            })
        except Exception:
            # 같은 실패 항목을 매 실행마다 다시 신규로 취급하지 않도록 아래에서 seen_keys에는 포함한다.
            continue

    state["seen_keys"] = list(dict.fromkeys((state.get("seen_keys") or []) + [i["key"] for i in items]))[-3000:]
    state["sent_urls"] = list(sent_urls)[-1500:]
    state["recent_titles"] = recent_titles[-500:]
    state["last_run_kst"] = now_kst().isoformat(timespec="seconds")
    state["last_new_count"] = len(new_items)
    state["last_sent_count"] = sent_count
    state["last_duplicate_suppressed"] = duplicate_suppressed
    state["last_errors"] = errors[:10]
    save_state(state)


if __name__ == "__main__":
    main()
