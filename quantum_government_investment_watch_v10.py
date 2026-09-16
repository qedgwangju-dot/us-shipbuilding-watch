import re

import quantum_government_investment_watch as base
import quantum_government_investment_watch_v2 as v2
import quantum_government_investment_watch_v7 as v7
import quantum_government_investment_watch_v9  # 기존 중복·원화환산 패치 적용


# 기관 이름이나 행사·회의만으로는 알림하지 않는다.
# 실제 돈·지분·조달·선정·예산·지급·정부 시장창출 단계가 변해야 한다.
MATERIAL_ACTION_TERMS = [
    "final award", "award", "awarded", "funding", "grant", "investment",
    "equity stake", "minority stake", "non-controlling stake",
    "definitive agreement", "letter of intent", "chips act", "chips and science act",
    "milestone payment", "payment milestone", "tranche", "disbursement", "disbursed",
    "procurement", "government purchase", "purchase order", "advance market commitment",
    "qc-adds", "supplier selected", "selected supplier", "vendor selected",
    "appropriation", "budget request", "solicitation", "request for proposal", "request for proposals",
    "request for information", "notice of funding opportunity", "nofo",
    "government contract", "contract award", "contract awarded",
    "government demand", "first customer", "market commitment",
]

LOW_VALUE_TERMS = [
    "conference", "congress 2026", "showcase", "webinar", "panel discussion",
    "to host meeting", "meeting, discussions", "speaking at", "fireside chat",
    "stock popped", "stock jumps", "stocks surge", "shares soar",
    "hype to prototype",
]


def material_relevant(text: str) -> bool:
    low = base.clean(text).lower()
    has_quantum = any(x in low for x in base.QUANTUM_TERMS) or any(x in low for x in base.PORTFOLIO_COMPANIES)
    if not has_quantum:
        return False
    if any(x in low for x in LOW_VALUE_TERMS):
        # 행사·회의라도 실제 계약/지원/조달이 제목에 함께 있으면 허용
        strong = any(x in low for x in [
            "award", "funding", "grant", "investment", "procurement", "purchase order",
            "contract awarded", "milestone payment", "disbursement", "appropriation",
        ])
        if not strong:
            return False
    return any(x in low for x in MATERIAL_ACTION_TERMS)


base.relevant = material_relevant


# 환산 기준 줄의 '1달러=...' 자체를 미환산 외화로 다시 오인하지 않도록 최종 검증을 수정한다.
def _all_foreign_money_converted_v10(text: str) -> bool:
    patterns = [v7.v6.KOREAN_MONEY_RE, v7.v6.SYMBOL_MONEY_RE, v7.v6.CODE_MONEY_RE, v7.HYBRID_SYMBOL_KOREAN_RE]
    for pat in patterns:
        for m in pat.finditer(text):
            line_start = text.rfind("\n", 0, m.start()) + 1
            line_end = text.find("\n", m.end())
            if line_end < 0:
                line_end = len(text)
            line = text[line_start:line_end]
            if line.strip().startswith("환산 기준:"):
                continue
            if text[m.end():m.end()+2].lstrip().startswith("="):
                continue
            if pat is v7.v6.KOREAN_MONEY_RE:
                amount = v7.v6.parse_korean_number(m.groups()[:4])
                if amount is None:
                    continue
            elif pat is v7.HYBRID_SYMBOL_KOREAN_RE:
                if v7._hybrid_amount(m.groups()[1:4]) is None:
                    continue
            if not v7._has_krw_immediately_after(text, m.end()):
                return False
    return True


v7._all_foreign_money_converted = _all_foreign_money_converted_v10
base.build_message = v7.v6.build_message_with_krw


if __name__ == "__main__":
    v2.main()
