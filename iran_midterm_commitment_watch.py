#!/usr/bin/env python3
"""Iran / 2026-11-03 election-specific Trump posture watch.

Only election-timed reversal or verified new U.S. strikes qualify here.
Ceasefire, Hormuz maritime incidents, refinery policy, fuel prices, and
Silver Bulletin polls remain in their existing specialized watchers.
"""
from __future__ import annotations

import datetime as dt
import email.utils
import hashlib
import html
import json
import os
import pathlib
import re
import sys
import urllib.parse
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

ROOT = pathlib.Path(__file__).resolve().parent
STATE = ROOT / "iran_midterm_commitment_state.json"
OUT = ROOT / "out"
PENDING = OUT / "iran_midterm_pending.json"
MESSAGE = OUT / "iran_midterm_telegram.html"
REPORT = OUT / "iran_midterm_status.md"
DEBUG = OUT / "iran_midterm_debug.json"
KST = ZoneInfo("Asia/Seoul")
EASTERN = ZoneInfo("America/New_York")
PLEDGE_DATE = dt.datetime(2026, 10, 8, 12, 17, tzinfo=EASTERN).astimezone(dt.timezone.utc)
ELECTION_DATE = dt.date(2026, 11, 3)
END_DATE = dt.date(2026, 11, 18)
CANONICAL_PLEDGE_URL = "https://truthsocial.com/@realDonaldTrump/117406186276133332"
CNBC_URL = "https://www.cnbc.com/2026/10/08/iran-war-trump-midterm-election.html"
ARCHIVE_FEED = "https://www.trumpstruth.org/feed"
CENTCOM_RELEASES = "https://www.centcom.mil/MEDIA/PUBLIC-RELEASES/"
PRESS_FEEDS = [
    '(Trump Iran) (midterm OR "November 3") (attack OR strikes OR pledge OR reverses) when:3d',
    '(US Iran) (resumes strikes OR launched attack OR airstrikes OR bombs OR strike) when:3d',
    '(Trump Iran) (attack before elections OR no longer rules out strikes) when:3d',
]
ALLOWED_NEWS = {
    "Reuters": ("reuters",),
    "Associated Press": ("associated press", "ap news", "apnews"),
    "AFP": ("afp", "agence france-presse"),
    "BBC": ("bbc",),
    "Axios": ("axios",),
    "The Washington Post": ("washington post",),
    "The Wall Street Journal": ("wall street journal", "wsj"),
    "The New York Times": ("new york times",),
}
SOURCE_MAX_AGE_HOURS = 48
MAX_STORED = 150
SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; Iran-midterm-alert/1.0; GitHubActions)",
    "Cache-Control": "no-cache",
})

@dataclass(frozen=True)
class Evidence:
    kind: str
    source: str
    title: str
    published: dt.datetime
    url: str
    raw_text: str
    official_voice: bool = False
    original_post_id: str = ""

    def key(self):
        token = f"{self.kind}:{self.source}:{self.original_post_id or self.url}"
        return hashlib.sha256(token.encode()).hexdigest()[:20]


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def clean(value: str) -> str:
    soup = BeautifulSoup(html.unescape(value or ""), "html.parser")
    return re.sub(r"\s+", " ", soup.get_text(" ", strip=True)).strip()


