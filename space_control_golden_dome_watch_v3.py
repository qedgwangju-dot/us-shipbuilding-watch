import re

import space_control_golden_dome_watch as base


# 2026-09-14 이미 기준선으로 확정·송출한 사건:
# 미국이 'on-orbit space control weapons' 보유를 처음 공개 인정.
# 이후 같은 사실을 재작성한 기사/통신 재전송은 언론사가 달라도 중복으로 차단한다.
BASELINE_PATTERNS = [
    r"on[- ]orbit space control weapon",
    r"space control weapon[s]?.*orbit",
    r"weapons? in (?:earth'?s )?orbit",
    r"deployed weapons? in space",
    r"space[- ]based weapons?.*first",
    r"u\.s\..*weapons?.*orbit",
    r"united states.*weapons?.*orbit",
]

# 같은 사건이라도 아래 정보가 새로 공개되면 후속 변화로 인정한다.
MATERIAL_DELTA_TERMS = [
    "manufacturer", "contractor", "built by", "manufactured by", "program name",
    "system name", "designation", "quantity", "number of weapons", "number of systems",
    "launch date", "launched on", "launch vehicle", "mission name", "satellite name",
    "kinetic", "non-kinetic", "jammer", "jamming payload", "laser payload",
    "electronic warfare payload", "entered service", "initial operational capability",
    "additional deployment", "follow-on deployment", "new deployment",
    "contract awarded", "new contract", "task order", "award value", "budget request",
    "appropriation", "funding increase", "funding decrease",
]

# 기사에서 단순히 '운동성인지 비운동성인지 밝히지 않았다'라고 쓰는 경우는 새 정보가 아니다.
NEGATED_DISCLOSURE_PATTERNS = [
    r"did not (?:say|specify|disclose|reveal).*kinetic",
    r"would not (?:say|specify|disclose|reveal).*kinetic",
    r"unclear whether.*kinetic",
    r"not disclosed.*(?:type|nature|number|quantity|manufacturer|contractor)",
]


def _contains_baseline_event(text: str) -> bool:
    low = text.lower()
    return any(re.search(p, low) for p in BASELINE_PATTERNS)


def _has_material_delta(text: str) -> bool:
    low = text.lower()
    # 미공개/불명확 문맥이면 delta로 보지 않는다.
    if any(re.search(p, low) for p in NEGATED_DISCLOSURE_PATTERNS):
        # 다른 실명·계약·수량 변화가 같이 있는지 아래에서 다시 본다.
        pass

    strong_terms = [
        "manufacturer", "contractor", "built by", "manufactured by", "program name",
        "system name", "designation", "quantity revealed", "number of weapons", "number of systems",
        "launch date revealed", "launched on", "launch vehicle", "mission name", "satellite name",
        "entered service", "initial operational capability", "additional deployment",
        "follow-on deployment", "new deployment", "contract awarded", "new contract",
        "task order", "award value", "budget request", "appropriation",
    ]
    if any(term in low for term in strong_terms):
        return True

    # 무기 유형은 '확인됐다/identified/confirmed'가 있을 때만 새 정보로 인정.
    type_terms = ["kinetic", "non-kinetic", "jammer", "laser", "electronic warfare"]
    if any(t in low for t in type_terms) and any(x in low for x in ["confirmed", "identified", "revealed", "is a ", "are "]):
        if not any(re.search(p, low) for p in NEGATED_DISCLOSURE_PATTERNS):
            return True
    return False


def _is_baseline_recap(item) -> bool:
    enriched = base.enrich(dict(item))
    text = f"{enriched.get('title','')} {enriched.get('summary','')} {enriched.get('article_text','')}"
    return _contains_baseline_event(text) and not _has_material_delta(text)


_RAW_RSS = base.fetch_rss
_RAW_HTML = base.fetch_html_source


def fetch_rss_v3(source):
    items = _RAW_RSS(source)
    out = {}
    for url, item in items.items():
        if _is_baseline_recap(item):
            print(f"[SPACE DEDUPE] 2026-09-14 궤도상 우주통제 무기 기준선 재기사 제외: {item.get('title','')}")
            continue
        out[url] = item
    return out


def fetch_html_source_v3(source, stage):
    items = _RAW_HTML(source, stage)
    out = {}
    for url, item in items.items():
        if _is_baseline_recap(item):
            print(f"[SPACE DEDUPE] 2026-09-14 궤도상 우주통제 무기 기준선 재게시 제외: {item.get('title','')}")
            continue
        out[url] = item
    return out


base.fetch_rss = fetch_rss_v3
base.fetch_html_source = fetch_html_source_v3


if __name__ == "__main__":
    base.main()
