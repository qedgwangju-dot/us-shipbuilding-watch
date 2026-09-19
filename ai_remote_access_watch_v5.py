from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

import ai_remote_access_watch as base
import ai_remote_access_watch_v4 as v4


# Google News가 오래된 기사를 검색결과에 새로 노출해도 신규 정책 변화로 알리지 않는다.
# 뉴스/보도는 RSS 원문 공개시각 기준 최근 7일만 신규 후보로 허용한다.
MAX_NEWS_AGE_DAYS = 7

_original_fetch_news_rss = base.fetch_news_rss


def _is_fresh_rss(item) -> bool:
    raw = (item.get("rss_pub_date") or "").strip()
    if not raw:
        # 날짜가 없는 보도는 신규성 확인이 불가능하므로 보수적으로 차단한다.
        return False
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        age = now - dt.astimezone(timezone.utc)
        return timedelta(days=-1) <= age <= timedelta(days=MAX_NEWS_AGE_DAYS)
    except Exception:
        return False


def fetch_news_rss_fresh(source):
    items = _original_fetch_news_rss(source)
    out = {}
    for url, item in items.items():
        if _is_fresh_rss(item):
            out[url] = item
        else:
            print(
                f"[AI STALE] 과거 보도 제외: {item.get('rss_pub_date','')} | "
                f"{item.get('title','')}"
            )
    return out


base.fetch_news_rss = fetch_news_rss_fresh


if __name__ == "__main__":
    v4.main()
