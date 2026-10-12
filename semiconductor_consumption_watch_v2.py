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
from tesla_state_checkpoint import persist_intent as durable_checkpoint

KST = ZoneInfo("Asia/Seoul")
STATE_PATH = pathlib.Path("semiconductor_consumption_state.json")
CHECKPOINT_PATH = pathlib.Path("out/semiconductor_consumption_checkpoint.json")
DIAGNOSTICS_PATH = pathlib.Path("out/semiconductor_consumption_diagnostics.json")
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
    except Exception as exc:
        raise RuntimeError("Existing consumption state is unreadable; refusing to reset deduplication") from exc


def save_state(state):
    body = json.dumps(state, ensure_ascii=False, indent=2) + "\n"
    temporary = STATE_PATH.with_suffix(STATE_PATH.suffix + ".tmp")
    temporary.write_text(body, encoding="utf-8")
    temporary.replace(STATE_PATH)


class TelegramRejected(RuntimeError):
    """A definitive API rejection, with no response body or credential in the error."""
    def __init__(self, code, retry_after=None):
        self.code = code
        self.retry_after = retry_after
        super().__init__(f"Telegram rejected request (code={code})")


class TelegramDeliveryUnknown(RuntimeError):
    """A send was attempted but its result cannot safely be retried automatically."""
    pass


def tg_api(token, method, payload=None):
    url = f"https://api.telegram.org/bot{token}/{method}"
    r = requests.post(url, data=payload or {}, timeout=25) if payload is not None else requests.get(url, timeout=25)
    data = r.json()
    if isinstance(data, dict) and data.get("ok") is False:
        code = data.get("error_code")
        if type(code) is int and 400 <= code < 500 and r.status_code < 500:
            retry_after = (data.get("parameters") or {}).get("retry_after")
            raise TelegramRejected(code, retry_after)
    r.raise_for_status()
    if not isinstance(data, dict) or data.get("ok") is not True:
        raise RuntimeError("Telegram response has no verified success acknowledgement")
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
    try:
        data = tg_api(token, "sendMessage", {
            "chat_id": chat_id,
            "text": text[:3900],
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        })
        result = data.get("result") or {}
        message_id = result.get("message_id")
        destination = (result.get("chat") or {}).get("id")
        if type(message_id) is not int or message_id <= 0 or str(destination) != str(chat_id):
            raise TelegramDeliveryUnknown("Telegram acknowledgement lacks a matching delivery receipt")
        return {"message_id": message_id, "bot_username": EXPECTED_BOT,
                "confirmed_at_kst": now_kst().isoformat(timespec="seconds")}
    except TelegramRejected:
        raise
    except Exception as exc:
        raise TelegramDeliveryUnknown(f"Send outcome unverified: {type(exc).__name__}") from None


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


def checkpoint_state(state, persistence_blocked=False):
    """Local crash checkpoint; the workflow must persist it even after a failed scan."""
    save_state(state)
    CHECKPOINT_PATH.parent.mkdir(parents=True, exist_ok=True)
    marker = {
        "run_id": os.getenv("GITHUB_RUN_ID", "local"),
        "state_sha256": hashlib.sha256(STATE_PATH.read_bytes()).hexdigest(),
        "schema_version": state.get("schema_version"),
        "persistence_blocked": persistence_blocked,
    }
    CHECKPOINT_PATH.write_text(json.dumps(marker, sort_keys=True) + "\n", encoding="utf-8")


def safe_item_error(stage, exc):
    result = {"stage": stage, "error_type": type(exc).__name__}
    code = getattr(exc, "code", None)
    response = getattr(exc, "response", None)
    code = code if type(code) is int else getattr(response, "status_code", None)
    if type(code) is int:
        result["http_status"] = code
    return result


