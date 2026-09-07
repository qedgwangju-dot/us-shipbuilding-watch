#!/usr/bin/env python3
from __future__ import annotations

import html
import re

import us_investment_watch as base
import us_investment_watch_v3 as v3

# v4 keeps v3's full-text grounding and monitoring logic, but reorganizes the
# Telegram message so the same information is easier to scan.


def fact_label(sentence: str) -> str:
    low = sentence.lower()
    if re.search(r'18\s*일|9\s*월|서명|사인|발표 예정|일정', sentence):
        return "일정"
    if re.search(r'8\s*기|\d+(?:\.\d+)?\s*gw|\d+\s*억달러|\d+\s*조원', sentence, flags=re.I):
        return "규모"
    if any(x in low for x in ["프레임워크", "큰 틀", "방향성", "합의문", "포함", "명시"]):
        return "합의 범위"
    if any(x in low for x in ["사업관리위원회", "운영위원회", "국회 보고", "국회 동의", "심의"]):
        return "절차"
    if any(x in low for x in ["가스복합", "알래스카", "lng", "엔시날", "원전 이외"]):
        return "동시 사업"
    if any(x in low for x in ["사업주", "관계자", "산업통상", "정부", "미국", "한국"]):
        return "당사자"
    if any(x in low for x in ["지분", "의결권", "손실분담", "수익배분", "운영권"]):
        return "투자 구조"
    if any(x in low for x in ["ppa", "전력판매", "장기 구매", "구매계약", "계약"]):
        return "매출 연결"
    return "핵심"


def clean_fact(sentence: str) -> str:
    text = base.clean(sentence)
    # Remove obvious photo/caption leftovers without changing the substantive sentence.
    text = re.sub(r'^/?사진\s*=\s*[^ ]+\s*', '', text)
    text = re.sub(r'^사진\s*[:=]\s*[^ ]+\s*', '', text)
    return text.strip(" -·")


def grouped_facts(article_text: str, limit: int = 7) -> list[tuple[str, str]]:
    facts = v3.select_original_facts(article_text, limit=limit)
    out: list[tuple[str, str]] = []
    used: set[str] = set()
    for fact in facts:
        fact = clean_fact(fact)
        if not fact:
            continue
        label = fact_label(fact)
        # Prefer one strong sentence per label; preserve another when it has a distinct hard number/date.
        key = label
        if key in used and not re.search(r'\d', fact):
            continue
        used.add(key)
        out.append((label, fact))
    return out[:limit]


def nuclear_8_summary(article_text: str) -> list[str]:
    """Readable, source-grounded summary for the 2026-09-07 MoneyToday exclusive.

    Only emits claims if matching concepts exist in the fetched article body.
    """
    low = article_text.lower()
    lines: list[str] = []
    if "원전" in low and re.search(r'8\s*기', article_text):
        lines.append("• <b>범위</b>  미국 현지 <b>원전 8기 건설 추진</b>을 대미투자 최종 합의문의 큰 틀에 포함하는 방향으로 보도됐습니다.")
    if re.search(r'18\s*일', article_text) and any(x in low for x in ["서명", "사인"]):
        lines.append("• <b>일정</b>  이르면 <b>9월 18일 최종 서명(MOU)</b>을 진행하는 일정이 기사에 제시됐습니다.")
    if any(x in low for x in ["프레임워크", "큰 틀", "방향성"]):
        lines.append("• <b>합의 수준</b>  8기 각각의 부지·노형·사업비를 확정한 단계가 아니라, <b>‘8기 건설 추진’이라는 프레임워크</b>를 합의문에 담는 수준으로 설명됐습니다.")
    if all(x in low for x in ["사업관리위원회", "운영위원회"]):
        lines.append("• <b>절차</b>  <b>사업관리위원회 → 운영위원회 심의 → 국회 보고</b> 절차가 남아 있다고 기사에 적시됐습니다.")
    if "가스복합" in low and "알래스카" in low and "lng" in low:
        lines.append("• <b>동시 포함</b>  최종 합의문에는 원전 외에 <b>가스복합화력발전·Alaska LNG</b> 등 에너지·인프라 사업도 함께 포함될 것으로 보도됐습니다.")
    return lines


