#!/usr/bin/env python3
from __future__ import annotations

import datetime as dt
import hashlib
import html
import json
import re
import urllib.parse

import us_investment_watch as base
import us_investment_watch_v3 as v3
import us_investment_watch_v4 as v4

# v5 rule: FAIL CLOSED.
# A headline is NOT enough for Telegram. We alert only after we can open a real
# publisher/Naver article body and extract enough source text to explain it.
# If the body is inaccessible, keep retrying; do not mark it seen and do not
# send a low-information alert.

SOURCE_DOMAINS = {
    "머니투데이": ["mt.co.kr"],
    "MTN 머니투데이방송": ["mtn.co.kr"],
    "아시아경제": ["asiae.co.kr"],
    "파이낸셜뉴스": ["fnnews.com"],
    "뉴시스": ["newsis.com", "mobile.newsis.com"],
    "연합뉴스": ["yna.co.kr"],
    "뉴스1": ["news1.kr"],
    "이데일리": ["edaily.co.kr"],
    "헤럴드경제": ["heraldcorp.com", "biz.heraldcorp.com"],
    "한국경제": ["hankyung.com"],
    "매일경제": ["mk.co.kr"],
    "서울경제": ["sedaily.com"],
    "조선비즈": ["chosunbiz.com"],
}

SEARCH_ENGINES = [
    "https://search.naver.com/search.naver?where=news&query={query}",
    "https://search.daum.net/search?w=news&q={query}",
]


def base_title(title: str) -> str:
    text = base.clean(title)
    text = re.sub(r"^\[(?:단독|속보|종합|포토)\]\s*", "", text)
    # Google News usually appends " - source". Remove one or repeated suffixes.
    for _ in range(2):
        if " - " in text:
            left, right = text.rsplit(" - ", 1)
            if len(right) <= 30:
                text = left.strip()
    return text.strip()


def source_domains(source: str, title: str) -> list[str]:
    blob = f"{source} {title}".lower()
    domains: list[str] = []
    for name, values in SOURCE_DOMAINS.items():
        if name.lower() in blob:
            domains.extend(values)
    return domains


def link_allowed(url: str, domains: list[str]) -> bool:
    try:
        host = urllib.parse.urlparse(url).netloc.lower()
    except Exception:
        return False
    if host.endswith("news.naver.com") or host.endswith("n.news.naver.com"):
        return True
    if not domains:
        return not any(x in host for x in ["google.com", "google.co.kr", "search.naver.com", "search.daum.net"])
    return any(host == d or host.endswith("." + d) for d in domains)


def extract_links(raw: str) -> list[str]:
    links: list[str] = []
    for m in re.finditer(r'href=["\']([^"\']+)["\']', raw, flags=re.I):
        href = html.unescape(m.group(1)).strip()
        if href.startswith("//"):
            href = "https:" + href
        if href.startswith("http"):
            links.append(href)
        elif "url=" in href:
            try:
                qs = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
                for key in ("url", "u", "target"):
                    for value in qs.get(key, []):
                        if value.startswith("http"):
                            links.append(value)
            except Exception:
                pass
    # Also catch escaped URLs in JS blobs.
    for candidate in re.findall(r'https?://[^"\'<>\\ ]+', raw):
        links.append(html.unescape(candidate).replace("\\/", "/"))
    out: list[str] = []
    seen = set()
    for link in links:
        if link not in seen:
            seen.add(link)
            out.append(link)
    return out


def strict_extract_article_body(raw: str) -> str:
    body = v3.extract_article_body(raw)
    if len(body) >= 180:
        return body

    # Naver News article body.
    patterns = [
        r'<div[^>]+id=["\']dic_area["\'][^>]*>(.*?)</div>\s*(?:<div|</article>)',
        r'<div[^>]+id=["\']articeBody["\'][^>]*>(.*?)</div>',
        r'<div[^>]+id=["\']newsEndContents["\'][^>]*>(.*?)</div>',
        r'<section[^>]+class=["\'][^"\']*article-body[^"\']*["\'][^>]*>(.*?)</section>',
    ]
    candidates: list[str] = []
    for pattern in patterns:
        for m in re.finditer(pattern, raw, flags=re.I | re.S):
            txt = v3.strip_html(m.group(1))
            if len(txt) >= 180:
                candidates.append(txt)
    return max(candidates, key=len) if candidates else ""