def parse_pubdate(s: str) -> dt.datetime | None:
    try:
        date = email.utils.parsedate_to_datetime(s)
        if date.tzinfo is None:
            date = date.replace(tzinfo=dt.timezone.utc)
        return date.astimezone(dt.timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return None


def recent(when: dt.datetime, now: dt.datetime) -> bool:
    age = (now - when).total_seconds()
    return -600 <= age <= SOURCE_MAX_AGE_HOURS * 3600


def classify_official(text: str) -> str | None:
    """Explicit statements of intent, not automatic evidence of military action.

    The 2026-10-08 promise concerns strikes *before* 2026-11-03. A statement
    contemplating strikes AFTER that date is not a broken promise.
    """
    low = clean(text).lower().replace("’", "'")
    if not re.search(r"\biran(?:ian)?\b", low):
        return None
    if re.search(r"\b(will not|won't|not be|no plans to)\s+(?:be\s+)?(?:attacking|attack|strike|striking)\b", low):
        return None

    if re.search(r"\b(i have ordered|we have ordered|authorized|i ordered)\b.{0,90}\b(strikes?|attacks?|bombings?)\b.{0,80}\biran\b", low):
        return "strike_announced"

    reversed_pledge = re.search(
        r"\b(no longer|reconsider|reverse|withdraw|rescind|cancel|walk back)\b"
        r".{0,120}\b(no.attack|pledge|promise|pause|iran|strik)", low,
    )
    explicit_attack = re.search(
        r"\b(will|intend|plan|going to)\s+(?:now\s+)?(?:attack|strike|bomb)\s+iran\b",
        low,
    )
    if not reversed_pledge and not explicit_attack:
        return None

    after_election = re.search(
        r"\b(after|following)\b.{0,45}\b("
        r"(?:nov(?:ember)?\s*(?:3rd|3))|midterms?|elections?)\b", low,
    )
    before_election = re.search(
        r"\b(before|prior to|ahead of)\b.{0,45}\b("
        r"(?:nov(?:ember)?\s*(?:3rd|3))|midterms?|elections?)\b", low,
    )
    if after_election and not before_election:
        return "post_election_threat"
    if reversed_pledge or before_election:
        return "policy_reversal"
    return "military_warning"

def classify_press(title: str, body: str) -> str | None:
    # Detect *definite* changes in the headline, not speculation or historic
    # context reprinted in an article body. Always reject negated strike verbs.
    t = clean(title).lower().replace("’", "'")
    if not re.search(r"\biran(?:ian)?\b", t):
        return None

    negative_strike = (
        r"(?:\bwill not\b|\bwon't\b|\bwould not\b|\bdoesn't\b|"
        r"\bnot going to\b|\bnot planning to\b|\bno plans? to\b|"
        r"\bno new\b|\brules? out\b|\bruled out\b|\bpledges? not to\b|"
        r"\bpauses?\b|\bsuspends?\b|\bpostpones?\b|\bdelays?\b)"
        r".{0,100}\b(?:attack|attacking|strike|strikes|striking|bomb|resume|resuming)\b"
    )
    if re.search(negative_strike, t):
        return None
    if any(x in t for x in (
        "may attack", "might attack", "could attack", "considers", "considering",
        "is expected", "options for", "what if", "would attack", "warns of",
        "was considering", "can attack", "prepares to attack", "preparing for strikes",
        "iran attacks us", "iran strikes us", "israel strikes iran",
        "says no attack", "won't attack", "not attack", "will not attack",
    )):
        return None

    strike = (
        r"(?:\bu\.s\.|\bus military\b|\bunited states\b|"
        r"\bamerican forces\b|\bpentagon\b)"
        r".{0,65}\b(resumes?|resumed|launches?|launched|conducts?|conducted|"
        r"begins?|began|carries out|carried out|hits?|strikes?|attacks?|bombs?|bombed)\b"
        r".{0,65}\biran(?:ian)?\b"
    )
    if re.search(strike, t) or re.search(
        r"(?:\bu\.s\.|\bus military\b|\bamerican forces\b)"
        r".{0,45}\bnew\b.{0,25}\bstrikes\b.{0,50}\biran", t,
    ):
        return "us_strike"

    reversal = (
        r"\b(trump|white house|us president)\b.{0,65}"
        r"\b(reverses|withdraws|rescinds|walks back|abandons|drops|breaks|reneges on)\b"
        r".{0,90}\b(iran|pledge|promise|midterm|election)"
    )
    if re.search(reversal, t):
        return "policy_reversal"

    explicit_attack = (
        r"\b(trump|white house|us president)\b.{0,60}"
        r"\b(will attack|will strike|to attack|to strike)\b.{0,50}\biran\b"
    )
    if re.search(explicit_attack, t):
        after = re.search(
            r"\b(after|following)\b.{0,35}\b(nov(?:ember)?\s*3|midterms?|elections?)\b",
            t,
        )
        before = re.search(
            r"\b(before|prior to|ahead of)\b.{0,35}\b(nov(?:ember)?\s*3|midterms?|elections?)\b",
            t,
        )
        if after and not before:
            return "post_election_threat"
        if before:
            return "policy_reversal"
        return "military_warning"
    return None

def provider(source_name: str) -> str | None:
    norm = re.sub(r"\s+", " ", source_name.lower()).strip()
    for canonical, aliases in ALLOWED_NEWS.items():
        if norm == canonical.lower() or norm in aliases:
            return canonical
    return None


def rss_request(url: str) -> ET.Element:
    r = SESSION.get(url, timeout=25)
    r.raise_for_status()
    if "xml" not in (r.headers.get("Content-Type") or "").lower() and not r.text.lstrip().startswith(("<?xml", "<rss", "<feed")):
        raise RuntimeError("RSS endpoint did not return XML")
    return ET.fromstring(r.content)


def google_feed_url(query: str) -> str:
    return "https://news.google.com/rss/search?" + urllib.parse.urlencode({
        "q": query, "hl": "en-US", "gl": "US", "ceid": "US:en",
    })


def read_press(now: dt.datetime) -> tuple[list[Evidence], int, list[str]]:
    candidates, success, errors = [], 0, []
    for query in PRESS_FEEDS:
        try:
            root = rss_request(google_feed_url(query))
            success += 1
        except Exception as e:
            errors.append("news: " + type(e).__name__ + ": " + str(e)[:150])
            continue
        for item in root.findall(".//item")[:100]:
            title = clean(item.findtext("title") or "")
            source = provider((item.findtext("source") or "").strip())
            if not source:
                continue
            published = parse_pubdate(item.findtext("pubDate") or "")
            if published is None or not recent(published, now) or published < PLEDGE_DATE:
                continue
            body = clean(item.findtext("description") or "")
            kind = classify_press(title, body)
            if not kind:
                continue
            url = (item.findtext("link") or "").strip()
            if not url.startswith("https://"):
                continue
            candidates.append(Evidence(kind, source, title, published, url, body))
    unique = {e.key(): e for e in candidates}
    return list(unique.values()), success, errors


def read_archive(now: dt.datetime) -> tuple[list[Evidence], bool, list[str]]:
    start = (now.astimezone(EASTERN).date() - dt.timedelta(days=3)).isoformat()
    end = (now.astimezone(EASTERN).date() + dt.timedelta(days=1)).isoformat()
    url = ARCHIVE_FEED + "?" + urllib.parse.urlencode({"start_date": start, "end_date": end})
    try:
        root = rss_request(url)
    except Exception as e:
        return [], False, ["archive: " + type(e).__name__ + ": " + str(e)[:150]]
    found = []
    for item in root.findall(".//item")[:150]:
        title = clean(item.findtext("title") or "")
        desc = clean(item.findtext("description") or "")
        content_ns = item.findtext("{http://purl.org/rss/1.0/modules/content/}encoded") or ""
        full = clean(" ".join((title, desc, content_ns)))
        # The archive includes reposted material. Reposts are not direct policy statements.
        if re.search(r"\b(?:retruth|reposted|retweet|rt @)\b", (title + " " + desc).lower()):
            continue
        date = parse_pubdate(item.findtext("pubDate") or "")
        if date is None or not recent(date, now) or date <= PLEDGE_DATE:
            continue
        kind = classify_official(full)
        if not kind:
            continue
        link = (item.findtext("link") or "").strip()
        original = re.search(r"https://truthsocial\.com/@realDonaldTrump/\d+", html.unescape(content_ns + " " + desc))
        official_url = original.group(0) if original else ""
        if not official_url and not link.startswith("https://www.trumpstruth.org/"):
            continue
        found.append(Evidence(
            kind=kind, source="트럼프 게시물 보존본", title=title[:240],
            published=date, url=official_url or link, raw_text=full,
            official_voice=True, original_post_id=official_url,
        ))
    unique = {e.key(): e for e in found}
    return list(unique.values()), True, []


def read_centcom(now: dt.datetime) -> tuple[list[Evidence], bool, list[str]]:
    """Official CENTCOM releases confirm actual action; not mere preparations.

    A date MUST be present on the release page. Never use the retrieval time as
    the publication date. Old official releases cannot trigger a new alert.
    """
    try:
        res = SESSION.get(CENTCOM_RELEASES, timeout=25)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, "html.parser")
    except Exception as exc:
        return [], False, ["centcom: " + type(exc).__name__ + ": " + str(exc)[:150]]
    candidates = []
    seen_urls = set()
    for a in soup.find_all("a", href=True):
        headline = clean(a.get_text(" ", strip=True))
        if not re.search(r"\biran(?:ian)?\b", headline.lower()):
            continue
        if not re.search(r"\b(?:strike|strikes|bomb|attack|attacks)\b", headline.lower()):
            continue
        url = urllib.parse.urljoin(CENTCOM_RELEASES, a["href"])
        if not url.startswith("https://www.centcom.mil/") or "/Article/" not in url or url in seen_urls:
            continue
        seen_urls.add(url)
        if any(x in headline.lower() for x in ("refutes", "denies", "no strikes", "not strike")):
            continue
        try:
            article = SESSION.get(url, timeout=20)
            article.raise_for_status()
            article_soup = BeautifulSoup(article.text, "html.parser")
            article_text = clean(article_soup.get_text(" ", strip=True))
            # Public Releases | Oct. 9, 2026
            date_match = re.search(
                r"(?:Public Releases|PRESS RELEASE|Public Release)\s*\|\s*"
                r"((?:January|February|March|April|May|June|July|August|September|October|November|December|"
                r"Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\.?\s+\d{1,2},\s+2026)",
                article_text, re.I,
            )
            if not date_match:
                continue
            pub_date = None
            for fmt in ("%B %d, %Y", "%b %d, %Y", "%b. %d, %Y"):
                try:
                    pub_date = dt.datetime.strptime(date_match.group(1).replace("Sept.", "Sep."), fmt).replace(
                        tzinfo=EASTERN
                    ).astimezone(dt.timezone.utc)
                    break
                except ValueError:
                    continue
            if not pub_date or not recent(pub_date, now) or pub_date <= PLEDGE_DATE:
                continue
            if not re.search(r"\b(u\.s\.|centcom|american|u\.s\. forces)\b", headline.lower()):
                continue
            candidates.append(Evidence(
                kind="us_strike", source="CENTCOM", title=headline,
                published=pub_date, url=url, raw_text=headline,
            ))
        except Exception:
            # One malformed official release must not corrupt valid others.
            continue
    return candidates, True, []


