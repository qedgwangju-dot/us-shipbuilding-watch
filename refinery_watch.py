#!/usr/bin/env python3
import datetime as dt
import hashlib
import html
import json
import os
import pathlib
import re
import time
import threading
import urllib.parse
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup
from deep_translator import GoogleTranslator
import deep_translator.google as google_translate_runtime
from googlenewsdecoder import gnewsdecoder
import googlenewsdecoder.new_decoderv2 as decoder_runtime

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

ERROR_MARKERS = (
    "error 500", "500 (server error)", "server error", "that's an error",
    "that’s an error", "please try again later", "that's all we know",
    "that’s all we know", "internal server error", "service unavailable",
    "temporarily unavailable", "access denied", "request blocked",
    "enable javascript", "verify you are human", "captcha", "cloudflare",
    "오류 500", "서버 오류", "나중에 다시 시도", "접근이 거부",
)

REJECT_HOST_FRAGMENTS = (
    "news.google.com", "google.com", "googleusercontent.com", "translate.goog",
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
    )
}


_SOURCE_STAGES = {'unknown', 'rss', 'news_decode', 'news_redirect', 'publisher_search',
                  'article_fetch', 'translation_google', 'translation_google_http', 'translation_mymemory'}
_SOURCE_REASONS = {'unknown', 'http_denial', 'http_rate_limit', 'challenge_page',
                   'exception_denial', 'in_run_circuit', 'decoder_result_denial'}


class SourceAccessDenied(RuntimeError):
    """A held source failure with allowlisted nonsecret evidence, never raw URLs/body."""
    def __init__(self, message='Source access denied', *, stage='unknown', url=None,
                 status_code=None, reason='unknown', blocked_before_request=False):
        super().__init__(message)
        details = {'stage': stage if isinstance(stage, str) and stage in _SOURCE_STAGES else 'unknown',
                   'reason': reason if isinstance(reason, str) and reason in _SOURCE_REASONS else 'unknown',
                   'blocked_before_request': bool(blocked_before_request)}
        if type(status_code) is int and 100 <= status_code <= 599:
            details['http_status'] = status_code
        if isinstance(url, str) and url:
            # Never persist URL query strings, userinfo, paths, translation input,
            # exception messages or response bodies. A digest permits comparison
            # against a URL already independently known to the operator.
            details['request_url_sha256'] = hashlib.sha256(url.encode()).hexdigest()
            try:
                host = (urllib.parse.urlparse(url).hostname or '').encode('idna').decode().lower()
                if re.fullmatch(r'[a-z0-9.-]{1,253}', host):
                    details['provider'] = host
            except (ValueError, UnicodeError):
                pass
        self.source_details = details


def source_failure_details(exc):
    if not isinstance(exc, SourceAccessDenied):
        return None
    # Revalidate every field so mutated/external dictionaries cannot leak data.
    raw = getattr(exc, 'source_details', {})
    raw = raw if isinstance(raw, dict) else {}
    out = {'stage': raw.get('stage') if isinstance(raw.get('stage'), str) and raw['stage'] in _SOURCE_STAGES else 'unknown',
           'reason': raw.get('reason') if isinstance(raw.get('reason'), str) and raw['reason'] in _SOURCE_REASONS else 'unknown',
           'blocked_before_request': raw.get('blocked_before_request') is True}
    for name, pattern in (('provider', r'[a-z0-9.-]{1,253}'), ('request_url_sha256', r'[a-f0-9]{64}'),
                          ('origin_provider', r'[a-z0-9.-]{1,253}'), ('origin_request_url_sha256', r'[a-f0-9]{64}')):
        if isinstance(raw.get(name), str) and re.fullmatch(pattern, raw[name]):
            out[name] = raw[name]
    for name in ('http_status', 'origin_http_status'):
        if type(raw.get(name)) is int and 100 <= raw[name] <= 599:
            out[name] = raw[name]
    if isinstance(raw.get('origin_stage'), str) and raw['origin_stage'] in _SOURCE_STAGES:
        out['origin_stage'] = raw['origin_stage']
    if isinstance(raw.get('origin_reason'), str) and raw['origin_reason'] in _SOURCE_REASONS:
        out['origin_reason'] = raw['origin_reason']
    return out


