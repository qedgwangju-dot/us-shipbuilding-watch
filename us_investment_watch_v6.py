#!/usr/bin/env python3
from __future__ import annotations

import datetime as dt
import hashlib
import html
import re
import urllib.parse

import us_investment_watch as base
import us_investment_watch_v3 as v3
import us_investment_watch_v4 as v4
import us_investment_watch_v5 as v5

# v6: capture material nuclear-investment follow-ups even when the headline
# does not literally contain '대미투자'. Keep v5 fail-closed full-text rule.

EXTRA_QUERIES = [
    '"원전 8기" "한국형 원전" when:2d',
    '"1300억달러" 원전 when:2d',
    '"한국형 원전 2기" 미국 when:2d',
    '"미국 내 원전 8기" 한국 when:2d',
    '"미국 원전" "대미투자" when:2d',
]
for query in reversed(EXTRA_QUERIES):
    if query not in base.QUERIES:
        base.QUERIES.insert(0, query)

for term in [
    "1300억달러", "1,300억달러", "원전 8기", "한국형 원전", "한국형 원전 2기",
    "미국 내 원전 8기", "대형원전 8기",
]:
    if term not in base.MATERIAL:
        base.MATERIAL.append(term)

PINNED_KOREAN_REACTOR = {
    "id": "naver-008-0005410466-us-nuclear-8-korean-2-v1",
    "title": "1300억달러 규모 미국 내 원전 8기 건설 추진…한국형 원전 2기 포함 - 머니투데이",
    "description": "원전 8기 총사업 규모와 한국형 원전 2기 포함 여부를 원문 본문에서 직접 검증",
    "source": "머니투데이",
    "link": "https://n.news.naver.com/mnews/article/008/0005410466?ntype=RANKING&rc=N&sid=100",
    "published": "2026-09-07T16:00:00+09:00",
    "tags": ["원전", "사업비", "한국기업 수주"],
}

_ORIG_DIRECT_MT_ITEMS = v3.direct_mt_items


def broadened_direct_mt_items(now: dt.datetime) -> list[dict]:
    rows = list(_ORIG_DIRECT_MT_ITEMS(now))
    try:
        raw = base.fetch(v3.MT_ECONOMY_URL).decode("utf-8", errors="ignore")
    except Exception:
        return rows

    triggers = [
        "대미투자", "원전 8기", "한국형 원전", "1300억달러", "1,300억달러",
        "미국 내 원전", "대형원전", "한미 투자",
    ]
    for match in re.finditer(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', raw, flags=re.I | re.S):
        href = html.unescape(match.group(1)).strip()
        title = base.clean(match.group(2))
        if not title or not any(t.lower() in title.lower() for t in triggers):
            continue
        if href.startswith("/"):
            href = urllib.parse.urljoin("https://www.mt.co.kr", href)
        if not href.startswith("http"):
            continue
        key = hashlib.sha256(f"MT-V6|{title}|{href}".encode("utf-8")).hexdigest()[:24]
        row = {
            "id": key,
            "title": title if "머니투데이" in title else f"{title} - 머니투데이",
            "description": "머니투데이 직접 탐지: 대미투자·원전 8기·한국형 원전·총사업비 핵심 숫자",
            "source": "머니투데이",
            "link": href,
            "published": now.isoformat(),
            "tags": base.tags_for(title, "원전 8기 한국형 원전 대미투자"),
        }
        if not any(r.get("id") == row["id"] for r in rows):
            rows.append(row)
    return rows


v3.direct_mt_items = broadened_direct_mt_items

_ORIG_NUCLEAR_SUMMARY = v4.nuclear_8_summary


def nuclear_8_summary_v6(article_text: str) -> list[str]:
    lines = list(_ORIG_NUCLEAR_SUMMARY(article_text))
    low = article_text.lower()
    extra: list[str] = []
    if re.search(r'1,?300\s*억\s*달러|130\s*billion', article_text, flags=re.I):
        extra.append("• <b>총사업 규모</b>  기사에서 미국 내 원전 8기 건설 규모를 <b>1,300억달러</b>로 제시했는지 원문 기준으로 확인합니다.")
    if "한국형 원전" in low and re.search(r'2\s*기', article_text):
        extra.append("• <b>한국 몫</b>  기사에서 <b>한국형 원전 2기 포함</b>을 명시했다면, 단순 EPC 참여보다 한국 노형·공급망 몫이 구체화된 중요한 변화로 표시합니다.")
    if extra:
        return extra + lines
    return lines


v4.nuclear_8_summary = nuclear_8_summary_v6


def main() -> int:
    # Force this article into the strict full-text verification queue once.
    original_pinned = v3.PINNED
    try:
        v3.PINNED = PINNED_KOREAN_REACTOR
        return v5.main()
    finally:
        v3.PINNED = original_pinned


if __name__ == "__main__":
    raise SystemExit(main())
