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

# v6: high-recall nuclear follow-up monitor + compact Telegram output.
# Every USD amount shown in the alert is paired with KRW using the run-time FX.

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

# Official-source resolution: do not accept generic Naver policy/terms pages as an article body.
v5.SOURCE_DOMAINS.setdefault("대한민국 정책브리핑", ["motir.go.kr", "korea.kr"])
v5.SOURCE_DOMAINS.setdefault("산업통상부", ["motir.go.kr"])
_ORIG_LINK_ALLOWED = v5.link_allowed


def link_allowed_v6(url: str, domains: list[str]) -> bool:
    try:
        host = urllib.parse.urlparse(url).netloc.lower()
    except Exception:
        return False
    if host in {"policy.naver.com", "terms.naver.com"} or host.endswith(".policy.naver.com"):
        return False
    return _ORIG_LINK_ALLOWED(url, domains)


v5.link_allowed = link_allowed_v6

# Direct source pin: exact Edaily article into the strict full-text queue.
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
        extra.append("• <b>미국 측 제안</b>  대미투자 2,000억달러 중 1,200억달러를 원전 8기에 활용하자는 미국 측 요구로 보도됐습니다.")
    if re.search(r'70\s*%|71\.[0-9]\s*%', article_text):
        extra.append("• <b>재원 배분</b>  원전 제안액과 엔시날 사업비의 단순 합계가 대미투자 약 70% 수준이라는 의미이며, 한국의 확정 출자액은 아닙니다.")
    if re.search(r'1,?300\s*억\s*달러|130\s*billion', article_text, flags=re.I):
        extra.append("• <b>별도 보도 기준</b>  1,300억달러가 원전 8기 총사업 규모로 제시되면 1,200억달러 미국 측 제안액과 별도 숫자로 유지합니다.")
    if "한국형 원전" in low and re.search(r'2\s*기', article_text):
        extra.append("• <b>한국 몫</b>  한국형 원전 2기 포함 보도는 노형·공급망 몫이 구체화되는 변화로 따로 표시합니다.")
    if extra:
        return extra + lines
    return lines


v4.nuclear_8_summary = nuclear_8_summary_v6


def krw_text_from_won(won: float) -> str:
    eok = int(round(won / 100_000_000))
    jo, rem = divmod(eok, 10_000)
    if jo and rem:
        return f"약 {jo:,}조 {rem:,}억원"
    if jo:
        return f"약 {jo:,}조원"
    return f"약 {rem:,}억원"


def fmt_num(value: float) -> str:
    if abs(value - round(value)) < 1e-9:
        return f"{int(round(value)):,}"
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def usd_eok_with_krw(amount_eok: float, usdkrw: float) -> str:
    won = amount_eok * 100_000_000 * usdkrw
    return f"{fmt_num(amount_eok)}억달러({krw_text_from_won(won)})"


def add_krw(text: str, usdkrw: float) -> str:
    """Append KRW next to USD mentions while leaving already-converted text alone."""

    def repl_eok(m: re.Match) -> str:
        tail = text[m.end():m.end() + 24]
        if re.match(r'\s*\((?:약\s*)?[^)]*원\)', tail):
            return m.group(0)
        raw = m.group(1).replace(",", "")
        try:
            value = float(raw)
        except ValueError:
            return m.group(0)
        return usd_eok_with_krw(value, usdkrw)

    out = re.sub(r'(\d[\d,]*(?:\.\d+)?)\s*억\s*달러', repl_eok, text, flags=re.I)

    def repl_dollar_unit(m: re.Match) -> str:
        tail = out[m.end():m.end() + 24]
        if re.match(r'\s*\((?:약\s*)?[^)]*원\)', tail):
            return m.group(0)
        value = float(m.group(1).replace(",", ""))
        unit = m.group(2).lower()
        if unit in {"b", "bn", "billion"}:
            amount_eok = value * 10.0
        else:
            amount_eok = value / 100.0
        won = amount_eok * 100_000_000 * usdkrw
        return f"{m.group(0)}({krw_text_from_won(won)})"

    out = re.sub(r'\$(\d[\d,]*(?:\.\d+)?)\s*(billion|bn|b|million|m)\b', repl_dollar_unit, out, flags=re.I)
    return out