def _source_denial(stage, url, response=None, reason=None):
    status = getattr(response, 'status_code', None)
    effective_url = getattr(response, 'url', None)
    if not isinstance(effective_url, str) or not effective_url:
        effective_url = url
    if reason is None:
        reason = 'http_rate_limit' if status == 429 else 'http_denial' if status in (401, 403) else 'exception_denial'
    return SourceAccessDenied(stage=stage, url=effective_url, status_code=status, reason=reason)


def is_access_denied(exc):
    response = getattr(exc, 'response', None)
    status = getattr(response, 'status_code', None)
    if status in (401, 403, 429) or isinstance(exc, SourceAccessDenied):
        return True
    text = str(exc).lower()
    return any(marker in text for marker in (
        'http 401', 'http 403', 'http 429', '401 client error', '403 client error', '429 client error', 'too many requests',
        'access denied', 'request blocked', 'verify you are human', 'captcha',
        '접근이 거부', '원문 접근이 거부됨',
    ))


def source_block_page(text):
    """Recognize an actual HTTP-200 challenge page, not script/metadata mentions."""
    soup = BeautifulSoup(text or '', 'html.parser')
    title = clean_paragraph(soup.title.get_text(' ', strip=True)).lower() if soup.title else ''
    if any(marker in title for marker in ('access denied', 'request blocked', 'just a moment',
                                          'verify you are human', 'attention required', 'too many requests')):
        return True
    if soup.select_one("form#challenge-form, form[action*='/sorry/']"):
        return True
    for tag in soup(['script', 'style', 'noscript', 'meta']):
        tag.decompose()
    visible = clean_paragraph(soup.get_text(' ', strip=True)).lower()
    return len(visible) < 1000 and visible.startswith((
        'access denied', 'request blocked', 'verify you are human', 'please verify you are human',
        'too many requests', 'sorry, you have been blocked', 'sorry you have been blocked'))


_NEWS_DENIED_HOSTS = set()
_NEWS_DENIAL_DETAILS = {}


def reset_source_circuits():
    # Called once at production main start, never between topics/items.
    _NEWS_DENIED_HOSTS.clear()
    _NEWS_DENIAL_DETAILS.clear()


def source_request(transport, method, url, *, stage='unknown', **kwargs):
    host = urllib.parse.urlparse(url).hostname or ''
    if stage == 'unknown' and host == 'news.google.com':
        stage = 'rss' if urllib.parse.urlparse(url).path == '/rss/search' else 'news_redirect'
    if host in _NEWS_DENIED_HOSTS:
        failure = SourceAccessDenied(stage=stage, url=url, reason='in_run_circuit', blocked_before_request=True)
        cause = _NEWS_DENIAL_DETAILS.get(host, {})
        # Status describes the earlier observed denial, not an unmade request.
        if 'http_status' in cause:
            failure.source_details['origin_http_status'] = cause['http_status']
        for name in ('provider', 'request_url_sha256'):
            if name in cause:
                failure.source_details['origin_' + name] = cause[name]
        failure.source_details['origin_stage'] = cause.get('stage', 'unknown')
        failure.source_details['origin_reason'] = cause.get('reason', 'unknown')
        raise failure
    try:
        response = getattr(transport, method)(url, **kwargs)
    except Exception as exc:
        if is_access_denied(exc):
            failure = exc if isinstance(exc, SourceAccessDenied) else _source_denial(stage, url, getattr(exc, 'response', None))
            if host == 'news.google.com':
                _NEWS_DENIED_HOSTS.add(host)
                _NEWS_DENIAL_DETAILS[host] = source_failure_details(failure)
            raise failure from None
        raise
    if response.status_code in (401, 403, 429) or source_block_page(response.text[:20000]):
        failure = _source_denial(stage, url, response,
            'challenge_page' if response.status_code not in (401, 403, 429) else None)
        if host == 'news.google.com':
            _NEWS_DENIED_HOSTS.add(host)
            _NEWS_DENIAL_DETAILS[host] = source_failure_details(failure)
        raise failure
    return response


