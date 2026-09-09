import html
import os
import re

import requests

import quantum_government_investment_watch as base
import quantum_government_investment_watch_v2 as v2
import quantum_government_investment_watch_v3 as v3


# -----------------------------------------------------------------------------
# 라우팅: 이 웹감시는 기존 메인 Telegram 봇만 사용한다.
# QUANTUM/AI_REMOTE 전용 토큰으로 자동 우회하지 않는다.
# -----------------------------------------------------------------------------
def send_telegram_main_bot(text: str):
    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    if not token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN 또는 TELEGRAM_CHAT_ID가 없습니다.")

    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": text[:4096],
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
        },
        timeout=base.TIMEOUT,
    )
    r.raise_for_status()


base.send_telegram = send_telegram_main_bot


# -----------------------------------------------------------------------------
# 2026-09-08 기준 이미 공식 확정·알림된 사실.
# 같은 사실을 다른 매체가 다시 쓰는 경우 새 알림으로 보지 않는다.
# ----------------------------------------------------------------------------
KNOWN_FINAL_AWARDS = {
    "d-wave": {
        "award_usd_m": 100,
        "award_date": "2026-09-04",
        "public_date": "2026-09-08",
        "tranches_usd_m": [53.552620, 9.075000, 16.695000, 20.390000, 0.287380],
        "shares": 7_095_721,
        "share_price": 14.093,
    },
    "rigetti": {
        "award_usd_m": 100,
        "award_date": "2026-09-04",
        "public_date": "2026-09-08",
        "tranches_usd_m": [43.9, 29.9, 26.2],
        "shares": 7_739_938,
        "share_price": 12.92,
    },
    "quantinuum": {"award_usd_m": 100, "public_date": "2026-09-08"},
    "psiquantum": {"award_usd_m": 100, "public_date": "2026-09-08"},
    "globalfoundries": {"award_usd_m": 375, "public_date": "2026-09-08"},
}


# 공식 NIST가 9/8 확정한 기업들을 모두 감시에 포함한다.
for company in ["psiquantum"]:
    if company not in base.PORTFOLIO_COMPANIES:
        base.PORTFOLIO_COMPANIES.append(company)


# Google News에서 같은 계약을 재가공한 기사·주가해설을 막기 위한 원본 fetch.
_RAW_RSS_FETCH = getattr(base, "fetch_news_rss")

MARKET_COMMENTARY_PATTERNS = [
    r"\bwhy\b.*\bstock\b",
    r"\bstock\b.*\bpopp?ed\b",
    r"\bstock\b.*\bjump(?:s|ed)?\b",
    r"\bshares?\b.*\bsoar(?:s|ed)?\b",
    r"\bquantum stocks?\b",
    r"\bstock price\b",
    r"\bwhat happened\b.*\bstock\b",
]

# 이미 알려진 수치 외에 아래와 같은 사실이 새로 붙으면 다시 알린다.
MATERIAL_UPDATE_TERMS = [
    "new tranche", "additional tranche", "milestone achieved", "milestone completed",
    "payment released", "payment received", "disbursed", "disbursement", "repaid",
    "award increased", "award reduced", "award amended", "amendment",
    "new shares", "additional shares", "share sale", "government sold", "government sale",
    "repurchase", "registration statement", "resale", "voting", "transfer restriction",
    "procurement", "purchase order", "government purchase", "advance market commitment",
    "qc-adds", "supplier selected", "selected supplier", "deployment", "installed",
    "commercial order", "customer order", "manufacturing agreement", "foundry agreement",
    "globalfoundries", "monarch quantum",
]


def _companies_in(text: str):
    low = text.lower()
    found = []
    aliases = {
        "d-wave": ["d-wave", "dwave", "qbts"],
        "rigetti": ["rigetti", "rgti"],
        "quantinuum": ["quantinuum"],
        "psiquantum": ["psiquantum"],
        "globalfoundries": ["globalfoundries", "global foundries", "gfs"],
    }
    for company, keys in aliases.items():
        if any(k in low for k in keys):
            found.append(company)
    return found