def decide(candidates: list[Evidence], today: dt.date) -> tuple[str, list[Evidence]] | None:
    grouped = defaultdict(list)
    for item in candidates:
        grouped[item.kind].append(item)
    for kind in ("strike_announced", "us_strike", "policy_reversal", "post_election_threat", "military_warning"):
        rows = grouped.get(kind, [])
        if not rows:
            continue
        primary = [x for x in rows if x.official_voice]
        news = [x for x in rows if not x.official_voice]
        # Reprints by one syndicator count as only one independent news source.
        distinct = {}
        for item in sorted(news, key=lambda x: x.published, reverse=True):
            distinct.setdefault(item.source, item)
        verified_news = list(distinct.values())
        if kind == "strike_announced" and primary:
            return kind, primary[:1] + verified_news[:2]
        if kind == "us_strike" and any(x.source == "CENTCOM" for x in rows):
            return kind, [next(x for x in rows if x.source == "CENTCOM")] + verified_news[:2]
        if kind in ("policy_reversal", "post_election_threat", "military_warning") and primary:
            return kind, primary[:1] + verified_news[:2]
        if kind in ("policy_reversal", "us_strike", "post_election_threat", "military_warning") and len(verified_news) >= 2:
            # Filter widely separated unrelated items with matching labels.
            recent_pair = sorted(verified_news, key=lambda x: x.published, reverse=True)
            if (recent_pair[0].published - recent_pair[1].published).total_seconds() <= 36 * 3600:
                return kind, recent_pair[:2]
    return None


