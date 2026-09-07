#!/usr/bin/env python3
from __future__ import annotations

import datetime as dt

import us_investment_watch as base
import us_investment_watch_v3 as v3
import us_investment_watch_v6 as v6

# v7: pin Seoul Economic Daily follow-up on the Encinal/LNG-power investment
# increase and nuclear-package expansion. Keep v6's strict full-text-only rule,
# compact interpretation, and USD->KRW conversion.

for query in [
    '"LNG발전소 투자금 증액" 원전 패키지 when:2d',
    '"美 압박에 LNG발전소 투자금 증액" when:2d',
    '"원전도 패키지 포함" 대미투자 when:2d',
    '"가스발전소" 증액 원전 대미투자 when:2d',
]:
    if query not in base.QUERIES:
        base.QUERIES.insert(0, query)

for term in [
    "LNG발전소 투자금 증액", "가스발전소 투자금 증액", "원전도 패키지 포함",
    "원전 패키지", "美 압박", "증액",
]:
    if term not in base.MATERIAL:
        base.MATERIAL.append(term)

PINNED_SEDAILY = {
    "id": "sedaily-20088124-us-investment-lng-power-increase-nuclear-package-v1",
    "title": "美 압박에 LNG발전소 투자금 증액…원전도 패키지 포함될 듯 - 서울경제",
    "description": "엔시날/LNG발전 투자금 증액 배경과 원전의 대미투자 패키지 포함 여부를 서울경제 원문 본문에서 직접 검증",
    "source": "서울경제",
    "link": "https://sedaily.com/article/20088124",
    "published": "2026-09-07T17:54:00+09:00",
    "tags": ["1호·엔시날", "사업비", "원전", "투자구조"],
}

_ORIG_COLLECT = v3.collect


def collect_with_sedaily(now: dt.datetime) -> list[dict]:
    rows = [PINNED_SEDAILY]
    rows.extend(_ORIG_COLLECT(now))
    out: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        key = str(row.get("id") or "")
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        out.append(row)
    return out


v3.collect = collect_with_sedaily


def main() -> int:
    return v6.main()


if __name__ == "__main__":
    raise SystemExit(main())
