import re

import quantum_government_investment_watch as base
import quantum_government_investment_watch_v11 as core


# 특정 기사 URL이 아니라 공식 양자정책·파운드리 상태 변화를 감시한다.
# NIST 전자·CHIPS 업데이트와 IBM 연구·혁신 보도자료 목록을 공식 원천으로 사용한다.
for source in base.OFFICIAL_SOURCES:
    if source.get("name") == "NIST·CHIPS 공식 뉴스":
        source["url"] = "https://www.nist.gov/news-events/news-updates/topic/248486"
    elif source.get("name") == "IBM 공식 뉴스":
        source["url"] = "https://newsroom.ibm.com/press-releases-research-and-innovation"

# Anderon은 IBM의 양자 파운드리 자회사다. 제목에 IBM/quantum이 빠져도 공식 지원 변화를 놓치지 않는다.
for term in ["anderon", "quantum wafer", "quantum wafer foundry", "pure-play quantum foundry", "quantum semiconductor foundry", "300-millimeter quantum"]:
    if term not in base.QUANTUM_TERMS:
        base.QUANTUM_TERMS.append(term)
if "anderon" not in base.PORTFOLIO_COMPANIES:
    base.PORTFOLIO_COMPANIES.append("anderon")

# 파운드리·제조 인프라 후속 계약을 넓게 잡는 검색축을 추가한다.
extra_rss = {
    "name": "양자 파운드리·제조 정부지원 감시",
    "url": "https://news.google.com/rss/search?q=%28%22quantum+foundry%22+OR+%22quantum+wafer%22+OR+Anderon%29+%28CHIPS+OR+%22Department+of+Commerce%22+OR+government+award%29&hl=en-US&gl=US&ceid=US:en",
}
if not any(x.get("name") == extra_rss["name"] for x in base.NEWS_RSS):
    base.NEWS_RSS.append(extra_rss)


_ORIGINAL_EVENT_ACTION = core._event_action
_ORIGINAL_COMPANIES = core._companies


# 'finalization'도 최종 확정 단계로 통일해 NIST·IBM의 같은 사건이 2건으로 분리되지 않게 한다.
def _event_action_v12(text: str) -> str:
    low = text.lower()
    if any(x in low for x in [
        "final award", "final r&d award", "finalization", "finalisation",
        "finalized", "finalised", "finalizes", "finalises", "definitive agreement",
    ]):
        return "final_award"
    return _ORIGINAL_EVENT_ACTION(text)


# Anderon을 IBM 사건으로 정규화한다.
def _companies_v12(text: str):
    companies = set(_ORIGINAL_COMPANIES(text))
    if "anderon" in text.lower():
        companies.add("ibm")
    return sorted(companies)


core._event_action = _event_action_v12
core._companies = _companies_v12


_original_title = base.korean_title
_original_bullets = base.investment_bullets


def korean_title_v12(item, companies, stage):
    text = f"{item.get('title','')} {item.get('article_text','')}".lower()
    if "anderon" in text and any(x in text for x in ["$1 billion", "1 billion", "one billion"]):
        return "IBM 자회사 Anderon, 미 상무부 CHIPS R&D 최대 10억달러 최종 지원 확정"
    return _original_title(item, companies, stage)


def investment_bullets_v12(item, companies, stage, rate, fx_date):
    raw = f"{item.get('title','')} {item.get('summary','')} {item.get('article_text','')}"
    low = raw.lower()
    if "anderon" in low and any(x in low for x in ["$1 billion", "1 billion", "one billion"]):
        award_krw = base.krw_text(1000, rate) if rate else "원화 환산 확인 불가"
        ibm_krw = base.krw_text(1000, rate) if rate else "원화 환산 확인 불가"
        return [
            "단계: 최종 확정",
            "달라진 점: 2026-05-21 미 상무부·IBM 의향서 단계에서 2026-09-16 Anderon 최종 CHIPS R&D 지원계약으로 진전",
            f"정부 지원: 최대 10억달러({award_krw}) — 미국 양자 웨이퍼 제조 연구개발·양산기반 확장에 투입",
            f"민간 투입: IBM도 Anderon에 추가 10억달러({ibm_krw})를 투자해 정부지원과 별도로 제조기반을 확대",
            "생산 숫자: 뉴욕주 Albany의 300mm 양자 웨이퍼 전용 파운드리에서 첫 양자 웨이퍼가 이미 제조 공정에 투입된 상태",
            "다음 확인: 실제 지급 마일스톤·정부 지분 조건·외부 고객 실명·웨이퍼 출하량·연간 생산능력·파운드리 매출 발생 시점",
        ]
    return _original_bullets(item, companies, stage, rate, fx_date)


base.korean_title = korean_title_v12
base.investment_bullets = investment_bullets_v12


if __name__ == "__main__":
    core.main()
