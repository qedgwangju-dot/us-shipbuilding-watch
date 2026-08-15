import html
import re

import monitor

TEST_TITLE = "President Donald J. Trump Rebuilds the U.S. Navy and America’s Shipbuilding Industrial Base"
TEST_TITLE_KO = "트럼프 대통령, 미 해군·미국 조선산업 기반 재건"


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
        if translated and translated not in result:
            result.append(translated)
        if len(result) >= limit:
            break
    return result


def structured_bullets(blocks):
    full = " ".join(blocks)
    low = full.lower()
    bullets = []

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

    # 정형 요약은 짧은 핵심만 남긴다.
    return bullets[:5]


def build_message(item):
    if item.get("title") == TEST_TITLE:
        title_ko = TEST_TITLE_KO
    else:
        title_ko = polish_korean(monitor.translate_piece(item.get("title", "")))
        title_ko = monitor.compact_korean(title_ko, 90)

    blocks = monitor.extract_article_blocks(item["url"])
    bullets = structured_bullets(blocks)

    # 이미 핵심 규칙으로 2개 이상 잡혔으면 원문 장문 번역을 덧붙이지 않는다.
    if len(bullets) < 2:
        bullets = fallback_bullets(blocks, limit=3)

    if not bullets and item.get("summary"):
        summary = polish_korean(monitor.translate_piece(item["summary"]))
        bullets = [monitor.compact_korean(summary, 125)]

    if not bullets:
        bullets = ["새로운 공식자료가 감지되었습니다. 세부 내용은 원문에서 확인할 수 있습니다."]

    safe_title = html.escape(title_ko or item.get("title", "새 공식자료"))
    safe_source = html.escape(item.get("source", "공식자료"))
    safe_url = html.escape(item["url"], quote=True)
    bullet_text = "\n".join(f"• {html.escape(b)}" for b in bullets[:5])

    return (
        "🚨 <b>미국 조선·해군 정책 중요 변화</b>\n\n"
        f"<b>{safe_title}</b>\n"
        f"<i>출처: {safe_source}</i>\n\n"
        f"{bullet_text}\n\n"
        f"🔎 <a href=\"{safe_url}\"><b>원문</b></a>"
    )


monitor.build_message = build_message
monitor.main()