def title_match(article_text: str, title: str) -> bool:
    title_words = [w for w in re.sub(r"[^0-9A-Za-z가-힣]+", " ", base_title(title).lower()).split() if len(w) >= 2]
    if not title_words:
        return len(article_text) >= 180
    low = article_text.lower()
    hits = sum(1 for w in title_words if w in low)
    # Need at least 3 distinctive title words or 40% of them.
    return hits >= min(3, max(1, len(title_words))) or hits / max(1, len(title_words)) >= 0.40


def fetch_candidate(url: str, title: str) -> tuple[str, str] | None:
    try:
        raw = base.fetch(url).decode("utf-8", errors="ignore")
    except Exception:
        return None
    body = strict_extract_article_body(raw)
    if len(body) < 180 or not title_match(body, title):
        return None
    return body, url


def resolve_google_news(row: dict) -> list[str]:
    link = str(row.get("link") or "")
    if "news.google.com" not in link:
        return []
    try:
        raw = base.fetch(link).decode("utf-8", errors="ignore")
    except Exception:
        return []
    domains = source_domains(str(row.get("source") or ""), str(row.get("title") or ""))
    return [u for u in extract_links(raw) if link_allowed(u, domains)]


def search_candidates(row: dict) -> list[str]:
    title = base_title(str(row.get("title") or ""))
    source = str(row.get("source") or "").strip()
    query_text = f'"{title}" {source}'.strip()
    q = urllib.parse.quote_plus(query_text)
    domains = source_domains(source, title)
    links: list[str] = []
    for template in SEARCH_ENGINES:
        url = template.format(query=q)
        try:
            raw = base.fetch(url).decode("utf-8", errors="ignore")
        except Exception:
            continue
        for candidate in extract_links(raw):
            if link_allowed(candidate, domains):
                links.append(candidate)
    out: list[str] = []
    seen = set()
    for link in links:
        if link not in seen:
            seen.add(link)
            out.append(link)
    return out[:20]


def resolve_original(row: dict) -> tuple[str, str, str]:
    title = str(row.get("title") or "")
    original = str(row.get("link") or "").strip()
    domains = source_domains(str(row.get("source") or ""), title)

    # 1) Direct publisher/Naver link.
    if original and "news.google.com" not in original and link_allowed(original, domains):
        got = fetch_candidate(original, title)
        if got:
            body, url = got
            return body, "원문 본문 직접 열람", url

    # 2) Try to decode/follow Google News wrapper.
    for candidate in resolve_google_news(row):
        got = fetch_candidate(candidate, title)
        if got:
            body, url = got
            return body, "Google News → 원문 본문 직접 열람", url

    # 3) Exact-title Naver/Daum search → publisher/Naver article.
    for candidate in search_candidates(row):
        got = fetch_candidate(candidate, title)
        if got:
            body, url = got
            return body, "제목 재검색 → 원문 본문 직접 열람", url

    return "", "원문 본문 확보 실패 — 알림 보류", ""


def strict_rows(rows: list[dict], unresolved: dict, now: dt.datetime) -> list[dict]:
    ready: list[dict] = []
    for row in rows:
        body, status, resolved = resolve_original(row)
        if not body:
            key = row["id"]
            info = unresolved.get(key) or {"first_seen": now.isoformat(), "tries": 0, "title": row.get("title", "")}
            info["tries"] = int(info.get("tries", 0)) + 1
            info["last_try"] = now.isoformat()
            unresolved[key] = info
            print(f"source_unresolved=true tries={info['tries']} title={row.get('title','')[:80]}")
            continue
        item = dict(row)
        item["article_text"] = body
        item["fetch_status"] = status
        item["resolved_link"] = resolved
        unresolved.pop(row["id"], None)
        ready.append(item)
    return ready


def article_block(row: dict, article_text: str, fetch_status: str, usdkrw: float) -> list[str]:
    # Use the resolved real article URL as the clickable title whenever possible.
    display = dict(row)
    if row.get("resolved_link"):
        display["link"] = row["resolved_link"]
    return v4.article_block(display, article_text, fetch_status, usdkrw)