def load_state() -> dict:
    if not STATE.exists():
        return {}
    try:
        state = json.loads(STATE.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            raise ValueError("state JSON object required")
        return state
    except (OSError, ValueError) as exc:
        raise RuntimeError("감시 상태 파일 파싱 오류: 이전 기준값 보존 필요") from exc


def validated_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    trusted = {
        "truthsocial.com", "www.trumpstruth.org", "trumpstruth.org",
        "news.google.com", "www.centcom.mil",
    }
    if parsed.scheme != "https" or host not in trusted:
        raise ValueError("검증된 원문 도메인 이외의 링크")
    return html.escape(url, quote=True)


def format_message(kind: str, rows: list[Evidence], now: dt.datetime) -> str:
    titles = {
        "policy_reversal": "선거 전 군사공격 유예 방침 변경",
        "strike_announced": "대이란 군사공격 지시 발표",
        "us_strike": "대이란 미국 공격 재개 교차보도",
        "post_election_threat": "선거 후 공격 가능성 발언",
        "military_warning": "대이란 공격 방침 강화·시점 미확정",
    }
    if kind not in titles or not rows:
        raise ValueError("Unrecognized event type")
    first = rows[0]
    stage = (
        "대통령 게시물 보존본 확인" if first.official_voice
        else "미국 중부사령부 공식 발표" if first.source == "CENTCOM"
        else "독립 언론 2곳 교차 확인"
    )
    title = f"[이란·11월 3일 중간선거] {titles[kind]}"
    when_kst = first.published.astimezone(KST).strftime("%Y-%m-%d %H:%M KST")
    lines = [
        f"<b>{html.escape(title)}</b>",
        "",
        f"상태: {stage}",
        f"변화: {html.escape(titles[kind])}",
        "기존 기준: 2026-10-08 트럼프 대통령의 11월 3일 이전 대이란 공격 유예 발언",
        f"확인 발표: {when_kst}",
    ]
    stage_date = first.published.astimezone(EASTERN).date()
    lines.append(
        "시점: 미국 중간선거 전" if stage_date < ELECTION_DATE else "시점: 미국 중간선거 당일·이후"
    )
    if kind == "policy_reversal":
        lines.append("의미: 선거 전 공격 유예 기대에 변화가 생겼습니다. 실제 공격 발생과 구분합니다.")
    elif kind == "post_election_threat":
        lines.append("의미: 선거 이후의 공격 가능성 발언입니다. 선거 전 유예 약속 철회나 실제 공격은 아닙니다.")
    elif kind == "military_warning":
        lines.append("의미: 공격 경고 발언은 확인됐으나 실행 시점이 특정되지 않았습니다.")
    elif kind == "strike_announced":
        lines.append("의미: 공격 관련 대통령 발표입니다. 전투 개시의 독립 확인과 구분합니다.")
    else:
        lines.append("의미: 복수 언론이 새로운 미국의 공격을 보도했습니다. 공식 작전 발표는 별도 확인이 필요합니다.")
    lines.extend(["", "근거:"])
    for row in rows[:3]:
        safe = validated_url(row.url)
        label = "원문" if row.official_voice or row.source == "CENTCOM" else row.source
        lines.append(f'<a href="{safe}">{html.escape(label)}</a>')
    lines.extend([
        "", "추가 확인: CENTCOM 실제 군사작전, 백악관 방침, 이란 대응, 호르무즈 통항·봉쇄 유지 여부",
        "검증: 서로 다른 출처·발표 시각과 새 사건 여부를 확인한 경우에만 발송",
    ])
    body = "\n".join(lines)
    if len(body) > 3900 or "🔗" in body:
        raise ValueError("Telegram message format invalid")
    return body


def main() -> int:
    OUT.mkdir(exist_ok=True)
    for p in (PENDING, MESSAGE, REPORT, DEBUG):
        p.unlink(missing_ok=True)

    now = now_utc()
    eastern_today = now.astimezone(EASTERN).date()
    state = load_state()
    old = state.get("events", {})
    if not isinstance(old, dict):
        raise RuntimeError("Old events state must be an object")
    if eastern_today > END_DATE:
        REPORT.write_text("선거 이후 감시 종료일 경과. 새로운 알림 없음.\n", encoding="utf-8")
        return 0

    archive, archive_ok, archive_errors = read_archive(now)
    press, press_success, press_errors = read_press(now)
    military, centcom_ok, centcom_errors = read_centcom(now)
    errors = archive_errors + press_errors + centcom_errors
    DEBUG.write_text(json.dumps({
        "checked_at_utc": now.isoformat(),
        "original_pledge_id": "117406186276133332",
        "archive_available": archive_ok,
        "centcom_available": centcom_ok,
        "news_queries_succeeded": press_success,
        "news_queries_total": len(PRESS_FEEDS),
        "candidates": [
            {"kind": x.kind, "source": x.source, "published": x.published.isoformat(),
             "title": x.title[:150], "url": x.url} for x in archive + press + military
        ],
        "errors": errors,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if not archive_ok and press_success < 2 and not centcom_ok:
        REPORT.write_text(
            "미확인: 대통령 발언 보존본·독립 뉴스 조회에 모두 충분한 접근이 없어 상태를 변경하지 않았습니다.\n"
            + "\n".join(errors[:4]) + "\n", encoding="utf-8"
        )
        return 2

    result = decide(archive + press + military, eastern_today)
    if result:
        kind, evidence = result
        # At most one message per posture regime. A formal reversal is not
        # re-sent for every Reuters/AP rewrite or every repeated quote.
        # Strike events are distinct from announcements.
        event_key = kind
        if event_key not in old:
            PENDING.write_text(json.dumps({
                "version": 1,
                "baseline": "2026-10-08-no-strikes-before-Nov-3",
                "baseline_source": CANONICAL_PLEDGE_URL,
                "events": {**old, event_key: {
                    "first_observed": now.isoformat(),
                    "sources": sorted(set(x.source for x in evidence)),
                    "evidence_urls": [x.url for x in evidence],
                }},
                "updated_at_utc": now.isoformat(),
            }, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            MESSAGE.write_text(format_message(kind, evidence, now), encoding="utf-8")
    REPORT.write_text(
        f"이란·중간선거 별도 감시 — 미국 동부 기준 {eastern_today.isoformat()}\n"
        f"- 공식 게시물 보존본 조회: {'성공' if archive_ok else '실패'}\n"
        f"- CENTCOM 공식 공개자료 조회: {'성공' if centcom_ok else '실패'}\n"
        f"- 독립 언론 검색 경로: {press_success}/{len(PRESS_FEEDS)} 성공\n"
        f"- 후보 사건 수: {len(archive)+len(press)+len(military)}\n"
        f"- 신규 송출 대상: {'1건' if MESSAGE.exists() else '0건'}\n"
        f"- 기존 호르무즈·정유·지지율 감시와 중복하는 단순 가격·기사 반복 알림은 제외\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
