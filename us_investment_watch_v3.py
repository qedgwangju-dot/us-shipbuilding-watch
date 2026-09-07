#!/usr/bin/env python3
from __future__ import annotations

import datetime as dt
import hashlib
import html
import json
import re
import urllib.parse
from pathlib import Path

import us_investment_watch as base

# v3: fast, high-recall monitoring + source-grounded interpretation.
# Every alert now tries to fetch the original article body first. The alert separates:
# 1) what the linked article actually says, 2) what that means, 3) what is still unconfirmed.

for source in [
    "머니투데이", "서울경제", "조선비즈", "아시아경제", "파이낸셜뉴스", "매일경제", "한국경제",
]:
    if source not in base.TRUSTED:
        base.TRUSTED.append(source)

for term in [
    "최종 사인", "최종 서명", "최종서명", "원전 8기", "대형원전", "포괄적 프레임워크",
    "운영위원회", "국회 동의", "국회 보고", "최종 합의", "최종합의", "사인", "서명",
]:
    if term not in base.MATERIAL:
        base.MATERIAL.append(term)

EXTRA_QUERIES = [
    '"대미투자" when:1d',
    '"한미전략투자" when:1d',
    '"대미투자" "최종 사인" OR "최종 서명" OR "최종합의" when:2d',
    '"대미투자" "원전 8기" OR "대형원전" OR "포괄적 프레임워크" when:2d',
]
for query in reversed(EXTRA_QUERIES):
    if query not in base.QUERIES:
        base.QUERIES.insert(0, query)

MT_ECONOMY_URL = "https://www.mt.co.kr/economy"

# Re-issue once with full-text grounding after the earlier headline-only alert was too vague.
PINNED = {
    "id": "mt-20260907-us-investment-nuclear-8-final-sign-grounded-v2",
    "title": "[단독]대미투자 '원전 8기' 포함…이달 18일 최종 사인 - 머니투데이",
    "description": "머니투데이 원문 본문을 직접 읽고 핵심 사실과 해설을 분리해 재전송",
    "source": "머니투데이",
    "link": "https://mt.co.kr/economy/2026/09/07/2026090714365254729",
    "published": "2026-09-07T14:36:00+09:00",
    "tags": ["원전", "최종합의"],
}


def strip_html(value: str) -> str:
    value = re.sub(r"<script\b[^>]*>.*?</script>", " ", value or "", flags=re.I | re.S)
    value = re.sub(r"<style\b[^>]*>.*?</style>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<[^>]+>", " ", value)
    return base.clean(value)


