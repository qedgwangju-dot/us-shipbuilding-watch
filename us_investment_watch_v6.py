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
    '"1200억달러" "원전 8기" when:2d',
    '"대미투자 70%" 원전 when:2d',
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
    "1200억달러", "1,200억달러", "대미투자 70%", "70% 윤곽",
    "1300억달러", "1,300억달러", "원전 8기", "한국형 원전", "한국형 원전 2기",
    "미국 내 원전 8기", "대형원전 8기",
]:
    if term not in base.MATERIAL:
        base.MATERIAL.append(term)

# Direct source pin: force exact Edaily article into the strict full-text queue.
PINNED_EDAILY_120B = {
    "id": "edaily-03660486645577824-us-nuclear-8-120b-70pct-v1",
    "title": "[단독]美 ‘1200억달러로 원전 8기’ 제안…대미투자 70% 윤곽 잡혔다 - 이데일리",
    "description": "미국 측 1,200억달러 원전 8기 제안과 2,000억달러 전략투자의 70% 윤곽을 원문 본문에서 직접 검증",
    "source": "이데일리",
    "link": "https://edaily.co.kr/news/read?newsId=03660486645577824",
    "published": "2026-09-07T16:31:42+09:00",
    "tags": ["원전", "사업비", "투자구조"],
}

_ORIG_DIRECT_MT_ITEMS = v3.direct_mt_items


def broadened_direct_mt_items(now: dt.datetime) -> list[dict]:
    rows = list(_ORIG_DIRECT_MT_ITEMS(now))
    try:
        raw = base.fetch(v3.MT_ECONOMY_URL).decode("utf-8", errors="ignore")
    except Exception:
        return rows

    triggers = [
        "대미투자", "원전 8기", "한국형 원전", "1200억달러", "1,200억달러",
        "1300억달러", "1,300억달러", "대미투자 70%", "미국 내 원전", "대형원전", "한미 투자",
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

    # Keep source-specific amount labels separate. Do not silently reconcile
    # Edaily's US proposal ($120B) with MoneyToday's $130B project-size report.
    if re.search(r'1,?200\s*억\s*달러|120\s*billion', article_text, flags=re.I):
        extra.append("• <b>미국 측 제안</b>  원문에서 대미투자 2,000억달러 가운데 <b>1,200억달러를 원전 8기에 활용</b>하자는 미국의 요구가 확인되면 ‘제안액’으로 표시합니다.")
    if re.search(r'70\s*%|71\.[0-9]\s*%', article_text):
        extra.append("• <b>재원 배분</b>  원전 제안액과 엔시날 사업비를 단순 합산한 <b>대미투자 약 70% 윤곽</b>은 확정 투자액이 아니라 현재 논의 중인 사업규모의 단순 합산으로 구분합니다.")
    if re.search(r'1,?300\s*억\s*달러|130\s*billion', article_text, flags=re.I):
        extra.append("• <b>별도 보도 기준</b>  원문이 미국 내 원전 8기 건설 규모를 <b>1,300억달러</b>로 제시하면 1,200억달러 ‘미국 측 제안액’과 자동 합치지 않고 출처별 숫자로 따로 유지합니다.")
    if "한국형 원전" in low and re.search(r'2\s*기', article_text):
        extra.append("• <b>한국 몫</b>  기사에서 <b>한국형 원전 2기 포함</b>을 명시하면 단순 EPC 참여보다 한국 노형·공급망 몫이 구체화된 변화로 표시합니다.")
    if extra:
        return extra + lines
    return lines


v4.nuclear_8_summary = nuclear_8_summary_v6


def main() -> int:
    # Force the newest direct article into v5's strict body-verification path.
    original_pinned = v3.PINNED
    try:
        v3.PINNED = PINNED_EDAILY_120B
        return v5.main()
    finally:
        v3.PINNED = original_pinned


if __name__ == "__main__":
    raise SystemExit(main())
