from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime

import space_control_golden_dome_watch as base
import space_control_golden_dome_watch_v3  # patches baseline-event dedupe onto base


# RSS 재노출·색인 갱신으로 과거 기사가 새 변화처럼 들어오는 것을 차단한다.
MAX_RSS_AGE_DAYS = 4

# 미국 우주통제·Golden Dome 투자 판단을 실제로 바꾸는 변화만 허용한다.
MATERIAL_EVENT_TERMS = [
    "contract awarded", "contract award", "task order", "award value", "selected", "selection",
    "prototype", "flight test", "flight-ready", "flight ready", "on-orbit test", "orbital test",
    "launch", "launched", "deployment", "deployed", "operational", "initial operational capability",
    "gate 2", "gate two", "gate 3", "gate three", "gate 4", "gate four",
    "manufacturer", "contractor", "built by", "system name", "program name", "designation",
    "quantity", "number of systems", "number of weapons", "satellite count", "unit cost",
    "budget", "appropriation", "funding", "billion", "million", "$",
    "space-based amti", "airborne moving target", "space-based interceptor",
]

# 단순 후속 반응·논평은 미국 프로그램의 새 상태 변화가 아니므로 제외한다.
REACTION_ONLY_TERMS = [
    "warns u.s.", "warns us", "arms race fears", "reaction to", "responds to revelation",
    "concern over", "criticism of", "commentary", "opinion",
]


def _published_is_fresh(item) -> bool:
    raw = (item.get("published_date") or "").strip()
    if not raw:
        # 공식 HTML 소스는 본문에서 날짜를 다시 읽으므로 여기서는 허용한다.
        return True
    dt = None
    for fmt in ("%Y-%m-%d %H:%M KST", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(raw, fmt)
            break
        except Exception:
            pass
    if dt is None:
        try:
            dt = parsedate_to_datetime(raw).replace(tzinfo=None)
        except Exception:
            return True
    return dt >= datetime.now() - timedelta(days=MAX_RSS_AGE_DAYS)


def _material(item) -> bool:
    enriched = base.enrich(dict(item))
    text = f"{enriched.get('title','')} {enriched.get('summary','')} {enriched.get('article_text','')}".lower()
    if any(x in text for x in REACTION_ONLY_TERMS) and not any(x in text for x in ["contract awarded", "task order", "selected", "launch", "deployment"]):
        return False
    return any(x in text for x in MATERIAL_EVENT_TERMS)


_RAW_RSS = base.fetch_rss


def fetch_rss_v4(source):
    items = _RAW_RSS(source)
    out = {}
    for url, item in items.items():
        if not _published_is_fresh(item):
            print(f"[SPACE STALE] 과거 기사 제외: {item.get('published_date','')} | {item.get('title','')}")
            continue
        if not _material(item):
            print(f"[SPACE LOW-VALUE] 실질 상태변화 없는 재기사·반응 제외: {item.get('title','')}")
            continue
        out[url] = item
    return out


base.fetch_rss = fetch_rss_v4


if __name__ == "__main__":
    base.main()