def readable_alert_message(now, rows, usdkrw: float, fx_source: str) -> str:
    # Same v4 layout, but never refetch Google wrapper; use bodies already verified by strict_rows().
    parts = ["<b>🇺🇸 대미투자 | 중요 업데이트</b>", ""]
    combined_tags: list[str] = []
    for index, row in enumerate(rows[:5], 1):
        article_text = str(row.get("article_text") or "")
        fetch_status = str(row.get("fetch_status") or "원문 본문 직접 열람")
        combined_tags.extend(list(row.get("tags") or []))
        if len(rows) > 1:
            parts.append(f"<b>{index}.</b>")
        parts.extend(article_block(row, article_text, fetch_status, usdkrw))
        if index != min(len(rows), 5):
            parts.extend(["", "────────────", ""])

    parts.extend(["", "<b>💵 공식 투자 틀</b>"])
    parts.extend(base.official_structure_lines(usdkrw))
    parts.extend([
        "",
        "<b>📈 재평가 순서</b>",
        "① 원문 확인 → ② 공식 확정 → ③ 한국 실제 출자액·지분율 → ④ I-SPV 의결권·손실분담",
        "⑤ PPA/장기 구매계약 → ⑥ 한국 기업 본계약·물량×단가 → ⑦ 착공·전원 인가/FID → ⑧ 장기 유지보수·반복매출",
        "",
        "<b>⚠️ 최대 역풍</b>",
    ])
    if any("원전" in tag for tag in combined_tags):
        parts.append("• 원전 프레임워크가 포함돼도 <b>노형·사업비·EPC 배분·지분·인허가</b>가 잠기지 않으면 한국 기업 매출은 지연·축소될 수 있습니다.")
        parts.append("• 먼저 볼 지표: <b>최종 서명문·개별 사업비·한국 기업 본계약</b>")
    else:
        risk, early = base.risk_summary(combined_tags)
        parts.append(f"• {html.escape(risk)}")
        parts.append(f"• 먼저 볼 지표: <b>{html.escape(early)}</b>")
    parts.extend([
        "",
        f'<a href="{base.MOU_OFFICIAL_URL}"><b>산업통상부 한미 전략투자 MOU</b></a>',
        base.footer(now, usdkrw, fx_source, "원문 본문 확보된 기사만 발송 · 같은 사건 반복 기사와 단순 주가 반응 제외"),
    ])
    return "\n".join(parts)


def main() -> int:
    if base.ALERT.exists():
        base.ALERT.unlink()
    state = base.load_state()
    now = dt.datetime.now(base.UTC)
    usdkrw, fx_source = base.get_usdkrw()
    seen = state.setdefault("seen", {})
    recent = list(state.get("recent_titles") or [])[-100:]
    unresolved = state.setdefault("unresolved_sources", {})

    candidates: list[dict] = []
    # Retain pinned article only if not yet delivered; it is a direct publisher URL.
    if v3.PINNED["id"] not in seen:
        candidates.append(v3.PINNED)
    for row in v3.collect(now):
        if row["id"] in seen:
            continue
        if any(v3.title_similarity(row["title"], old) >= 0.70 for old in recent):
            continue
        candidates.append(row)
        if len(candidates) >= 15:
            break

    ready = strict_rows(candidates, unresolved, now)
    fresh: list[dict] = []
    for row in ready:
        if row["id"] in seen:
            continue
        if any(v3.title_similarity(row["title"], old) >= 0.70 for old in recent):
            continue
        fresh.append(row)
        seen[row["id"]] = now.isoformat()
        recent.append(row["title"])
        if len(fresh) >= 5:
            break

    state["recent_titles"] = recent[-100:]
    state["last_checked_at"] = now.astimezone(base.KST).isoformat(timespec="seconds")
    state["last_usdkrw"] = usdkrw
    state["last_fx_source"] = fx_source

    # Keep retry metadata bounded.
    if len(unresolved) > 200:
        keep = sorted(unresolved.items(), key=lambda kv: kv[1].get("last_try", ""), reverse=True)[:200]
        state["unresolved_sources"] = dict(keep)

    if fresh:
        base.ALERT.write_text(readable_alert_message(now, fresh, usdkrw, fx_source) + "\n", encoding="utf-8")
        state["last_alert_at"] = now.astimezone(base.KST).isoformat(timespec="seconds")
        print(f"new_alerts={len(fresh)}")
    else:
        print("new_alerts=0")

    base.STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
