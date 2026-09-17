import re

import quantum_government_investment_watch as base
import quantum_government_investment_watch_v11 as core
import quantum_government_investment_watch_v12  # applies IBM/Anderon source and message patches


# 2026-09-16 IBM/Anderon 최대 10억달러 CHIPS R&D 최종 지원은 하나의 사건이다.
# 이후 언론사가 'matches', 'backs', 'secures', 'award' 등 다른 표현으로 재보도해도
# 신규 지급·증액·마일스톤·조달·고객수주가 없으면 같은 사건으로 정규화한다.
FOLLOWUP_TERMS = set(core.KNOWN_RECAP_NEW_TERMS) | {
    "new milestone", "milestone reached", "milestone passed",
    "award amendment", "award amended", "award increased", "award reduced",
    "additional funding", "additional award", "incremental funding",
    "new customer", "customer named", "first external customer", "external customer",
    "wafer shipment", "wafer shipments", "production volume", "annual capacity",
}


_ORIGINAL_EVENT_ACTION = core._event_action
_ORIGINAL_PROGRAM = core._program
_ORIGINAL_COMPANIES = core._companies
_RAW_FETCH_HTML = base.fetch_html_source

CANONICAL_IBM_ANDERON_URL = (
    "https://newsroom.ibm.com/2026-09-16-anderon%2C-an-ibm-company%2C-finalizes-agreement-"
    "with-the-u-s-department-of-commerce-for-a-1-billion-chips-award-to-accelerate-r-d-for-"
    "u-s-based-pure-play-quantum-foundry"
)


def _ibm_anderon_award_context(text: str) -> bool:
    low = (text or "").lower().replace(",", "")
    has_company = "anderon" in low or "ibm" in low
    has_foundry = any(x in low for x in [
        "quantum foundry", "quantum wafer", "pure-play quantum", "pure play quantum",
        "quantum semiconductor foundry",
    ])
    has_amount = any(x in low for x in ["$1 billion", "$1b", "1 billion", "one billion"])
    has_chips = "chips" in low or "department of commerce" in low or "commerce department" in low
    has_award = any(x in low for x in ["award", "funding", "agreement", "government deal", "government support"])
    return has_company and has_foundry and has_amount and has_chips and has_award


def _has_true_followup(text: str) -> bool:
    low = (text or "").lower()
    return any(term in low for term in FOLLOWUP_TERMS)


def _event_action_v13(text: str) -> str:
    low = (text or "").lower()
    if _ibm_anderon_award_context(low):
        if any(x in low for x in ["payment received", "payment released", "disbursed", "tranche"]):
            return "payment"
        if any(x in low for x in ["award amendment", "award amended", "award increased", "award reduced", "additional funding", "additional award"]):
            return "amendment"
        if any(x in low for x in ["purchase order", "government purchase", "procurement", "supplier selected", "selected supplier"]):
            return "procurement"
        if not _has_true_followup(low):
            return "final_award"
    return _ORIGINAL_EVENT_ACTION(text)


def _program_v13(text: str) -> str:
    if _ibm_anderon_award_context(text):
        return "chips"
    return _ORIGINAL_PROGRAM(text)


def _companies_v13(text: str):
    companies = set(_ORIGINAL_COMPANIES(text))
    if _ibm_anderon_award_context(text):
        companies.add("ibm")
    return sorted(companies)


def fetch_html_source_v13(source):
    items = _RAW_FETCH_HTML(source)
    if source.get("name") != "IBM 공식 뉴스":
        return items

    out = {}
    for key, item in items.items():
        title = (item.get("title") or "").lower()
        if "anderon" in title and "1 billion" in title and "chips" in title:
            fixed = dict(item)
            fixed["url"] = CANONICAL_IBM_ANDERON_URL
            out[CANONICAL_IBM_ANDERON_URL] = fixed
        else:
            out[key] = item
    return out


core._event_action = _event_action_v13
core._program = _program_v13
core._companies = _companies_v13
base.fetch_html_source = fetch_html_source_v13


def _self_test():
    official = (
        "Anderon, an IBM Company, Finalizes Agreement with the U.S. Department of Commerce "
        "for a $1 Billion CHIPS Award to Accelerate R&D for U.S.-Based Pure-Play Quantum Foundry"
    )
    recap = "IBM Matches a $1 Billion CHIPS Award For Quantum Foundry"
    assert _event_action_v13(official) == "final_award"
    assert _event_action_v13(recap) == "final_award"
    assert _program_v13(official) == "chips"
    assert _program_v13(recap) == "chips"
    assert "ibm" in _companies_v13(official)
    assert "ibm" in _companies_v13(recap)
    print("[QUANTUM SELFTEST] IBM/Anderon 2026-09-16 최종지원 재기사 정규화 정상")


if __name__ == "__main__":
    _self_test()
    core.main()
