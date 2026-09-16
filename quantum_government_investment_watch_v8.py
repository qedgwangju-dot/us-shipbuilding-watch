import quantum_government_investment_watch as base
import quantum_government_investment_watch_v2 as v2
import quantum_government_investment_watch_v7 as v7


# 이 감시는 '양자회사 일반 뉴스'가 아니라 미국 정부 투자·지분·조달·시장창출 변화만 대상으로 한다.
STRICT_POLICY_TERMS = [
    "chips act", "chips and science act", "department of commerce", "u.s. department of commerce",
    "nist", "department of energy", "doe", "darpa", "federal government", "u.s. government",
    "government award", "government funding", "government investment", "government equity",
    "minority stake", "equity stake", "non-controlling stake", "letter of intent",
    "definitive agreement", "final award", "funding agreement", "grant", "award",
    "milestone payment", "payment milestone", "tranche", "disbursement",
    "procurement", "government purchase", "purchase order", "advance market commitment",
    "qc-adds", "government demand", "first customer", "supplier selected",
    "selected supplier", "appropriation", "budget request", "public-private partnership",
]


def strict_relevant(text: str) -> bool:
    low = base.clean(text).lower()
    has_quantum = any(x in low for x in base.QUANTUM_TERMS) or any(x in low for x in base.PORTFOLIO_COMPANIES)
    has_policy = any(x in low for x in STRICT_POLICY_TERMS)
    return has_quantum and has_policy


# 기존 base.relevant는 포트폴리오 회사 이름만 있어도 일반 행사/제품 뉴스를 잡을 수 있었다.
# 이를 정부자금·지분·조달 이벤트가 실제 포함된 경우로 제한한다.
base.relevant = strict_relevant


if __name__ == "__main__":
    v2.main()