def extract_jsonld_article_body(raw: str) -> str:
    for match in re.finditer(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', raw, flags=re.I | re.S):
        blob = html.unescape(match.group(1)).strip()
        try:
            data = json.loads(blob)
        except Exception:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            obj = stack.pop()
            if isinstance(obj, list):
                stack.extend(obj)
                continue
            if not isinstance(obj, dict):
                continue
            body = obj.get("articleBody")
            if isinstance(body, str) and len(body.strip()) >= 80:
                return base.clean(body)
            for key in ("@graph", "mainEntity", "itemListElement"):
                child = obj.get(key)
                if isinstance(child, (dict, list)):
                    stack.append(child)
    return ""


def extract_meta(raw: str, names: list[str]) -> str:
    for name in names:
        patterns = [
            rf'<meta[^>]+(?:property|name)=["\']{re.escape(name)}["\'][^>]+content=["\']([^"\']+)["\']',
            rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']{re.escape(name)}["\']',
        ]
        for pattern in patterns:
            m = re.search(pattern, raw, flags=re.I | re.S)
            if m:
                return base.clean(html.unescape(m.group(1)))
    return ""


def extract_article_body(raw: str) -> str:
    body = extract_jsonld_article_body(raw)
    if body:
        return body

    # Prefer common article containers and paragraph text.
    candidates: list[str] = []
    for pattern in [
        r'<article\b[^>]*>(.*?)</article>',
        r'<div[^>]+class=["\'][^"\']*(?:article|news|view)[^"\']*(?:body|content|text)[^"\']*["\'][^>]*>(.*?)</div>',
        r'<div[^>]+id=["\'][^"\']*(?:article|news|view)[^"\']*(?:body|content|text)[^"\']*["\'][^>]*>(.*?)</div>',
    ]:
        for m in re.finditer(pattern, raw, flags=re.I | re.S):
            txt = strip_html(m.group(1))
            if len(txt) >= 120:
                candidates.append(txt)
    if candidates:
        return max(candidates, key=len)

    paragraphs = []
    for m in re.finditer(r'<p\b[^>]*>(.*?)</p>', raw, flags=re.I | re.S):
        txt = strip_html(m.group(1))
        if len(txt) >= 20:
            paragraphs.append(txt)
    if paragraphs:
        joined = " ".join(paragraphs)
        if len(joined) >= 120:
            return joined

    return extract_meta(raw, ["description", "og:description", "twitter:description"])


def fetch_original(row: dict) -> tuple[str, str]:
    """Return original article text and extraction status. Never fabricate missing body text."""
    link = str(row.get("link") or "").strip()
    if not link:
        return "", "원문 링크 없음"
    try:
        raw = base.fetch(link).decode("utf-8", errors="ignore")
    except Exception as exc:
        return "", f"원문 직접 열람 실패: {type(exc).__name__}"
    text = extract_article_body(raw)
    if len(text) < 80:
        return "", "원문 본문 추출 실패"
    return text, "원문 본문 직접 열람"


def split_sentences(text: str) -> list[str]:
    if not text:
        return []
    text = re.sub(r"\s+", " ", text).strip()
    chunks = re.split(r'(?<=[.!?。]|다\.|했다\.|됐다\.|밝혔다\.|전했다\.)\s+', text)
    out: list[str] = []
    for chunk in chunks:
        chunk = base.clean(chunk)
        if 18 <= len(chunk) <= 500:
            out.append(chunk)
    if len(out) <= 1:
        # Korean news HTML often loses punctuation boundaries; use sentence endings as fallback.
        chunks = re.split(r'(?<=다\.)|(?<=했다\.)|(?<=됐다\.)|(?<=밝혔다\.)|(?<=전했다\.)', text)
        out = [base.clean(x) for x in chunks if 18 <= len(base.clean(x)) <= 500]
    return out


def material_score(sentence: str) -> int:
    low = sentence.lower()
    score = 0
    for term in base.MATERIAL:
        if term.lower() in low:
            score += 3
    for term in [
        "원전", "엔시날", "가스", "lng", "i-spv", "spv", "투자", "총사업비", "사업비",
        "18일", "8기", "노형", "웨스팅하우스", "한수원", "한국전력", "지분", "운영권", "ppa",
        "국회", "운영위원회", "사업관리위원회", "미국", "한국", "서명", "사인",
    ]:
        if term in low:
            score += 2
    if re.search(r'\d', sentence):
        score += 2
    return score


def select_original_facts(text: str, limit: int = 7) -> list[str]:
    sentences = split_sentences(text)
    ranked = sorted(enumerate(sentences), key=lambda x: (material_score(x[1]), -x[0]), reverse=True)
    picked_idx: list[int] = []
    for idx, sent in ranked:
        if material_score(sent) < 3:
            continue
        if any(base.similarity(sent, sentences[old]) >= 0.75 for old in picked_idx):
            continue
        picked_idx.append(idx)
        if len(picked_idx) >= limit:
            break
    picked_idx.sort()
    return [sentences[i] for i in picked_idx]


def article_specific_explanation(text: str, row: dict) -> list[str]:
    """Explain only distinctions supported by the linked article text; avoid adding project details not in source."""
    low = text.lower()
    lines: list[str] = []

    if "원전" in low and re.search(r'8\s*기', text):
        lines.append("'원전 8기'는 숫자 자체가 핵심입니다. 다만 원문이 '대미투자 대상·협의안에 포함'이라고 쓴 것과 8기 각각의 투자승인·착공·EPC 본계약이 확정됐다는 것은 서로 다른 단계입니다.")

    if re.search(r'9\s*월\s*18\s*일|이달\s*18\s*일|18\s*일', text) and any(x in low for x in ["사인", "서명", "합의"]):
        lines.append("'18일'은 기사에서 제시한 최종 서명·합의 시간표로 읽어야 합니다. 오늘 운영위원회 의결 시점, 1호 사업 발표 시점, 실제 자금 집행·착공 시점과 동일한 날짜로 합치면 안 됩니다.")

    if "엔시날" in low and "원전" in low:
        lines.append("원문 안에서 엔시날과 원전이 각각 별도 사업·안건으로 서술되는지 먼저 봐야 합니다. '엔시날 1호'와 '원전 포함 대미투자 전체 패키지'를 같은 사업으로 합쳐 해석하지 않습니다.")

    if "2000억" in text or "2,000억" in text:
        lines.append("2,000억달러는 전략산업 대미투자의 전체 약정 한도와 개별 프로젝트 사업비를 구분해야 합니다. 기사에 개별 원전 투자액이 따로 없으면 2,000억달러 전부를 원전 8기에 배정된 금액으로 계산하지 않습니다.")

    if any(x in low for x in ["노형", "ap1000", "apr1400", "웨스팅하우스"]):
        lines.append("노형·웨스팅하우스가 원문에 직접 등장하면 원전 8기의 주도권·기자재 배분을 가르는 핵심 조건입니다. 반대로 원문에 없으면 알림이 임의로 AP1000·APR1400을 확정 후보처럼 추가하지 않습니다.")

    if any(x in low for x in ["한수원", "한국수력원자력", "지분", "운영권"]):
        lines.append("한수원 지분·운영권이 본문에 명시되면 한국이 단순 금융제공자인지, 사업자·운영자로도 참여하는지를 구분할 수 있습니다. 본문에 없으면 '한수원 운영권'을 확인된 조건으로 쓰지 않습니다.")

    if not lines:
        lines.append("해설은 원문에서 확인된 사업 범위·금액·물량·날짜·당사자만 연결합니다. 원문에 없는 노형·지분·운영권·수주기업은 추정으로 채우지 않습니다.")
    return lines[:5]


def direct_mt_items(now: dt.datetime) -> list[dict]:
    """Scan MoneyToday economy page directly so fresh exclusives do not wait for Google News indexing."""
    rows: list[dict] = []
    try:
        raw = base.fetch(MT_ECONOMY_URL).decode("utf-8", errors="ignore")
    except Exception:
        return rows

    for match in re.finditer(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', raw, flags=re.I | re.S):
        href = html.unescape(match.group(1)).strip()
        title = base.clean(match.group(2))
        if not title or "대미투자" not in title:
            continue
        if not any(term.lower() in title.lower() for term in base.MATERIAL):
            continue
        if href.startswith("/"):
            href = urllib.parse.urljoin("https://www.mt.co.kr", href)
        if not href.startswith("http"):
            continue
        key = hashlib.sha256(f"MT|{title}|{href}".encode("utf-8")).hexdigest()[:24]
        rows.append({
            "id": key,
            "title": title if "머니투데이" in title else f"{title} - 머니투데이",
            "description": "머니투데이 경제면 직접 탐지",
            "source": "머니투데이",
            "link": href,
            "published": now.isoformat(),
            "tags": base.tags_for(title, "머니투데이 경제면 직접 탐지"),
        })
    return rows


def title_similarity(a: str, b: str) -> float:
    return base.similarity(a, b)


def collect(now: dt.datetime) -> list[dict]:
    items: list[dict] = []
    items.extend(direct_mt_items(now))
    items.extend(base.rss_items(now))

    unique: list[dict] = []
    for row in items:
        if any(row["id"] == old["id"] for old in unique):
            continue
        if any(title_similarity(row["title"], old["title"]) >= 0.70 for old in unique):
            continue
        unique.append(row)
    return unique


def alert_message(now: dt.datetime, rows: list[dict], usdkrw: float, fx_source: str) -> str:
    parts = ["<b>🇺🇸 대미투자 | 중요 업데이트</b>", ""]
    combined_tags: list[str] = []

    for index, row in enumerate(rows[:5], 1):
        tags = list(row.get("tags") or ["대미투자"])
        combined_tags.extend(tags)
        title_link = f'<a href="{html.escape(row["link"], quote=True)}"><b>{html.escape(row["title"])}</b></a>'
        parts.append(f"{index}. {title_link}")

        original_text, original_status = fetch_original(row)
        source_text = original_text or f"{row['title']} {row.get('description', '')}"

        parts.append(f"• 원문 확인: <b>{html.escape(original_status)}</b>")
        facts = select_original_facts(original_text, 7) if original_text else []
        if facts:
            parts.append("<b>• 원문에서 직접 확인된 핵심</b>")
            for fact in facts:
                # Paraphrase by clipping the source sentence; do not reproduce long article passages.
                clipped = fact if len(fact) <= 180 else fact[:177].rstrip() + "…"
                parts.append("  - " + html.escape(clipped))
        else:
            parts.append("• 원문에서 직접 확인된 핵심: 본문을 열지 못한 경우 제목 이상으로 확대해석하지 않음")

        conversions = base.extract_usd_conversions(source_text, usdkrw)
        if conversions:
            parts.append("• 원화 환산: " + " · ".join(html.escape(x) for x in conversions[:6]))
        for check in base.numeric_checks(source_text, usdkrw)[:3]:
            parts.append(f"• 숫자 검산: {html.escape(check)}")

        if original_text:
            parts.append("<b>• 해설</b>")
            for line in article_specific_explanation(original_text, row):
                parts.append("  - " + html.escape(line))
        else:
            parts.append("• 해설: 원문 본문을 확보하기 전에는 '원전 8기=확정 착공', '18일=실제 자금집행일'처럼 단정하지 않음")

        parts.append(f"• 구분: <b>{html.escape(' / '.join(tags[:3]))}</b>")
        parts.append("• 확정도: 기사 보도와 정부·회사 공식 확정을 분리. 공식 발표 전에는 보도 단계로 표시")
        parts.append("")

    parts.extend(["<b>공식 투자 틀</b>"] + base.official_structure_lines(usdkrw))
    parts.extend([
        "",
        "<b>재평가 순서</b>",
        "① 원문 사업범위·당사자·금액·물량·날짜 → ② 공식 확정 → ③ 한국 실제 출자액·지분율 → ④ I-SPV 의결권·손실분담",
        "⑤ PPA/장기 구매계약 → ⑥ 한국 기업 본계약·물량×단가 → ⑦ 착공·전원 인가/FID → ⑧ 장기 유지보수·반복매출",
        "",
        "<b>최대 역풍</b>",
    ])

    risk, early = base.risk_summary(combined_tags)
    if PINNED["id"] in {row["id"] for row in rows}:
        risk = "원전 8기가 협의안에 포함돼도 노형·사업비·EPC 배분·지분·PPA·인허가가 잠기지 않으면 실제 한국 기업 매출은 지연되거나 축소될 수 있음"
        early = "9월 18일 서명문 원문·원전 8기 사업명/노형·개별 사업비·한국 기업 본계약"
    parts.extend([
        f"• {html.escape(risk)}",
        f"• 먼저 볼 지표: <b>{html.escape(early)}</b>",
        "",
        f'<a href="{base.MOU_OFFICIAL_URL}"><b>산업통상부 한미 전략투자 MOU</b></a>',
        base.footer(now, usdkrw, fx_source, "원문 본문 직접 확인 우선 · 같은 사건 반복 기사와 단순 주가 반응 제외"),
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

    fresh: list[dict] = []

    # Send the corrected, full-text-grounded version once.
    if PINNED["id"] not in seen:
        fresh.append(PINNED)
        seen[PINNED["id"]] = now.isoformat()
        recent.append(PINNED["title"])
    else:
        for row in collect(now):
            if row["id"] in seen:
                continue
            seen[row["id"]] = now.isoformat()
            if any(title_similarity(row["title"], old) >= 0.70 for old in recent):
                continue
            fresh.append(row)
            recent.append(row["title"])
            if len(fresh) >= 5:
                break

    state["recent_titles"] = recent[-100:]
    state["last_checked_at"] = now.astimezone(base.KST).isoformat(timespec="seconds")
    state["last_usdkrw"] = usdkrw
    state["last_fx_source"] = fx_source

    if fresh:
        base.ALERT.write_text(alert_message(now, fresh, usdkrw, fx_source) + "\n", encoding="utf-8")
        state["last_alert_at"] = now.astimezone(base.KST).isoformat(timespec="seconds")
        print(f"new_alerts={len(fresh)}")
    else:
        print("new_alerts=0")

    base.STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