def _known_numbers_only(text: str, company: str) -> bool:
    """현재 알려진 계약 숫자만 반복하는 기사인지 대략 판정한다."""
    low = text.lower().replace(",", "")
    if company == "d-wave":
        known = [
            "100 million", "$100 million", "53552620", "53.552620", "9075000",
            "16695000", "20390000", "287380", "7095721", "14.093",
            "100000 qubit", "100000-qubit", "10000 qubit", "10000-qubit",
            "100 logical qubit", "one million operations",
        ]
    elif company == "rigetti":
        known = [
            "100 million", "$100 million", "43.9 million", "29.9 million", "26.2 million",
            "7739938", "12.92", "readout electronics", "cryogenic", "high-connectivity",
        ]
    elif company in ("quantinuum", "psiquantum"):
        known = ["100 million", "$100 million"]
    elif company == "globalfoundries":
        known = ["375 million", "$375 million"]
    else:
        return False

    # 신규 변경을 뜻하는 명시적 표현이 있으면 반복으로 보지 않는다.
    if any(term in low for term in MATERIAL_UPDATE_TERMS):
        return False

    # final/award/chips/government와 기존 금액이 함께 있으면 단순 재기사 가능성이 높다.
    has_event = any(x in low for x in ["final award", "definitive agreement", "chips", "department of commerce", "government"])
    has_known = any(x in low for x in known)
    return has_event and has_known


def _is_market_commentary(item) -> bool:
    title = base.strip_source_suffix(item.get("title", "")).lower()
    return any(re.search(pat, title) for pat in MARKET_COMMENTARY_PATTERNS)


def fetch_news_rss_filtered(source):
    items = _RAW_RSS_FETCH(source)
    out = {}
    for url, item in items.items():
        text = f"{item.get('title','')} {item.get('summary','')}"
        if _is_market_commentary(item):
            print(f"[QUANTUM DEDUPE] 단순 주가해설 제외: {item.get('title','')}")
            continue

        companies = _companies_in(text)
        if companies and all(_known_numbers_only(text, company) for company in companies):
            print(f"[QUANTUM DEDUPE] 기존 2026-09-08 CHIPS 최종계약 재기사 제외: {item.get('title','')}")
            continue

        out[url] = item
    return out


base.fetch_news_rss = fetch_news_rss_filtered


# -----------------------------------------------------------------------------
# 정확한 공식 수치가 들어온 경우 메시지를 더 정밀하게 구성한다.
# 공개일/사건일은 굵게, 확인시각은 일반 글씨로 유지한다.
# -----------------------------------------------------------------------------
_ORIGINAL_V3_BUILD = v3.build_message


def _money_krw(usd_m: float, rate):
    if not rate:
        return ""
    return base.krw_text(usd_m, rate)


