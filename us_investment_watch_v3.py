#!/usr/bin/env python3
from __future__ import annotations

import datetime as dt
import hashlib
import html
import json
import re
import urllib.parse
from pathlib import Path

import us_investment_watch as base

# v3: improve recall for fresh exclusives without throwing away the richer v2 formatting/helpers.
# Main gaps fixed:
# 1) MoneyToday was absent from the trusted-source list.
# 2) Google News indexing can lag a just-published exclusive, so we also scan MoneyToday's economy page.
# 3) A broad high-priority query catches wording that does not contain our narrower sub-keywords.

for source in [
    "머니투데이", "서울경제", "조선비즈", "아시아경제", "파이낸셜뉴스", "매일경제", "한국경제",
]:
    if source not in base.TRUSTED:
        base.TRUSTED.append(source)

for term in [
    "최종 사인", "최종 서명", "최종서명", "원전 8기", "대형원전", "포괄적 프레임워크",
    "운영위원회", "국회 동의", "국회 보고", "최종 합의", "최종합의",
]:
    if term not in base.MATERIAL:
        base.MATERIAL.append(term)

EXTRA_QUERIES = [
    '"대미투자" when:1d',
    '"한미전략투자" when:1d',
    '"대미투자" "최종 사인" OR "최종 서명" OR "최종합의" when:2d',
    '"대미투자" "원전 8기" OR "대형원전" OR "포괄적 프레임워크" when:2d',
]
for query in reversed(EXTRA_QUERIES):
    if query not in base.QUERIES:
        base.QUERIES.insert(0, query)

MT_ECONOMY_URL = "https://www.mt.co.kr/economy"

# One-time catch-up for the exclusive the user identified before Google News indexed it.
PINNED = {
    "id": "mt-20260907-us-investment-nuclear-8-final-sign",
    "title": "[단독]대미투자 '원전 8기' 포함…이달 18일 최종 사인 - 머니투데이",
    "description": "2026-09-07 14:36 KST 공개된 머니투데이 단독 제목 기준. 원전 8기 포함과 9월 18일 최종 사인 일정이 새로 제기됨.",
    "source": "머니투데이",
    "link": "https://mt.co.kr/economy/2026/09/07/2026090714365254729",
    "published": "2026-09-07T14:36:00+09:00",
    "tags": ["원전", "최종합의"],
}