def compact_summary(article_text: str, usdkrw: float) -> list[str]:
    low = article_text.lower()
    lines: list[str] = []

    if re.search(r'1,?200\s*억\s*달러', article_text) and re.search(r'2,?000\s*억\s*달러', article_text) and re.search(r'8\s*기', article_text):
        lines.append(
            "• 미국이 " + usd_eok_with_krw(2000, usdkrw) + " 중 " + usd_eok_with_krw(1200, usdkrw)
            + "를 원전 8기에 쓰자고 제안한 단계입니다. <b>한국 확정 출자액은 아닙니다.</b>"
        )
    elif "원전" in low and re.search(r'8\s*기', article_text):
        lines.append("• 미국 현지 <b>원전 8기 추진</b>이 협의·프레임워크에 포함된 것으로 보도됐습니다.")

    if "원전" in low and re.search(r'8\s*기', article_text):
        lines.append("• <b>부지·노형·개별 사업비·한국 출자액·EPC/기자재 계약은 아직 별도 확인 대상</b>입니다.")

    if re.search(r'18\s*일', article_text) and any(x in low for x in ["서명", "사인"]):
        lines.append("• 이르면 <b>9월 18일 서명</b> 일정이 보도됐습니다.")

    if "정해진 바" in low and any(x in low for x in ["대미투자", "원전"]):
        lines = ["• 산업통상부는 <b>구체적인 대미투자 원전 프로젝트는 아직 정해지지 않았다</b>고 설명했습니다."]

    if not lines:
        for label, fact in v4.grouped_facts(article_text, limit=3):
            lines.append(f"• <b>{html.escape(label)}</b>  {html.escape(add_krw(fact, usdkrw))}")
    return lines[:3]


def compact_judgment(article_text: str, row: dict) -> str:
    low = article_text.lower()
    if "정해진 바" in low and any(x in low for x in ["대미투자", "원전"]):
        return "보도 숫자는 <b>제안·협의 단계</b>로 두고, 공식 서명문과 개별 프로젝트 확정 전에는 확정 투자액으로 보지 않습니다."
    if "원전" in low and re.search(r'8\s*기', article_text):
        return "투자 판단의 핵심은 8기 숫자보다 <b>한국 실제 출자액·지분·EPC/기자재 본계약</b>이 언제 잠기는지입니다."
    tags = list(row.get("tags") or ["대미투자"])
    return base.meaning(tags)


def compact_article_block(row: dict, article_text: str, fetch_status: str, usdkrw: float) -> list[str]:
    display_link = str(row.get("resolved_link") or row.get("link") or "")
    title_link = f'<a href="{html.escape(display_link, quote=True)}"><b>{html.escape(row.get("title", ""))}</b></a>'
    parts = [title_link, f"• 원문 확인  <b>{html.escape(fetch_status)}</b>", ""]

    source_text = f"{row.get('title','')} {article_text or row.get('description','')}"
    conversions = base.extract_usd_conversions(source_text, usdkrw)
    checks = base.numeric_checks(source_text, usdkrw)
    if conversions or checks:
        parts.append("<b>💰 핵심 숫자</b>")
        if conversions:
            parts.append("• " + " · ".join(html.escape(x) for x in conversions[:6]))
        for check in checks[:1]:
            parts.append(f"• {html.escape(add_krw(check, usdkrw))}")
        parts.append("")

    parts.append("<b>📌 핵심</b>")
    if article_text:
        parts.extend(compact_summary(article_text, usdkrw))
    else:
        parts.append("• 원문 본문을 확보하지 못해 세부 해석은 보류합니다.")
    parts.append("")

    parts.append("<b>🧭 판단</b>")
    parts.append("• " + compact_judgment(article_text, row))
    return parts


def compact_alert_message(now, rows, usdkrw: float, fx_source: str) -> str:
    parts = ["<b>🇺🇸 대미투자 | 중요 업데이트</b>", ""]
    combined_tags: list[str] = []

    for index, row in enumerate(rows[:5], 1):
        article_text = str(row.get("article_text") or "")
        fetch_status = str(row.get("fetch_status") or "원문 본문 직접 열람")
        combined_tags.extend(list(row.get("tags") or []))
        if len(rows) > 1:
            parts.append(f"<b>{index}.</b>")
        parts.extend(compact_article_block(row, article_text, fetch_status, usdkrw))
        if index != min(len(rows), 5):
            parts.extend(["", "────────────", ""])

    # Keep only the two most decision-useful official-framework lines.
    official = [add_krw(x, usdkrw) for x in base.official_structure_lines(usdkrw)]
    parts.extend(["", "<b>💵 공식 투자 틀</b>"])
    parts.extend(official[:2])
    parts.extend([
        "",
        "<b>🔎 다음 확인</b>",
        "• 최종 서명문 → 한국 실제 출자액·지분 → PPA/장기 구매계약 → EPC·기자재 본계약 → 착공/FID",
        "",
        f'<a href="{base.MOU_OFFICIAL_URL}"><b>산업통상부 한미 전략투자 MOU</b></a>',
        base.footer(now, usdkrw, fx_source, "달러 금액은 모두 원화 병기 · 원문 본문 확보 기사만 발송 · 반복 기사 제외"),
    ])
    return "\n".join(parts)


# v5.main() calls this module-level formatter; replace it with the compact version.
v5.readable_alert_message = compact_alert_message


def main() -> int:
    original_pinned = v3.PINNED
    try:
        v3.PINNED = PINNED_EDAILY_120B
        return v5.main()
    finally:
        v3.PINNED = original_pinned


if __name__ == "__main__":
    raise SystemExit(main())
