import html
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import quantum_government_investment_watch as base
import quantum_government_investment_watch_v2 as v2

KST = ZoneInfo("Asia/Seoul")

# 미국의 양자 R&D 지원에서 정부 조달·선구매약정·시장창출로의 전환을 별도로 감시한다.
EXTRA_OFFICIAL_SOURCES = [
    {"name": "백악관 양자 정책", "url": "https://www.whitehouse.gov/presidential-actions/"},
    {"name": "백악관 양자 팩트시트", "url": "https://www.whitehouse.gov/fact-sheets/"},
    {"name": "DOE 양자·Genesis 공식 뉴스", "url": "https://www.energy.gov/science/listings/articles"},
    {"name": "DARPA QBI 공식", "url": "https://www.darpa.mil/research/programs/quantum-benchmarking-initiative"},
]

for src in EXTRA_OFFICIAL_SOURCES:
    if src not in base.OFFICIAL_SOURCES:
        base.OFFICIAL_SOURCES.append(src)

for term in [
    "advance market commitment", "advance market commitments", "government procurement",
    "procurement", "acquisition", "purchase", "first customer", "government demand",
    "market creation", "commercialization", "deployment", "qc-adds", "qcad ds",
    "quantum genesis", "public-private partnership", "private-sector partnership",
    "benchmarking", "utility-scale", "fault-tolerant",
]:
    if term not in base.MONEY_POLICY_TERMS:
        base.MONEY_POLICY_TERMS.append(term)

_original_build_message = base.build_message


def is_procurement_item(item):
    raw = f"{item.get('title','')} {item.get('summary','')} {item.get('article_text','')}".lower()
    anchors = [
        "advance market commitment", "government procurement", "procurement", "qc-adds",
        "quantum genesis", "first customer", "market creation", "private-sector partnership",
        "delivery of at least one", "commercialization", "deploy",
    ]
    return "quantum" in raw and any(x in raw for x in anchors)


def procurement_message(item):
    item = base.fetch_article_meta(dict(item))
    raw = f"{item.get('title','')} {item.get('summary','')} {item.get('article_text','')}"
    low = raw.lower()
    safe_url = html.escape(item["url"], quote=True)
    safe_source = html.escape(item.get("source") or "미국 정부 공식자료")

    if "qc-adds" in low or "next frontier of quantum innovation" in low:
        title = "미 정부, 양자 R&D 지원에서 조달·선구매약정 기반 시장창출로 전환"
    elif "quantum genesis" in low:
        title = "DOE Quantum Genesis, 2028년까지 과학적 활용 가능한 내결함성 양자컴퓨터 배치 추진"
    else:
        translated = base.monitor.translate_piece(base.strip_source_suffix(item.get("title", "")))
        title = base.monitor.compact_korean(translated or base.strip_source_suffix(item.get("title", "")), 105)

    bullets = ["단계: 공식 정책·시장창출 경로"]

    # EO 14413 타임라인: 2026-06-22 서명 기준
    if "qc-adds" in low or "next frontier of quantum innovation" in low or "advance market commitment" in low:
        bullets.append("달라진 점: 단순 연구비 지원을 넘어 DOE 시설에 최소 1대의 QC-ADDS를 배치하고, 민간 양자기업의 참여를 유도할 선구매약정 등 수요창출 수단을 검토")
        bullets.append("정책 일정: 2026-09-20 전후 QC-ADDS 기술사양, 2026-10-20 전후 핵심부품 시장장벽 대응계획, 2026-12-19 전후 민관협력 모델·선구매약정 계획·성능평가 국가센터")
    if "quantum genesis" in low:
        bullets.append("상용화 경로: DOE Quantum Genesis는 2028년까지 과학적으로 의미 있는 내결함성 양자컴퓨팅 능력을 개발·배치하는 목표")

    bullets.append("투자 관점: 보조금은 연구개발 비용을 낮추지만, 정부 조달·선구매약정은 실제 주문·매출·납품실적을 만들어 민간 고객 확산과 밸류에이션 재평가를 촉발할 수 있음")
    bullets.append("수혜 확인 순서: 기술사양 충족 → 정부 성능검증 → 조달 공고·선구매약정 → 공급사 선정 → 설치·사용료·유지보수 매출 → 민간 고객 확산")
    bullets.append("다음 확인: QC-ADDS 사양·예산·조달방식·공급사·설치 DOE 시설·계약금액·성능 마일스톤·반복 사용료·유지보수 계약")
    bullets = bullets[:6]

    published = item.get("published_date") or ""
    date_line = f"공개일: <b>{html.escape(published)}</b>\n" if published else ""
    checked_line = f"확인시각: {datetime.now(KST).strftime('%Y-%m-%d %H:%M KST')}\n"
    bullet_text = "\n".join(f"• {html.escape(x)}" for x in bullets)

    return (
        "🚨 <b>미국 양자컴퓨팅 정부조달·시장창출 중요 변화</b>\n\n"
        f"<b>{html.escape(title)}</b>\n"
        f"출처: <a href=\"{safe_url}\">{safe_source}</a>\n"
        f"{date_line}{checked_line}\n"
        f"{bullet_text}\n\n"
        f"<a href=\"{safe_url}\"><b>원문</b></a>"
    )


def build_message(item):
    enriched = base.fetch_article_meta(dict(item))
    if is_procurement_item(enriched):
        return procurement_message(enriched)
    return _original_build_message(enriched)


base.build_message = build_message


if __name__ == "__main__":
    v2.main()