def direct_mt_items(now: dt.datetime) -> list[dict]:
    """Scan MoneyToday economy page directly so fresh exclusives do not wait for Google News indexing."""
    rows: list[dict] = []
    try:
        raw = base.fetch(MT_ECONOMY_URL).decode("utf-8", errors="ignore")
    except Exception:
        return rows

    # Extract anchors conservatively. We only keep headlines with material Korea-US-investment language.
    for match in re.finditer(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', raw, flags=re.I | re.S):
        href = html.unescape(match.group(1)).strip()
        title = base.clean(match.group(2))
        if not title or "대미투자" not in title:
            continue
        if not any(term.lower() in title.lower() for term in base.MATERIAL):
            continue
        if href.startswith("/"):
            href = urllib.parse.urljoin("https://www.mt.co.kr", href)
        if not href.startswith("http"):
            continue
        key = hashlib.sha256(f"MT|{title}|{href}".encode("utf-8")).hexdigest()[:24]
        rows.append({
            "id": key,
            "title": title if "머니투데이" in title else f"{title} - 머니투데이",
            "description": "머니투데이 경제면 직접 탐지",
            "source": "머니투데이",
            "link": href,
            "published": now.isoformat(),
            "tags": base.tags_for(title, "머니투데이 경제면 직접 탐지"),
        })
    return rows


def title_similarity(a: str, b: str) -> float:
    return base.similarity(a, b)


def collect(now: dt.datetime) -> list[dict]:
    items: list[dict] = []
    items.extend(direct_mt_items(now))
    items.extend(base.rss_items(now))

    unique: list[dict] = []
    for row in items:
        if any(row["id"] == old["id"] for old in unique):
            continue
        if any(title_similarity(row["title"], old["title"]) >= 0.70 for old in unique):
            continue
        unique.append(row)
    return unique


def alert_message(now: dt.datetime, rows: list[dict], usdkrw: float, fx_source: str) -> str:
    parts = ["<b>🇺🇸 대미투자 | 중요 업데이트</b>", ""]
    combined_tags: list[str] = []

    for index, row in enumerate(rows[:5], 1):
        tags = list(row.get("tags") or ["대미투자"])
        combined_tags.extend(tags)
        title_link = f'<a href="{html.escape(row["link"], quote=True)}"><b>{html.escape(row["title"])}</b></a>'
        parts.append(f"{index}. {title_link}")

        source_text = f"{row['title']} {row.get('description', '')}"
        conversions = base.extract_usd_conversions(source_text, usdkrw)
        if conversions:
            parts.append("• 원화 환산: " + " · ".join(html.escape(x) for x in conversions[:6]))
        for check in base.numeric_checks(source_text, usdkrw)[:2]:
            parts.append(f"• 숫자 검산: {html.escape(check)}")

        if row["id"] == PINNED["id"]:
            parts.extend([
                "• 구분: <b>원전 / 최종합의 일정</b>",
                "• 새 변화: 기사 제목 기준으로 <b>대미투자에 원전 8기 포함</b>과 <b>9월 18일 최종 사인</b>이라는 구체적 규모·날짜가 새로 제기됐습니다.",
                "• 해석: 오늘 오전의 ‘엔시날 1호·원전은 1호 의결 제외’ 보도와 반드시 충돌하는 것은 아닙니다. <b>1호는 엔시날, 후속 전체 대미투자 패키지에는 원전 8기가 포함</b>되는 구조일 수 있어 공식 문서로 범위를 구분해야 합니다.",
                "• 다음 확인: <b>원전 8기의 노형·총사업비·2,000억달러 포함 여부·한국 실제 출자액·한수원 지분/운영권·9월 18일 서명문</b>",
                "• 확정도: 현재 이 항목은 <b>머니투데이 단독보도</b> 단계이며 공식 발표 전에는 세부 조건을 확정 사실로 표시하지 않습니다.",
            ])
        else:
            parts.extend([
                f"• 구분: <b>{html.escape(' / '.join(tags[:3]))}</b>",
                f"• 의미: {html.escape(base.meaning(tags))}",
                f"• 다음 확인: {html.escape(base.next_check(tags, usdkrw))}",
            ])
        parts.append("")

    parts.extend(["<b>공식 투자 틀</b>"] + base.official_structure_lines(usdkrw))
    parts.extend([
        "",
        "<b>재평가 순서</b>",
        "① 공식 확정 → ② 한국 실제 출자액·지분율 → ③ I-SPV 의결권·손실분담 → ④ PPA/장기 구매계약",
        "⑤ 한국 기업 본계약·물량×단가 → ⑥ 착공·전원 인가/FID → ⑦ 장기 유지보수·반복매출",
        "",
        "<b>최대 역풍</b>",
    ])

    if PINNED["id"] in {row["id"] for row in rows}:
        risk = "한국이 원전 자금·건설 위험을 부담하지만 노형·설계·운영 주도권과 한국 기자재 물량은 제한되는 구조"
        early = "8기 노형(AP1000/APR1400)·한국 지분/운영권·손실분담·총사업비"
    else:
        risk, early = base.risk_summary(combined_tags)
    parts.extend([
        f"• {html.escape(risk)}",
        f"• 먼저 볼 지표: <b>{html.escape(early)}</b>",
        "",
        f'<a href="{base.MOU_OFFICIAL_URL}"><b>산업통상부 한미 전략투자 MOU</b></a>',
        base.footer(now, usdkrw, fx_source, "같은 사건 반복 기사와 단순 주가 반응은 제외"),
    ])
    return "\n".join(parts)


def main() -> int:
    if base.ALERT.exists():
        base.ALERT.unlink()
    state = base.load_state()
    now = dt.datetime.now(base.UTC)
    usdkrw, fx_source = base.get_usdkrw()
    seen = state.setdefault("seen", {})
    recent = list(state.get("recent_titles") or [])[-100:]

    fresh: list[dict] = []

    # Always prioritize the missed breaking exclusive once.
    if PINNED["id"] not in seen:
        fresh.append(PINNED)
        seen[PINNED["id"]] = now.isoformat()
        recent.append(PINNED["title"])
    else:
        for row in collect(now):
            if row["id"] in seen:
                continue
            seen[row["id"]] = now.isoformat()
            if any(title_similarity(row["title"], old) >= 0.70 for old in recent):
                continue
            fresh.append(row)
            recent.append(row["title"])
            if len(fresh) >= 5:
                break

    state["recent_titles"] = recent[-100:]
    state["last_checked_at"] = now.astimezone(base.KST).isoformat(timespec="seconds")
    state["last_usdkrw"] = usdkrw
    state["last_fx_source"] = fx_source

    if fresh:
        base.ALERT.write_text(alert_message(now, fresh, usdkrw, fx_source) + "\n", encoding="utf-8")
        state["last_alert_at"] = now.astimezone(base.KST).isoformat(timespec="seconds")
        print(f"new_alerts={len(fresh)}")
    else:
        print("new_alerts=0")

    base.STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