def main():
    for path in (CHECKPOINT_PATH, DIAGNOSTICS_PATH):
        if path.exists():
            path.unlink()
    token = clean(os.getenv("KHS887900_BOT_TOKEN"))
    configured_chat_id = clean(os.getenv("KHS887900_CHAT_ID"))
    if not token:
        raise RuntimeError("KHS887900_BOT_TOKEN secret이 없습니다")

    # Do not turn unreadable existing history into a fresh baseline.
    state = load_state()
    if not isinstance(state, dict):
        raise RuntimeError("Invalid consumption state; refusing to reset deduplication")
    validate_bot(token)
    chat_id = resolve_chat_id(token, configured_chat_id)
    items, errors = collect_items()
    errors = list(errors)

    # Preserve the existing v1 -> v2 baseline policy. Existing v2 seen history,
    # including the historical 43 items, is never cleared or replayed here.
    if state.get("schema_version") != SCHEMA_VERSION:
        if errors:
            raise RuntimeError("Incomplete collection; refusing to create a new baseline")
        state["schema_version"] = SCHEMA_VERSION
        state["initialized"] = True
        state["seen_keys"] = [i["key"] for i in items][-3000:]
        state["sent_urls"] = []
        state["recent_titles"] = [
            {"title_norm": normalize_title(i.get("title", ""), i.get("source", "")),
             "numbers": list(extract_numeric_tokens(i.get("title", ""))),
             "seen_at": now_kst().isoformat(timespec="seconds")}
            for i in items[:300]
        ]
        state.update(last_run_kst=now_kst().isoformat(timespec="seconds"),
                     last_new_count=0, last_sent_count=0,
                     last_duplicate_suppressed=0, last_errors=[],
                     pending_items={}, delivery_receipts={}, last_outcome="baseline")
        checkpoint_state(state)
        return

    seen_order = list(state.get("seen_keys") or [])
    seen = set(seen_order)
    sent_urls = list(dict.fromkeys(state.get("sent_urls") or []))
    recent_titles = list(state.get("recent_titles") or [])
    pending = state.get("pending_items", {})
    receipts = state.get("delivery_receipts", {})
    if not isinstance(pending, dict) or not isinstance(receipts, dict):
        raise RuntimeError("Invalid delivery state; refusing to reset pending work")
    pending = dict(pending)
    receipts = dict(receipts)
    cooldown = None
    if state.get("telegram_retry_after_kst"):
        try:
            cooldown = dt.datetime.fromisoformat(state["telegram_retry_after_kst"])
            if cooldown.tzinfo is None:
                raise ValueError("Missing timezone")
        except (TypeError, ValueError):
            raise RuntimeError("Invalid Telegram cooldown; manual review required") from None
        if now_kst() >= cooldown:
            state.pop("telegram_retry_after_kst", None)
            cooldown = None
    hold_statuses = {"delivery_unknown", "delivery_unknown_duplicate", "delivery_rejected"}
    allowed_statuses = hold_statuses | {"pending_pre_send", "pending_cap", "pending_retry_after"}
    candidates = {}
    for key, record in pending.items():
        if (not isinstance(record, dict) or record.get("status") not in allowed_statuses
                or not isinstance(record.get("item"), dict) or record["item"].get("key") != key
                or key in seen):
            raise RuntimeError("Inconsistent pending delivery state; manual review required")
        candidates[key] = record["item"]
    current_new = [i for i in items if i["key"] not in seen]
    for item in current_new:
        if pending.get(item["key"], {}).get("status") not in hold_statuses:
            candidates[item["key"]] = item
    work = sorted(candidates.values(), key=lambda i: i.get("pub_kst", ""))
    sent_count = 0
    send_attempt_count = 0
    duplicate_suppressed = 0
    outcomes = {}

    def outcome(item, status, **details):
        outcomes[item["key"]] = {"status": status, **details}

    def queue(item, status, **details):
        previous = pending.get(item["key"], {})
        preserved = {k: previous[k] for k in ("canonical_url", "message_sha256", "destination_sha256", "checkpoint_commit") if k in previous}
        pending[item["key"]] = {
            **preserved, "item": dict(item), "status": status,
            "updated_at_kst": now_kst().isoformat(timespec="seconds"), **details,
        }
        outcome(item, status, **details)

    def terminal(item, status):
        seen.add(item["key"])
        seen_order.append(item["key"])
        pending.pop(item["key"], None)
        outcome(item, status)

    persistence_blocked = False

    def checkpoint():
        state.update(
            seen_keys=list(dict.fromkeys(seen_order))[-3000:],
            sent_urls=sent_urls[-1500:], recent_titles=recent_titles[-500:],
            pending_items=pending, delivery_receipts=receipts,
            last_run_kst=now_kst().isoformat(timespec="seconds"),
            last_new_count=len(current_new), last_candidate_count=len(work),
            last_sent_count=sent_count, last_duplicate_suppressed=duplicate_suppressed,
            last_send_attempt_count=send_attempt_count,
            last_pending_count=len(pending), last_errors=errors[:50],
            last_error_count=len(errors), last_item_outcomes=outcomes,
        )
        checkpoint_state(state, persistence_blocked=persistence_blocked)

    state["last_outcome"] = "in_progress"
    for item in work:
        if item["key"] not in pending:
            queue(item, "pending_pre_send", stage="queued")
    checkpoint()

    for item in work:
        key = item["key"]
        prior = pending.get(key, {})
        if prior.get("status") in hold_statuses:
            outcome(item, prior["status"], stage="manual_delivery_review")
            errors.append(f"{key}: manual_delivery_review:{prior['status']}")
            continue
        if cooldown is not None and now_kst() < cooldown:
            queue(item, "pending_retry_after", stage="bot_rate_limit_wait",
                  retry_after_kst=cooldown.isoformat(timespec="seconds"))
            errors.append(f"{key}: bot_rate_limit_wait")
            continue
        if prior.get("status") == "pending_retry_after":
            try:
                retry_at = dt.datetime.fromisoformat(prior["retry_after_kst"])
                if now_kst() < retry_at:
                    outcome(item, "pending_retry_after", stage="rate_limit_wait")
                    errors.append(f"{key}: rate_limit_wait")
                    continue
            except (KeyError, TypeError, ValueError):
                outcome(item, "pending_retry_after", stage="invalid_retry_deadline")
                errors.append(f"{key}: invalid_retry_deadline")
                continue
        # Unknown results may have been delivered too; bound all send attempts.
        if send_attempt_count >= MAX_ALERTS_PER_RUN:
            queue(item, "pending_cap", stage="run_alert_limit")
            continue
        if not is_recent(item):
            terminal(item, "suppressed_stale")
            continue
        if fuzzy_duplicate(item, recent_titles):
            duplicate_suppressed += 1
            terminal(item, "suppressed_fuzzy_duplicate")
            continue
        # An alias of an uncertain send must not become an automatic retry.
        uncertain = [r for r in pending.values() if r.get("status") in {"delivery_unknown", "delivery_unknown_duplicate"}]
        uncertain_titles = [
            {"title_norm": normalize_title(r["item"].get("title", ""), r["item"].get("source", "")),
             "numbers": list(extract_numeric_tokens(r["item"].get("title", "")))}
            for r in uncertain
        ]
        if any(fuzzy_duplicate(item, [old]) for old in uncertain_titles):
            queue(item, "delivery_unknown_duplicate", stage="uncertain_title_match")
            errors.append(f"{key}: uncertain_title_match")
            continue
        stage = "publisher_resolution"
        try:
            url = publisher_url(item)
            if not url:
                raise RuntimeError("Publisher URL unavailable")
            c_url = canonical_url(url)
            if c_url in sent_urls:
                duplicate_suppressed += 1
                terminal(item, "suppressed_confirmed_url")
                continue
            if any(r.get("canonical_url") == c_url for r in uncertain):
                queue(item, "delivery_unknown_duplicate", stage="uncertain_url_match", canonical_url=c_url)
                errors.append(f"{key}: uncertain_url_match")
                continue
            stage = "article_extraction"
            sentences = article_sentences(url)
            if not sentences:
                raise RuntimeError("Article body unavailable")
            stage = "alert_formatting"
            message = build_alert(item, url, sentences)
            if not clean(message):
                raise RuntimeError("Empty alert")
        except Exception as exc:
            detail = safe_item_error(stage, exc)
            queue(item, "pending_pre_send", **detail)
            errors.append(f"{key}: {stage}:{detail['error_type']}")
            continue

        # The exact payload and destination intent must be remotely durable
        # BEFORE Telegram is called. A restart sees the hold even if this runner
        # disappears between transmission and receipt persistence.
        message_digest = hashlib.sha256(message[:3900].encode("utf-8")).hexdigest()
        destination_digest = hashlib.sha256((EXPECTED_BOT + "\0" + str(chat_id)).encode("utf-8")).hexdigest()
        queue(item, "delivery_unknown", stage="send_prepared", canonical_url=c_url,
              message_sha256=message_digest, destination_sha256=destination_digest)
        checkpoint()
        try:
            remote_commit = durable_checkpoint(STATE_PATH, CHECKPOINT_PATH, key, message_digest, destination_digest)
            if not isinstance(remote_commit, str) or not re.fullmatch(r"[0-9a-f]{40}", remote_commit):
                raise RuntimeError("Remote checkpoint was not acknowledged")
        except Exception as exc:
            # The helper may have rebased or published an unacknowledged hold.
            # Never let the final workflow step overwrite that remote snapshot.
            persistence_blocked = True
            queue(item, "pending_pre_send", **safe_item_error("durable_checkpoint", exc))
            errors.append(f"{key}: durable_checkpoint:{type(exc).__name__}")
            # The transport was never called. Keep the already-queued tail and
            # stop attempting sends until state persistence is healthy again.
            break
        print(f"tesla_send_intent_durable=true item_key={key} commit={remote_commit}")
        send_attempt_count += 1
        try:
            receipt = send_telegram(token, chat_id, message)
            if (not isinstance(receipt, dict) or type(receipt.get("message_id")) is not int
                    or receipt["message_id"] <= 0):
                raise TelegramDeliveryUnknown("No verified message receipt")
        except TelegramRejected as exc:
            if exc.code == 429:
                delay = exc.retry_after if type(exc.retry_after) is int and exc.retry_after >= 0 else 300
                cooldown = now_kst() + dt.timedelta(seconds=delay)
                state["telegram_retry_after_kst"] = cooldown.isoformat(timespec="seconds")
                queue(item, "pending_retry_after", stage="verified_rejection", http_status=exc.code,
                      retry_after_kst=cooldown.isoformat(timespec="seconds"))
            else:
                queue(item, "delivery_rejected", stage="verified_rejection", http_status=exc.code)
            errors.append(f"{key}: verified_rejection:{exc.code}")
        except Exception as exc:
            queue(item, "delivery_unknown", checkpoint_commit=remote_commit,
                  **safe_item_error("send_outcome_unknown", exc))
            errors.append(f"{key}: send_outcome_unknown:{type(exc).__name__}")
        else:
            sent_count += 1
            sent_urls.append(c_url)
            recent_titles.append({
                "title_norm": normalize_title(item.get("title", ""), item.get("source", "")),
                "numbers": list(extract_numeric_tokens(item.get("title", ""))),
                "seen_at": now_kst().isoformat(timespec="seconds"),
            })
            receipts[key] = {**receipt, "checkpoint_commit": remote_commit}
            terminal(item, "delivery_confirmed")
            outcome(item, "delivery_confirmed", message_id=receipt["message_id"])
            print(f"tesla_delivery_confirmed=true item_key={key} message_id={receipt['message_id']}")
        checkpoint()

    state["last_outcome"] = "degraded" if errors else ("deferred" if pending else "complete")
    checkpoint()
    DIAGNOSTICS_PATH.parent.mkdir(parents=True, exist_ok=True)
    report = {k: state[k] for k in (
        "last_run_kst", "last_outcome", "last_new_count", "last_candidate_count",
        "last_sent_count", "last_duplicate_suppressed", "last_pending_count",
        "last_send_attempt_count",
        "last_errors", "last_error_count", "last_item_outcomes",
    )}
    DIAGNOSTICS_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("tesla_scan_summary=" + json.dumps({k: v for k, v in report.items() if k not in {"last_item_outcomes", "last_errors"}}, sort_keys=True))
    if errors:
        raise RuntimeError("Tesla collection/delivery incomplete; diagnostics and pending work retained")


if __name__ == "__main__":
    main()
