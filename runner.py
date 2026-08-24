import html
import re

import monitor

TEST_TITLE = "President Donald J. Trump Rebuilds the U.S. Navy and America’s Shipbuilding Industrial Base"
TEST_TITLE_KO = "트럼프 대통령, 미 해군·미국 조선산업 기반 재건"

TITLE_OVERRIDES = {
    "Acting SECNAV Appoints Andrew Magliochetti to Lead Defense Industrial Base Revitalization": "미 해군장관 대행, 방위산업 기반 재활성화 책임자로 Andrew Magliochetti 임명",
    "Acting SECNAV Appoints Andrew Magliochetti to Lead Defense Industrial Base Revitalization…": "미 해군장관 대행, 방위산업 기반 재활성화 책임자로 Andrew Magliochetti 임명",
}


def polish_korean(text: str) -> str:
    text = " ".join((text or "").split())
    replacements = {
        "도널드 J. 트럼프 대통령": "트럼프 대통령",
        "미 해군과 미국의 조선 산업 기지를 재건하다": "미 해군·미국 조선산업 기반 재건",
        "미 해군과 미국 조선 산업 기반을 재건하다": "미 해군·미국 조선산업 기반 재건",
        "미국의 조선 산업 기반": "미국 조선산업 기반",
        "조선 산업 기지": "조선산업 기반",
        "조선 산업 기반": "조선산업 기반",
        "전 미국 인력": "미국인 인력",
        "미국 전역의 인력": "미국인 인력",
        "모 조선소": "모(母)조선소",
        "교량 간격": "함정 인도 공백",
        "교량 격차": "함정 인도 공백",
        "빠른 처리": "신속 인도",
        "활성화된 미국 조선소": "재활성화된 미국 조선소",
        "다섯 번째 해군 조선소": "제5 해군 공창",
        "해군 해상 시스템 사령부": "해군 해상체계사령부(NAVSEA)",
        "해군 해상 체계 사령부": "해군 해상체계사령부(NAVSEA)",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text.strip()


def split_candidates(blocks):
    out = []
    seen = set()
    for block in blocks:
        for part in re.split(r"(?<=[.!?])\s+|;\s+", block):
            part = " ".join(part.split()).strip()
            if not 28 <= len(part) <= 700:
                continue

            # 원문 페이지의 URL·검색·게시시각 같은 메타 정보는 요약 bullet에 넣지 않는다.
            low = part.lower()
            if (
                "http://" in low
                or "https://" in low
                or low.startswith("url source")
                or low.startswith("source url")
                or "site search" in low
                or low.startswith("search:")
                or low.startswith("posted time")
                or low.startswith("published time")
                or low.startswith("publish date")
                or low.startswith("title:")
                or low.startswith("date:")
                or low.startswith("source:")
                or low.startswith("byline:")
            ):
                continue

            key = re.sub(r"\W+", " ", part.lower()).strip()
            if key and key not in seen:
                seen.add(key)
                out.append(part)
    return out


def token_overlap(a: str, b: str) -> float:
    aa = set(re.findall(r"[a-z0-9]+", a.lower()))
    bb = set(re.findall(r"[a-z0-9]+", b.lower()))
    if not aa or not bb:
        return 0.0
    return len(aa & bb) / max(1, min(len(aa), len(bb)))


def score_candidate(text: str, idx: int) -> int:
    low = text.lower()
    weights = {
        "foreign shipbuilder": 12,
        "up to two ships": 14,
        "two ships": 10,
        "parent shipyard": 12,
        "additional ships will be built": 11,
        "naval shipyard": 9,
        "fifth": 7,
        "navsea": 10,
        "reorgan": 8,
        "reform": 7,
        "national security presidential memorandum": 9,
        "ship repair": 6,
        "shipbuilding": 5,
        "submarine": 5,
        "aircraft carrier": 5,
        "icebreaker": 5,
        "investment": 4,
        "contract": 4,
        "award": 4,
    }
    score = sum(weight for term, weight in weights.items() if term in low)
    if re.search(r"\$|\b\d+(?:\.\d+)?%?\b", text):
        score += 3
    if idx < 20:
        score += 2
    return score


def fallback_bullets(blocks, limit=3):
    """정형 규칙으로 잡히지 않는 일반 자료만 짧게 보조 요약한다."""
    candidates = split_candidates(blocks)
    ranked = sorted(
        ((score_candidate(text, idx), idx, text) for idx, text in enumerate(candidates)),
        key=lambda x: (-x[0], x[1]),
    )
    chosen_en = []
    result = []
    for score, _, text in ranked:
        if score <= 0:
            continue
        if any(token_overlap(text, other) >= 0.55 for other in chosen_en):
            continue
        chosen_en.append(text)
        translated = polish_korean(monitor.translate_piece(text))
        translated = monitor.compact_korean(translated, 125)
        if translated and monitor.has_korean(translated) and translated not in result:
            result.append(translated)
        if len(result) >= limit:
            break
    return result


def structured_bullets(blocks):
    full = " ".join(blocks)
    low = full.lower()
    bullets = []

    if "andrew magliochetti" in low and "defense industrial base" in low:
        bullets.append("미 해군장관 대행 Hung Cao가 Andrew Magliochetti를 방위산업 기반 재활성화 신규 이니셔티브 책임자로 임명")
        if "25 years" in low and any(term in low for term in ["structured finance", "investment banking", "private equity"]):
            bullets.append("Magliochetti는 구조화금융·투자은행·사모펀드 등 민간 부문 경력 25년 보유")
        if "venture capital" in low and "national security" in low:
            bullets.append("최근에는 국가안보 기술 중심 벤처캐피털의 공동창업자·운용 파트너로 활동")

    if "national security presidential memorandum" in low and ("shipbuilding" in low or "ship repair" in low):
        bullets.append("미 해군 함정 건조·수리 프로그램의 지연·비용 문제 해결을 위한 국가안보 대통령 각서에 서명")

    if "foreign shipbuilders" in low and ("up to two ships" in low or "two ships" in low):
        bullets.append("미국 조선소에 지속 투자하고 미국인 인력을 훈련하는 외국 조선업체는 모(母)조선소에서 최대 2척까지 임시 건조 가능")

    if "additional ships will be built" in low and "american shipyards" in low:
        bullets.append("추가 함정은 재활성화된 미국 조선소에서 건조")

    if "fifth naval shipyard" in low or ("fifth" in low and "naval shipyard" in low):
        bullets.append("제5 해군 공창 신설로 잠수함·항공모함 수리 능력 확대")

    if "navsea" in low and any(term in low for term in ["reorgan", "reform", "overhaul", "review"]):
        bullets.append("해군 해상체계사령부(NAVSEA) 조직 개편 추진")

    return bullets[:5]


def build_message(item):
    raw_title = item.get("title", "")
    if raw_title == TEST_TITLE:
        title_ko = TEST_TITLE_KO
    elif raw_title in TITLE_OVERRIDES:
        title_ko = TITLE_OVERRIDES[raw_title]
    else:
        title_ko = polish_korean(monitor.translate_piece(raw_title))
        title_ko = monitor.compact_korean(title_ko, 90)
    if not monitor.has_korean(title_ko):
        title_ko = "미국 조선·해군 관련 새 공식 발표"

    blocks = monitor.extract_article_blocks(item["url"])
    bullets = structured_bullets(blocks)

    if len(bullets) < 2:
        bullets = fallback_bullets(blocks, limit=3)

    if not bullets and item.get("summary"):
        summary = polish_korean(monitor.translate_piece(item["summary"]))
        if summary and monitor.has_korean(summary):
            bullets = [monitor.compact_korean(summary, 125)]

    if not bullets:
        bullets = ["새로운 공식자료가 감지되었습니다. 세부 내용은 원문에서 확인할 수 있습니다."]

    safe_title = html.escape(title_ko or "미국 조선·해군 관련 새 공식 발표")
    safe_source = html.escape(item.get("source", "공식자료"))
    safe_url = html.escape(item["url"], quote=True)
    bullet_text = "\n".join(f"• {html.escape(b)}" for b in bullets[:5])

    return (
        "🚨 <b>미국 조선·해군 정책 중요 변화</b>\n\n"
        f"<b>{safe_title}</b>\n"
        f"출처: <a href=\"{safe_url}\">{safe_source}</a>\n\n"
        f"{bullet_text}\n\n"
        f"🔎 <a href=\"{safe_url}\"><b>원문</b></a>"
    )


monitor.build_message = build_message
monitor.main()
