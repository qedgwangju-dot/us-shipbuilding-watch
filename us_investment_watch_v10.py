#!/usr/bin/env python3
from __future__ import annotations

import datetime as dt
import email.utils
import hashlib
import xml.etree.ElementTree as ET

import us_investment_watch as base
import us_investment_watch_v9 as v9

# v10: 내용 기준 감시를 완성한다.
# 비슷한 제목의 기사라도 서로 다른 숫자·조건·일정을 담을 수 있으므로
# 제목 유사도 중복 제거를 하지 않는다. 동일 RSS 항목 ID만 현재 실행 안에서 제거한다.


def raw_rss_items(now: dt.datetime) -> list[dict]:
    rows: list[dict] = []
    exact_seen: set[str] = set()
    for query in base.QUERIES:
        try:
            root = ET.fromstring(base.fetch(base.google_news_url(query)))
        except Exception:
            continue
        for item in root.findall('.//item'):
            raw_title = base.clean(item.findtext('title') or '')
            source = base.clean(item.findtext('source') or '')
            if not source and ' - ' in raw_title:
                raw_title, source = raw_title.rsplit(' - ', 1)
            link = base.clean(item.findtext('link') or '')
            description = base.clean(item.findtext('description') or '')
            pub = item.findtext('pubDate') or ''
            try:
                published = email.utils.parsedate_to_datetime(pub)
                if published.tzinfo is None:
                    published = published.replace(tzinfo=base.UTC)
            except Exception:
                published = now
            if (now - published.astimezone(base.UTC)).total_seconds() > base.MAX_AGE_HOURS * 3600:
                continue
            blob = f'{raw_title} {description} {source}'
            if not any(x.lower() in blob.lower() for x in base.TRUSTED):
                continue
            if not any(x.lower() in blob.lower() for x in base.MATERIAL):
                continue
            if any(x.lower() in raw_title.lower() for x in base.MARKET_ONLY) and not any(x.lower() in blob.lower() for x in base.HARD_FACT):
                continue
            key = hashlib.sha256(f'{raw_title}|{link}'.encode()).hexdigest()[:24]
            if key in exact_seen:
                continue
            exact_seen.add(key)
            rows.append({
                'id': key,
                'title': raw_title.strip(),
                'description': description,
                'source': source.strip() or '신뢰자료',
                'link': link,
                'published': published.astimezone(base.UTC).isoformat(),
                'tags': base.tags_for(raw_title, description),
            })
    rows.sort(key=lambda x: x['published'], reverse=True)
    return rows


base.rss_items = raw_rss_items


def main() -> int:
    return v9.main()


if __name__ == '__main__':
    raise SystemExit(main())