def article_block(row: dict, article_text: str, fetch_status: str, usdkrw: float) -> list[str]:
    parts: list[str] = []
    title_link = f'<a href="{html.escape(row["link"], quote=True)}"><b>{html.escape(row["title"])}</b></a>'
    parts.append(title_link)
    parts.append(f"• 원문 확인  <b>{html.escape(fetch_status)}</b>")
    parts.append("")

    source_text = f"{row.get('title','')} {article_text or row.get('description','')}"
    conversions = base.extract_usd_conversions(source_text, usdkrw)
    checks = base.numeric_checks(source_text, usdkrw)
    if conversions or checks:
        parts.append("<b>💰 숫자</b>")
        if conversions:
            parts.append("• " + " · ".join(html.escape(x) for x in conversions[:6]))
        for check in checks[:2]:
            parts.append(f"• {html.escape(check)}")
        parts.append("")

    parts.append("<b>📌 원문 핵심</b>")
    if article_text:
        special = nuclear_8_summary(article_text)
        if special:
            parts.extend(special)
        else:
            for label, fact in grouped_facts(article_text):
                parts.append(f"• <b>{html.escape(label)}</b>  {html.escape(fact)}")
    else:
        parts.append("• 원문 본문을 직접 열지 못해 제목·공개 요약을 넘어선 세부 해석은 하지 않습니다.")
    parts.append("")

    tags = list(row.get("tags") or ["대미투자"])
    explanations = v3.article_specific_explanation(article_text, row) if article_text else []
    parts.append("<b>🧭 해석</b>")
    if explanations:
        for line in explanations[:4]:
            parts.append(f"• {html.escape(line)}")
    else:
        parts.append(f"• {html.escape(base.meaning(tags))}")
    parts.append("")

    parts.append("<b>✅ 확정 / ⚠️ 미확정</b>")
    if "원전" in (article_text or "").lower() and re.search(r'8\s*기', article_text or ""):
        parts.append("• ✅ 기사에서 확인: <b>원전 8기 추진 방향·9월 18일 서명 일정·프레임워크 수준</b>")
        parts.append("• ⚠️ 아직 별도 확인 필요: <b>8기 개별 사업명·부지·노형·사업비·한국 출자액·EPC/기자재 계약</b>")
    else:
        parts.append("• ✅ 원문에 직접 적힌 사업범위·금액·물량·날짜·당사자만 확인 사실로 취급")
        parts.append("• ⚠️ 원문에 없는 노형·지분·운영권·수주기업은 추정으로 채우지 않음")
    parts.append("")

    parts.append("<b>🔎 다음 확인</b>")
    if "원전" in (article_text or "").lower() and re.search(r'8\s*기', article_text or ""):
        parts.append("① 9월 18일 서명문 원문  ② 8기 사업명·부지·노형  ③ 개별 사업비·한국 출자액")
        parts.append("④ 한국 기업 EPC·기자재 본계약  ⑤ 착공·인허가·전원 인가 일정")
    else:
        parts.append(html.escape(base.next_check(tags, usdkrw)))
    return parts


def readable_alert_message(now, rows, usdkrw: float, fx_source: str) -> str:
    parts = ["<b>🇺🇸 대미투자 | 중요 업데이트</b>", ""]
    combined_tags: list[str] = []

    for index, row in enumerate(rows[:5], 1):
        article_text, fetch_status = v3.fetch_original(row)
        combined_tags.extend(list(row.get("tags") or []))
        if len(rows) > 1:
            parts.append(f"<b>{index}.</b>")
        parts.extend(article_block(row, article_text, fetch_status, usdkrw))
        if index != min(len(rows), 5):
            parts.extend(["", "────────────", ""])

    parts.extend([
        "",
        "<b>💵 공식 투자 틀</b>",
    ])
    parts.extend(base.official_structure_lines(usdkrw))
    parts.extend([
        "",
        "<b>📈 재평가 순서</b>",
        "① 원문 확인 → ② 공식 확정 → ③ 한국 실제 출자액·지분율 → ④ I-SPV 의결권·손실분담",
        "⑤ PPA/장기 구매계약 → ⑥ 한국 기업 본계약·물량×단가 → ⑦ 착공·전원 인가/FID → ⑧ 장기 유지보수·반복매출",
        "",
        "<b>⚠️ 최대 역풍</b>",
    ])

    if any("원전" in tag for tag in combined_tags):
        parts.append("• 원전 8기 프레임워크가 들어가도 <b>노형·사업비·EPC 배분·지분·인허가</b>가 잠기지 않으면 한국 기업 매출은 지연·축소될 수 있습니다.")
        parts.append("• 먼저 볼 지표: <b>9월 18일 서명문·8기 개별 사업비·한국 기업 본계약</b>")
    else:
        risk, early = base.risk_summary(combined_tags)
        parts.append(f"• {html.escape(risk)}")
        parts.append(f"• 먼저 볼 지표: <b>{html.escape(early)}</b>")

    parts.extend([
        "",
        f'<a href="{base.MOU_OFFICIAL_URL}"><b>산업통상부 한미 전략투자 MOU</b></a>',
        base.footer(now, usdkrw, fx_source, "원문 본문 직접 확인 우선 · 같은 사건 반복 기사와 단순 주가 반응 제외"),
    ])
    return "\n".join(parts)


# Replace only formatting; keep v3 monitoring/fetch/dedupe logic.
v3.alert_message = readable_alert_message


if __name__ == "__main__":
    raise SystemExit(v3.main())
