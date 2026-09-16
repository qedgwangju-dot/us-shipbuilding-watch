import re

import space_control_golden_dome_watch as base


# 2026-09-14 Troy Meink의 'on-orbit space control weapons' 공개 인정은 이미 기준선으로 송출됐다.
# 이후 다른 매체가 같은 사실을 재작성해도 새 알림으로 보지 않는다.
KNOWN_MEINK_EVENT_MARKERS = [
    "troy meink",
    "on-orbit space control",
    "space control weapons",
]

# 아래처럼 실제로 새로운 식별정보가 생겼을 때만 같은 사건의 후속 알림을 허용한다.
MATERIAL_DELTA_TERMS = [
    "identified the system", "system identified", "named the system", "program name",
    "manufacturer revealed", "built by", "manufactured by", "contractor revealed",
    "quantity revealed", "number of systems", "number of weapons", "how many",
    "launch date revealed", "launched on", "launch vehicle", "mission name",
    "confirmed to be kinetic", "confirmed non-kinetic", "confirmed as a jammer",
    "operational designation", "entered service", "additional deployment",
    "follow-on deployment", "new contract", "contract awarded", "task order",
]


def _known_meink_recap(item) -> bool:
    enriched = base.enrich(dict(item))
    text = f"{enriched.get('title','')} {enriched.get('summary','')} {enriched.get('article_text','')}".lower()

    has_meink = "meink" in text or "troy" in text
    has_event = "on-orbit space control" in text or "space control weapons" in text
    if not (has_meink and has_event):
        return False

    if any(term in text for term in MATERIAL_DELTA_TERMS):
        return False

    # 최초 공개 인정, 운동성/비운동성 여부 미공개, Golden Dome·AMTI 동시 언급은 모두 기존 기준선에 포함된다.
    return True


_RAW_RSS = base.fetch_rss
_RAW_HTML = base.fetch_html_source


def fetch_rss_v2(source):
    items = _RAW_RSS(source)
    out = {}
    for url, item in items.items():
        if _known_meink_recap(item):
            print(f"[SPACE DEDUPE] 2026-09-14 Meink 기준선 재기사 제외: {item.get('title','')}")
            continue
        out[url] = item
    return out


def fetch_html_source_v2(source, stage):
    items = _RAW_HTML(source, stage)
    out = {}
    for url, item in items.items():
        if _known_meink_recap(item):
            print(f"[SPACE DEDUPE] 2026-09-14 Meink 기준선 재게시 제외: {item.get('title','')}")
            continue
        out[url] = item
    return out


base.fetch_rss = fetch_rss_v2
base.fetch_html_source = fetch_html_source_v2


if __name__ == "__main__":
    base.main()
