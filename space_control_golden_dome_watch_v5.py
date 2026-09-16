import re

import space_control_golden_dome_watch as base
import space_control_golden_dome_watch_v4  # 기준선 재기사·과거기사·반응기사 필터 적용


# 기사 제목 문자열이 아니라 프로그램/행동/기업/금액/월 단위의 사건 상태로 중복을 기억한다.
def _action(text: str) -> str:
    low = text.lower()
    groups = [
        ("contract", ["contract awarded", "contract award", "task order", "award value"]),
        ("selection", ["selected", "selection", "supplier", "vendor"]),
        ("prototype", ["prototype", "flight-ready", "flight ready"]),
        ("test", ["flight test", "on-orbit test", "orbital test", "intercept test"]),
        ("launch", ["launch", "launched"]),
        ("deployment", ["deployment", "deployed", "additional deployment", "follow-on deployment"]),
        ("operational", ["operational", "initial operational capability", "entered service"]),
        ("gate", ["gate 2", "gate two", "gate 3", "gate three", "gate 4", "gate four"]),
        ("identity", ["manufacturer", "contractor", "built by", "system name", "program name", "designation"]),
        ("quantity", ["quantity", "number of systems", "number of weapons", "satellite count", "unit cost"]),
        ("budget", ["budget", "appropriation", "funding"]),
    ]
    for name, terms in groups:
        if any(t in low for t in terms):
            return name
    return "status"


def _amount_key(text: str) -> str:
    vals = []
    for raw, usd in base.money_mentions(text):
        vals.append(str(int(round(usd))))
    return ",".join(sorted(set(vals))) if vals else "na"


def semantic_key_event(item):
    text = f"{item.get('title','')} {item.get('summary','')}"
    cat = base.category(text)
    action = _action(text)
    cos = base.companies(text)
    company_key = ",".join(sorted(cos)) if cos else "sector"
    month_match = re.search(r"(20\d{2}-\d{2})", item.get("published_date", "") or "")
    month = month_match.group(1) if month_match else "unknown"
    amount = _amount_key(text)
    return f"{cat}|{action}|{company_key}|{month}|{amount}"


base.semantic_key = semantic_key_event


if __name__ == "__main__":
    base.main()
