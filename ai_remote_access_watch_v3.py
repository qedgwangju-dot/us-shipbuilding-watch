import re

import ai_remote_access_watch as base
import ai_remote_access_watch_v2 as v2


# Google News RSS 제목 끝에 자동으로 붙는 "- Forbes", "- forbes.com", "- Reuters" 같은
# 출처 꼬리표는 본문 제목에서 제거한다. 출처는 아래의 클릭 가능한 '출처:' 줄에만 표시한다.
def strip_source_suffix(title: str, source_name: str = "") -> str:
    title = (title or "").strip()
    source_name = (source_name or "").strip()
    if not title:
        return title

    # Google News는 보통 '기사 제목 - 매체명' 형식이다.
    m = re.match(r"^(.*?)(?:\s[-–—]\s)([^-–—]{2,80})$", title)
    if not m:
        return title

    body = m.group(1).strip()
    suffix = m.group(2).strip()
    suffix_low = suffix.lower().removeprefix("www.")
    src_low = source_name.lower().removeprefix("www.")

    # RSS <source>가 Forbes인데 제목 꼬리는 forbes.com처럼 도메인일 수 있다.
    src_root = src_low.split(".")[0]
    suffix_root = suffix_low.split(".")[0]
    same_source = bool(src_low) and (
        suffix_low == src_low
        or suffix_low.startswith(src_low + ".")
        or src_low.startswith(suffix_low + ".")
        or (src_root and src_root == suffix_root)
    )

    # 출처명이 비어 있어도 명백한 도메인 꼬리표는 제거한다.
    looks_like_domain = bool(re.fullmatch(r"(?:www\.)?[a-z0-9][a-z0-9.-]*\.[a-z]{2,}", suffix_low))

    return body if same_source or looks_like_domain else title


_original_fetch_news_rss = base.fetch_news_rss


def fetch_news_rss(source):
    items = _original_fetch_news_rss(source)
    for item in items.values():
        source_label = (item.get("source") or "").split(" · ")[-1].strip()
        item["title"] = strip_source_suffix(item.get("title", ""), source_label)
    return items


_original_korean_title = base.korean_title


def korean_title(raw_title: str) -> str:
    clean_title = strip_source_suffix(raw_title)
    low = clean_title.lower()

    # Forbes 2026-08-31 기사: 기계번역 대신 의미가 바로 보이는 한국어 제목으로 고정.
    if "the u.s. tried to keep ai chips from china" in low and "cloud created a loophole" in low:
        return "미국의 대중 AI 칩 수출통제, 해외 클라우드 원격접근이 허점으로 부상"

    return _original_korean_title(clean_title)


base.fetch_news_rss = fetch_news_rss
base.korean_title = korean_title


if __name__ == "__main__":
    base.build_message = v2.build_message
    base.main()