def source_get(url, **kwargs):
    return source_request(requests, 'get', url, **kwargs)


def raise_if_access_denied(exc, *, stage='unknown'):
    if isinstance(exc, SourceAccessDenied):
        raise exc from None
    if is_access_denied(exc):
        raise _source_denial(stage, None, getattr(exc, 'response', None)) from None


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
    STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def tg_api(token, method, payload=None):
    url = f"https://api.telegram.org/bot{token}/{method}"
    r = (
        requests.post(url, data=payload or {}, timeout=25)
        if payload is not None
        else requests.get(url, timeout=25)
    )
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, dict) or data.get("ok") is not True:
        raise RuntimeError(f"Telegram API error: {data}")
    return data


def validate_telegram(token):
    data = tg_api(token, "getMe")
    if data.get("ok") is not True or not isinstance(data.get("result"), dict):
        raise RuntimeError("Invalid Telegram bot identity receipt")
    username = str(data["result"].get("username") or "").lstrip("@")
    if username.lower() != EXPECTED_BOT.lower():
        raise RuntimeError(
            f"Wrong Telegram bot: expected @{EXPECTED_BOT}, got @{username or 'unknown'}"
        )
    return username


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
            "parse_mode": "HTML",
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
    r = source_get(url, headers=HEADERS, timeout=25)
    r.raise_for_status()
    if source_block_page(r.text[:20000]):
        raise SourceAccessDenied('RSS provider explicitly denied access')
    root = ET.fromstring(r.content)
    if root.tag != 'rss' or root.find('channel') is None:
        raise RuntimeError('Expected RSS/channel response; source returned unexpected content')
    out = []
    for node in root.findall("./channel/item"):
        title = (node.findtext("title") or "").strip()
        link = (node.findtext("link") or "").strip()
        pub_raw = (node.findtext("pubDate") or "").strip()
        source_el = node.find("source")
        source = (source_el.text or "").strip() if source_el is not None else ""
        source_url = (
            (source_el.attrib.get("url") or "").strip()
            if source_el is not None
            else ""
        )
        try:
            pub = parsedate_to_datetime(pub_raw)
            if pub.tzinfo is None:
                pub = pub.replace(tzinfo=dt.timezone.utc)
            pub_kst = pub.astimezone(KST)
        except Exception:
            pub_kst = None
        key = hashlib.sha256((title + "|" + link).encode("utf-8")).hexdigest()[:24]
        out.append({
            "id": key,
            "title": title,
            "link": link,
            "source": source,
            "source_url": source_url,
            "pub_kst": pub_kst.isoformat(timespec="minutes") if pub_kst else "",
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
            errors.append(f"{query}: {type(exc).__name__}")
            if is_access_denied(exc):
                break
    items = list(merged.values())
    items.sort(key=lambda x: x["pub_kst"], reverse=True)
    return items, errors


def looks_like_error_page(text):
    low = re.sub(r"\s+", " ", (text or "")).strip().lower()
    return any(marker in low for marker in ERROR_MARKERS)


def is_publisher_url(url):
    try:
        parsed = urllib.parse.urlparse(url)
        host = (parsed.netloc or "").lower()
        if parsed.scheme not in ("http", "https") or not host:
            return False
        return not any(fragment in host for fragment in REJECT_HOST_FRAGMENTS)
    except Exception:
        return False


_SOURCE_TRANSPORT_LOCK = threading.RLock()


class _SourceTransport:
    """Preserve denial/rate-limit signals before dependencies swallow HTTP status."""
    def __init__(self, transport, stage='unknown'):
        self.transport = transport
        self.stage = stage
        self.denied = False
        self.denial = None

    def __getattr__(self, name):
        return getattr(self.transport, name)

    def _request(self, method, *args, **kwargs):
        if self.denied:
            raise self.denial
        kwargs.setdefault('timeout', 25)
        try:
            return source_request(self.transport, method, *args, stage=self.stage, **kwargs)
        except SourceAccessDenied as exc:
            self.denied = True
            self.denial = exc
            # Not a requests.RequestException: the pinned decoder would retry it.
            raise

    def get(self, *args, **kwargs):
        return self._request('get', *args, **kwargs)

    def post(self, *args, **kwargs):
        return self._request('post', *args, **kwargs)


def guarded_source_call(module, operation):
    # Scope this guard to a dependency module, never application-wide HTTP.
    # Restore its transport even when an operation fails or the run stops.
    with _SOURCE_TRANSPORT_LOCK:
        original = module.requests
        stage = {'googlenewsdecoder.new_decoderv2': 'news_decode', 'deep_translator.google': 'translation_google',
                 'deep_translator.mymemory': 'translation_mymemory'}.get(getattr(module, '__name__', ''), 'unknown')
        transport = _SourceTransport(original, stage)
        module.requests = transport
        try:
            result = operation()
        finally:
            module.requests = original
        if transport.denied:
            raise transport.denial
        return result


def guarded_gnewsdecoder(news_url):
    return guarded_source_call(decoder_runtime, lambda: gnewsdecoder(news_url, interval=0.2))


def decode_google_news_url(news_url):
    try:
        decoded = guarded_gnewsdecoder(news_url)
        if isinstance(decoded, dict) and is_access_denied(decoded.get('message', '') or decoded.get('error', '')):
            raise SourceAccessDenied(stage='news_decode', url=news_url, reason='decoder_result_denial')
        if isinstance(decoded, dict) and decoded.get("status"):
            candidate = (decoded.get("decoded_url") or "").strip()
            if is_publisher_url(candidate):
                return candidate
    except Exception as exc:
        raise_if_access_denied(exc)
        pass

    try:
        r = source_get(news_url, headers=HEADERS, timeout=25, allow_redirects=True)
        r.raise_for_status()
        if source_block_page(r.text[:20000]):
            raise _source_denial('news_redirect', news_url, r, 'challenge_page')
        if is_publisher_url(r.url):
            return r.url
    except Exception as exc:
        raise_if_access_denied(exc)
        pass
    return ""


def publisher_domain(item):
    source_url = (item.get("source_url") or "").strip()
    try:
        host = urllib.parse.urlparse(source_url).netloc.lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""


def search_publisher_url(item):
    domain = publisher_domain(item)
    if not domain:
        return ""
    query = f'"{item["title"]}" site:{domain}'
    try:
        r = requests.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            headers=HEADERS,
            timeout=25,
        )
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.select("a.result__a"):
            href = (a.get("href") or "").strip()
            if not href:
                continue
            parsed = urllib.parse.urlparse(href)
            if "duckduckgo.com" in parsed.netloc and parsed.query:
                qs = urllib.parse.parse_qs(parsed.query)
                href = urllib.parse.unquote((qs.get("uddg") or [""])[0])
            host = urllib.parse.urlparse(href).netloc.lower()
            if domain in host and is_publisher_url(href):
                return href
    except Exception as exc:
        raise_if_access_denied(exc, stage='publisher_search')
        pass
    return ""


def resolve_article_url(item):
    direct_url = (item.get("direct_url") or "").strip()
    if direct_url and is_publisher_url(direct_url):
        return direct_url

    decoded = decode_google_news_url(item["link"])
    if decoded:
        return decoded

    searched = search_publisher_url(item)
    if searched:
        return searched

    raise RuntimeError("원문 기사 주소를 확인하지 못함")


def clean_paragraph(text):
    return " ".join((text or "").replace("\xa0", " ").split()).strip()


def extract_article_body(url):
    r = requests.get(url, headers=HEADERS, timeout=30, allow_redirects=True)
    if r.status_code in (401, 403, 429):
        raise _source_denial('article_fetch', url, r)
    if r.status_code >= 400:
        raise RuntimeError(f"원문 접속 실패 HTTP {r.status_code}")
    final_url = r.url
    if not is_publisher_url(final_url):
        raise RuntimeError("원문이 언론사 페이지로 연결되지 않음")

    page_text = clean_paragraph(r.text)
    if source_block_page(r.text[:20000]):
        raise _source_denial('article_fetch', url, r, 'challenge_page')
    if looks_like_error_page(page_text[:5000]):
        raise RuntimeError("언론사 오류 페이지를 기사 본문으로 판정하여 차단")

    soup = BeautifulSoup(r.text, "html.parser")
    for tag in soup(["script", "style", "noscript", "nav", "header", "footer", "aside", "form", "svg"]):
        tag.decompose()

    selectors = [
        "article p",
        "main article p",
        "[class*='article-body'] p",
        "[class*='article__body'] p",
        "[class*='story-body'] p",
        "[class*='story__body'] p",
        "[class*='content-body'] p",
        "main p",
    ]

    paragraphs = []
    seen = set()
    for selector in selectors:
        for p in soup.select(selector):
            text = clean_paragraph(p.get_text(" ", strip=True))
            if len(text) < 45 or text in seen:
                continue
            low = text.lower()
            if looks_like_error_page(text):
                continue
            if any(x in low for x in (
                "sign up", "subscribe", "cookie", "privacy policy",
                "all rights reserved", "advertisement", "read more",
                "newsletter", "reporting by", "editing by", "follow us",
            )):
                continue
            seen.add(text)
            paragraphs.append(text)
        if len(paragraphs) >= 4:
            break

    if not paragraphs:
        raise RuntimeError("기사 본문을 정상적으로 추출하지 못함")

    # 원문 전체 재배포를 피하면서 핵심 본문만 한국어로 전달한다.
    selected = []
    total_words = 0
    for paragraph in paragraphs:
        words = paragraph.split()
        if total_words >= 140 or len(selected) >= 4:
            break
        remaining = 140 - total_words
        piece_words = words[: min(len(words), 55, remaining)]
        if not piece_words:
            continue
        piece = " ".join(piece_words)
        if looks_like_error_page(piece):
            continue
        selected.append(piece)
        total_words += len(piece_words)

    if not selected:
        raise RuntimeError("정상 기사 문단을 선별하지 못함")
    return final_url, selected


def has_hangul(text):
    return any("가" <= ch <= "힣" for ch in (text or ""))


def google_translate_http(text):
    r = requests.get(
        "https://translate.googleapis.com/translate_a/single",
        params={
            "client": "gtx",
            "sl": "auto",
            "tl": "ko",
            "dt": "t",
            "q": text,
        },
        headers=HEADERS,
        timeout=25,
    )
    r.raise_for_status()
    data = r.json()
    translated = "".join(
        part[0] for part in (data[0] or [])
        if isinstance(part, list) and part and part[0]
    ).strip()
    return translated


def validate_korean_translation(source, translated):
    translated = clean_paragraph(translated)
    if not translated:
        raise RuntimeError("번역 결과가 비어 있음")
    if looks_like_error_page(translated):
        raise RuntimeError("번역 서비스 오류문을 번역 결과로 판정하여 차단")
    if not has_hangul(translated):
        raise RuntimeError("한국어가 없는 번역 결과를 차단")
    # 영어 원문이 통째로 그대로 돌아온 경우를 차단한다. 고유명·약어는 허용한다.
    src_norm = re.sub(r"\W+", "", source or "").lower()
    out_norm = re.sub(r"\W+", "", translated).lower()
    if len(src_norm) > 60 and src_norm == out_norm:
        raise RuntimeError("영어 원문이 번역 없이 반환됨")
    return translated


def translate_ko(text):
    text = clean_paragraph(text)
    if not text:
        return ""
    if looks_like_error_page(text):
        raise RuntimeError("오류문은 번역·송출하지 않음")
    if has_hangul(text) and sum(1 for ch in text if "가" <= ch <= "힣") >= max(4, len(text) // 8):
        return text

    last_error = None
    for attempt in range(2):
        try:
            translated = guarded_source_call(google_translate_runtime,
                lambda: GoogleTranslator(source="auto", target="ko").translate(text=text))
            return validate_korean_translation(text, translated)
        except Exception as exc:
            raise_if_access_denied(exc, stage='translation_google')
            last_error = exc
            time.sleep(0.8 * (attempt + 1))

    try:
        translated = google_translate_http(text)
        return validate_korean_translation(text, translated)
    except Exception as exc:
        raise_if_access_denied(exc, stage='translation_google_http')
        last_error = exc

    raise RuntimeError(f"한국어 번역 실패: {last_error}")


def enrich_korean(item):
    original_url = resolve_article_url(item)
    final_url, paragraphs = extract_article_body(original_url)

    title_ko = translate_ko(item["title"])
    body_ko = []
    for paragraph in paragraphs:
        translated = translate_ko(paragraph)
        if translated:
            body_ko.append(translated)

    if not title_ko or not body_ko:
        raise RuntimeError("한국어 번역 결과가 비어 있음")
    if looks_like_error_page(title_ko) or any(looks_like_error_page(x) for x in body_ko):
        raise RuntimeError("오류문이 포함된 알림을 차단")

    enriched = dict(item)
    enriched["title_ko"] = title_ko
    enriched["body_ko"] = body_ko
    enriched["original_url"] = final_url
    return enriched


def e(text):
    return html.escape(str(text or ""), quote=False)


def format_article_alert(item):
    link = html.escape(item["original_url"], quote=True)
    lines = [
        "<b>[트럼프 정유업계 고유가 대응 웹감시]</b>",
        f"<b>[{e(classify(item['title']))}] {e(item['title_ko'])}</b>",
        f"출처: {e(item['source'] or '확인 필요')} | 발표: {e(item['pub_kst'].replace('T', ' '))}",
        "",
        "<b>본문 한국어 번역</b>",
    ]
    for paragraph in item["body_ko"]:
        lines.append(e(paragraph))
        lines.append("")
    lines.extend([
        f'<a href="{link}">원문</a>',
        "",
        (
            "감시 포인트: 9월 1일 회동의 공식 확정·개최 결과, 휘발유·경유 가격 대책, "
            "정제능력 확대·허가 완화, RFS·소형 정유사 면제·RIN 변화, "
            "Marathon Petroleum·Valero·Chevron·PBF·Delek의 구체적 설비투자 또는 정책 수혜 여부."
        ),
    ])
    text = "\n".join(lines).strip()
    if looks_like_error_page(BeautifulSoup(text, "html.parser").get_text(" ", strip=True)):
        raise RuntimeError("최종 Telegram 메시지에 오류문이 남아 있어 송출 차단")
    return text


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
            "<b>[트럼프 정유업계 고유가 대응 웹감시 시작]</b>\n"
            f"발송 봇: @{bot_username}\n"
            "감시 주기: 약 10분\n"
            "영어 기사 처리: 제목과 핵심 본문을 한국어로 변환한 경우에만 송출합니다. "
            "번역 실패·오류 페이지·영어 원문 그대로 반환은 송출하지 않습니다.\n"
            "원문 링크는 ‘원문’ 한 단어를 눌러 이동하도록 표시합니다."
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
            print(
                f"alert_sent=true bot=@{bot_username} message_ids={ids} "
                f"article_id={item['id']}"
            )
        except Exception as exc:
            # 영어 원문이나 오류문으로 절대 대체 송출하지 않는다.
            # 미발송 항목은 seen에 넣지 않아 다음 실행에서 다시 시도한다.
            translation_errors.append(f"{item['title']}: {exc}")
            print(
                f"translation_delivery_skipped=true article_id={item['id']} error={exc}"
            )

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