def _exact_award_message(item, company: str):
    enriched = base.fetch_article_meta(dict(item))
    raw = f"{enriched.get('title','')} {enriched.get('summary','')} {enriched.get('article_text','')}"
    low = raw.lower().replace(",", "")
    rate, fx_date = base.fx_usd_krw()
    safe_url = html.escape(enriched["url"], quote=True)
    safe_source = html.escape(enriched.get("source") or "공식자료")
    checked = base.datetime.now(base.KST).strftime("%Y-%m-%d %H:%M KST") if hasattr(base, "datetime") else ""

    if company == "d-wave":
        title = "D-Wave, 미 상무부 CHIPS 지원 세부조건 변화"
        bullets = [
            "단계: 최종 계약·마일스톤 지급 구조",
            "공식 사건일: <b>2026-09-04</b> 지원계약 체결 → <b>2026-09-08</b> 지분계약 체결·공식 발표",
            f"금액: 최대 1억달러({html.escape(_money_krw(100, rate))}) — 최초 5,355만2,620달러 후속 907만5,000달러 → 1,669만5,000달러 → 2,039만달러 → 28만7,380달러, 기술·제조 마일스톤 연동" if rate else "금액: 최대 1억달러 — 최초 5,355만2,620달러 후속 907만5,000달러 → 1,669만5,000달러 → 2,039만달러 → 28만7,380달러, 기술·제조 마일스톤 연동",
            "정부 지분: 7,095,721주를 주당 14.093달러에 발행 — 소수·비지배 지분",
            "개발 목표: 10만 큐비트 어닐링 시스템 + 1만 큐비트 게이트형 시스템, 100 논리 큐비트·100만회 이상 연산 목표",
            "다음 확인: 후속 트랜치 실제 지급·마일스톤 달성·정부 지분 보유/매각·QC-ADDS 조달·상용 고객 수주",
        ]
    elif company == "rigetti":
        title = "Rigetti, 미 상무부 CHIPS 지원 세부조건 변화"
        bullets = [
            "단계: 최종 계약·마일스톤 지급 구조",
            "공식 사건일: <b>2026-09-04</b> 지원계약 체결 → <b>2026-09-08</b> 지분계약 체결·공식 발표",
            f"금액: 최대 1억달러({html.escape(_money_krw(100, rate))}) — 최초 4,390만달러, 이후 2,990만달러 → 2,620만달러를 마일스톤 달성 시 지급" if rate else "금액: 최대 1억달러 — 최초 4,390만달러, 이후 2,990만달러 → 2,620만달러를 마일스톤 달성 시 지급",
            "정부 지분: 7,739,938주를 주당 12.92달러에 발행. 미지급 지원분에 대응하는 주식은 계약 종료 시 회사가 총 1달러에 되살 수 있는 구조",
            "개발 병목: 판독전자 소형화·극저온 처리용량 대폭 확대·고연결성 칩 제조능력 확보",
            "다음 확인: 2·3차 트랜치 지급·기술 마일스톤·정부 지분 이전/매각·QC-ADDS 조달·상용 고객 수주",
        ]
    else:
        return _ORIGINAL_V3_BUILD(enriched)

    # 텔레그램 HTML을 직접 넣는 공식 사건일 줄과 일반 줄을 분리한다.
    rendered = []
    for b in bullets:
        if "<b>" in b:
            rendered.append(f"• {b}")
        else:
            rendered.append(f"• {html.escape(b)}")

    published = enriched.get("published_date") or ""
    date_line = f"공개일: <b>{html.escape(published)}</b>\n" if published else ""
    checked_line = f"확인시각: {checked}\n" if checked else ""
    fx_line = f"\n환산 기준: 1달러={rate:,.2f}원 · ECB {fx_date}" if rate else ""

    return (
        "🚨 <b>미국 양자컴퓨팅 정부투자·조달·지분 중요 변화</b>\n\n"
        f"<b>{html.escape(title)}</b>\n"
        f"출처: <a href=\"{safe_url}\">{safe_source}</a>\n"
        f"{date_line}{checked_line}\n"
        + "\n".join(rendered)
        + f"{html.escape(fx_line)}\n\n"
        + f"<a href=\"{safe_url}\"><b>원문</b></a>"
    )


def build_message(item):
    enriched = base.fetch_article_meta(dict(item))
    text = f"{enriched.get('title','')} {enriched.get('summary','')} {enriched.get('article_text','')}"
    companies = _companies_in(text)

    # D-Wave/Rigetti의 지분·트랜치·마일스톤 업데이트는 공식 수치형 메시지 우선.
    if "d-wave" in companies and any(x in text.lower() for x in ["tranche", "7,095,721", "7095721", "14.093", "milestone", "securities issuance"]):
        return _exact_award_message(enriched, "d-wave")
    if "rigetti" in companies and any(x in text.lower() for x in ["tranche", "7,739,938", "7739938", "12.92", "milestone", "securities issuance"]):
        return _exact_award_message(enriched, "rigetti")

    return _ORIGINAL_V3_BUILD(enriched)


base.build_message = build_message


if __name__ == "__main__":
    v2.main()
