#!/usr/bin/env python3
import html
import re

from deep_translator import MyMemoryTranslator
import readable_energy_alert_runner as base

_original_refinery_event_key = base.refinery_event_key
_original_spr_event_key = base.spr_event_key
_original_event_label = base.event_label
_original_impact_lines = base.impact_lines
_original_next_checks = base.next_checks
_original_translate_ko = base.rw.translate_ko

# 베네수엘라 증산·글로벌 에너지기업 계약을 SPR 감시축에서도 놓치지 않도록 검색 범위를 확장한다.
for _query in (
    'Chris Wright Venezuela oil output double agreements Chevron Eni ONGC GeoPark GE Vernova',
    'Venezuela oil production more than double next few years energy company agreements',
    'Venezuela Chevron Shell BP Repsol Eni oil agreements Chris Wright',
):
    if _query not in base.sv.NEWS_QUERIES:
        base.sv.NEWS_QUERIES.append(_query)

base.sv.NEWS_TERMS = tuple(dict.fromkeys(base.sv.NEWS_TERMS + (
    'production', 'output', 'double', 'agreements', 'chevron', 'eni', 'ongc',
    'geopark', 'ge vernova', 'shell', 'bp', 'repsol', 'chris wright',
)))

# 기사 제목·본문에 영어 원문이 반쯤 남는 문제를 막기 위한 한국어 정규화 규칙.
# 식별이 필요한 약어는 허용하지만, 일반 영어 문장은 송출하지 않는다.
_ALLOWED_IDENTIFIERS = {
    'RFS', 'RIN', 'SPR', 'DOE', 'EPA', 'EIA', 'IEA', 'OPEC', 'NABEP',
    'AAA', 'WTI', 'LNG', 'API', 'ONGC', 'BP', 'GE', 'BRICS', 'SWIFT',
}

_ENGLISH_PROSE_WORDS = {
    'the', 'a', 'an', 'and', 'or', 'but', 'of', 'to', 'in', 'on', 'at', 'for',
    'from', 'with', 'without', 'as', 'by', 'is', 'are', 'was', 'were', 'be',
    'been', 'being', 'has', 'have', 'had', 'will', 'would', 'could', 'should',
    'may', 'might', 'not', 'no', 'yes', 'stop', 'halt', 'hits', 'hit', 'record',
    'high', 'highs', 'price', 'prices', 'fuel', 'diesel', 'gasoline', 'gas',
    'refinery', 'refineries', 'strike', 'strikes', 'oil', 'output', 'production',
    'including', 'incl', 'today', 'asks', 'wants', 'urges', 'says', 'said',
    'bypass', 'against', 'amid', 'after', 'before', 'over', 'under', 'into',
}

_ENTITY_REPLACEMENTS = (
    (r'도널드\s+트럼프\s*\(\s*Donald\s+Trump\s*\)', '도널드 트럼프'),
    (r'볼로디미르\s+젤렌스키\s*\(\s*Volodymyr\s+Zelensk(?:yy|iy|y|i)\s*\)', '볼로디미르 젤렌스키'),
    (r'\bPresident\s+Donald\s+Trump\b', '도널드 트럼프 대통령'),
    (r'\bDonald\s+Trump\b', '도널드 트럼프'),
    (r'\bTrump\b', '트럼프'),
    (r'\bVolodymyr\s+Zelensk(?:yy|iy|y|i)\b', '볼로디미르 젤렌스키'),
    (r'\bZelensk(?:yy|iy|y|i)\b', '젤렌스키'),
    (r'\bVladimir\s+Putin\b', '블라디미르 푸틴'),
    (r'\bJoe\s+Biden\b', '조 바이든'),
    (r'\bChris\s+Wright\b', '크리스 라이트'),
    (r'\bRussia\b', '러시아'),
    (r'\bRussian\b', '러시아'),
    (r'\bUkraine\b', '우크라이나'),
    (r'\bUkrainian\b', '우크라이나'),
    (r'\bdiesel\b', '경유'),
    (r'\bgasoline\b', '휘발유'),
    (r'\bgas prices\b', '휘발유 가격'),
    (r'\bfuel prices\b', '연료 가격'),
    (r'\bfuel\b', '연료'),
    (r'\brefineries\b', '정유시설'),
    (r'\brefinery\b', '정유시설'),
    (r'\bstrikes\b', '공격'),
    (r'\bstrike\b', '공격'),
    (r'\bChevron\b', '셰브론'),
    (r'\bEni\b', '에니'),
    (r'\bShell\b', '셸'),
    (r'\bRepsol\b', '렙솔'),
    (r'\bGeoPark\b', '지오파크'),
    (r'\bGE\s+Vernova\b', 'GE 버노바'),
    (r'\bReuters\b', '로이터'),
    (r'\bBloomberg\b', '블룸버그'),
    (r'\bBRICS\b', '브릭스(BRICS)'),
    (r'\bSWIFT\b', '스위프트(SWIFT)'),
    (r'\bIncl\.?\b', '포함'),
)


def _chunks(text, limit=430):
    text = base.rw.clean_paragraph(text)
    if len(text) <= limit:
        return [text]
    out = []
    current = ""
    for sentence in base.split_sentences(text):
        if len(sentence) > limit:
            words = sentence.split()
            piece = ""
            for word in words:
                candidate = word if not piece else piece + " " + word
                if len(candidate) > limit and piece:
                    out.append(piece)
                    piece = word
                else:
                    piece = candidate
            if piece:
                if current:
                    out.append(current)
                    current = ""
                out.append(piece)
            continue
        candidate = sentence if not current else current + " " + sentence
        if len(candidate) > limit and current:
            out.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        out.append(current)
    return [x for x in out if x]


def _normalize_korean_output(text):
    text = base.rw.clean_paragraph(text)
    for pattern, replacement in _ENTITY_REPLACEMENTS:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    # 번역기가 한글명과 영문명을 중복 표기한 흔적 제거.
    text = re.sub(r'도널드 트럼프\s*\(\s*도널드 트럼프\s*\)', '도널드 트럼프', text)
    text = re.sub(r'볼로디미르 젤렌스키\s*\(\s*볼로디미르 젤렌스키\s*\)', '볼로디미르 젤렌스키', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def _latin_tokens(text):
    return re.findall(r"[A-Za-z][A-Za-z0-9&.+/-]*", text or "")


def _residual_english_prose(text):
    residual = []
    for token in _latin_tokens(text):
        stripped = token.strip('.,:;()[]{}').strip()
        if not stripped:
            continue
        upper = stripped.upper()
        lower = stripped.lower()
        if upper in _ALLOWED_IDENTIFIERS:
            continue
        # 식별용 티커·짧은 약어는 허용한다.
        if re.fullmatch(r'[A-Z]{2,5}', stripped):
            continue
        # URL/도메인은 제목·본문 번역 결과에 들어오면 안 되지만, 혹시 남아도 일반 영어 문장과 구분한다.
        if '.' in stripped or '/' in stripped:
            continue
        if lower in _ENGLISH_PROSE_WORDS:
            residual.append(stripped)
            continue
        # 긴 영문 이름/구가 여러 개 남으면 반쪽 번역으로 취급한다.
        if len(stripped) >= 6:
            residual.append(stripped)
    return residual


def _korean_quality_ok(text):
    text = _normalize_korean_output(text)
    if not text or not base.rw.has_hangul(text):
        return False
    if _residual_english_prose(text):
        return False
    hangul = sum(1 for ch in text if '가' <= ch <= '힣')
    latin = sum(1 for ch in text if ch.isascii() and ch.isalpha())
    # 약어를 제외한 영문 비중이 지나치게 높으면 송출 차단.
    return latin <= max(18, int((hangul + 1) * 0.18))


def _translate_with_mymemory(text):
    parts = []
    for chunk in _chunks(text):
        translated = MyMemoryTranslator(source='en-GB', target='ko-KR').translate(text=chunk)
        translated = _normalize_korean_output(translated)
        parts.append(base.rw.validate_korean_translation(chunk, translated))
    return _normalize_korean_output(' '.join(parts).strip())


def translate_ko_resilient(text):
    cleaned = base.rw.clean_paragraph(text)
    if not cleaned:
        return ""
    if base.rw.looks_like_error_page(cleaned):
        raise RuntimeError('오류문은 번역·송출하지 않음')

    # 이미 한국어 기사라도 영문 이름·일반 영문 문장이 섞였으면 먼저 정규화한다.
    normalized_source = _normalize_korean_output(cleaned)
    if _korean_quality_ok(normalized_source):
        return normalized_source

    first_error = None
    try:
        translated = _original_translate_ko(cleaned)
        translated = _normalize_korean_output(translated)
        if _korean_quality_ok(translated):
            return translated
        first_error = RuntimeError('1차 번역 뒤 영문 일반문장이 남아 있음')
    except Exception as exc:
        first_error = exc

    try:
        translated = _translate_with_mymemory(cleaned)
        if _korean_quality_ok(translated):
            return translated
        raise RuntimeError('2차 번역 뒤 영문 일반문장이 남아 있음')
    except Exception as exc:
        # 영어 원문이나 반쪽 번역을 절대 대체 송출하지 않는다.
        raise RuntimeError(f'한국어 완전 번역 실패: 1차={first_error}; 2차={exc}') from exc


def refinery_event_key_v2(item):
    t = f"{item.get('title','')} {item.get('source','')}".lower()

    if (
        any(x in t for x in ("small refinery exemption", "small-refinery exemption", "sre"))
        or ("rin" in t and any(x in t for x in ("realloc", "rvo", "waiver", "exemption")))
    ):
        if any(x in t for x in ("price", "prices", "jump", "rise", "rose", "rally", "market", "trading")) and "rin" in t:
            return "rfs_market_reaction_2025_20260901"
        return "rfs_sre_2025_package_20260831"

    return _original_refinery_event_key(item)


def spr_event_key_v2(item):
    t = f"{item.get('title','')} {item.get('source','')}".lower()

    if (
        ("venezuela" in t or "venezuelan" in t)
        and any(x in t for x in ("more than double", "double oil output", "double production", "production to double", "output to double"))
    ):
        return "venezuela_output_double_deals_20260902"

    if any(x in t for x in ("historic oil agreement", "65 billion", "north american blue energy", "nabep", "17 oilfield", "17 oil field")):
        return "spr_venezuela_oil_agreement_20260831"

    if any(x in t for x in ("heavy crude", "extra-heavy", "extra heavy", "sulfur", "sulphur", "cavern", "storage cost", "too heavy", "api gravity")):
        return "spr_venezuela_quality_bottleneck"

    venezuela = "venezuela" in t or "venezuelan" in t
    reserve = (
        "strategic petroleum reserve" in t
        or "strategic reserve" in t
        or "strategic oil reserve" in t
        or "spr" in t
    )
    refill = any(x in t for x in (
        "refill", "replenish", "fill up", "fill the", "top out", "topping", "gift",
        "restock", "rebuild", "re-stock",
    ))
    if venezuela and reserve and refill:
        return "spr_refill_statement_20260830"

    return _original_spr_event_key(item)


def event_label_v2(topic, key):
    if key == "rfs_sre_2025_package_20260831":
        return "RFS 정책 패키지"
    if key == "venezuela_output_double_deals_20260902":
        return "베네수엘라 증산·기업계약"
    return _original_event_label(topic, key)


def impact_lines_v2(topic, key):
    if key == "rfs_sre_2025_package_20260831":
        return [
            "소규모 정유사는 2025년 RFS 부담이 완화되지만, 대형 정유사는 2026·2027 RVO 재할당이 다음 비용 변수입니다.",
            "바이오연료·농가는 면제 자체보다 실제 재할당 규모와 RIN 가격이 중요합니다.",
        ]
    if key == "venezuela_output_double_deals_20260902":
        return []
    return _original_impact_lines(topic, key)


def next_checks_v2(topic, key):
    if key == "rfs_sre_2025_package_20260831":
        return [
            "10월 말 이전 EPA의 2026·2027 RVO 재할당안",
            "RIN 가격과 정유사별 실제 규제비용 변화",
        ]
    if key == "venezuela_output_double_deals_20260902":
        return [
            "실제 서명되는 기업별 계약·유전·설비투자·증산 물량",
            "베네수엘라 생산량이 110만~120만 배럴/일 기준에서 실제로 얼마나 빠르게 증가하는지",
        ]
    return _original_next_checks(topic, key)


NOISE_MARKERS = (
    "nvidia", "상위 10개 ai", "전체 목록", "무료로 확인", "무료로 공개",
    "지금 전체 목록", "newsletter", "subscribe", "sign up", "advertisement",
    "read more", "click here", "지금 확인하세요", "무료 목록",
)


def _is_noise(text):
    low = (text or "").lower()
    return any(x in low for x in NOISE_MARKERS)


def compact_snapshot(enriched, key):
    fixed = {
        "rfs_sre_2025_package_20260831": [
            "EPA가 2025년 SRE 34건을 결정하고 29개 정유소에 총 17억6천만 RIN을 면제했습니다.",
            "예상치보다 늘어난 면제분은 2026·2027 RVO에 100% 재할당하는 방안을 제안합니다.",
            "2025년 RVO 준수기한은 2026년 10월 1일까지 30일 연장됩니다.",
        ],
        "spr_refill_statement_20260830": [
            "트럼프는 베네수엘라산 원유로 미국 SPR을 보충하고 절차를 곧 시작하겠다고 밝혔습니다.",
            "아직 핵심 미확정은 DOE의 실제 조달 배럴 수·가격·반입일·저장기지입니다.",
            "실행 병목은 베네수엘라 초중질유의 저장 적합성, 생산 확대 속도, 기존 교환 반환물량과의 구분입니다.",
        ],
        "venezuela_output_double_deals_20260902": [
            "크리스 라이트 미 에너지부 장관은 곧 발표될 여러 글로벌 에너지기업 계약이 베네수엘라 원유 생산을 향후 몇 년 내 2배 이상 늘릴 것이라고 밝혔습니다.",
            "현재 로이터와 미국 에너지부 공식자료로 서명이 확인된 핵심 기업은 셰브론·에니·GE 버노바이며, 그 밖의 기업은 발표·협의 단계와 기존 합작을 구분해 표시합니다.",
            "이는 셰브론의 대규모 사업 확대 등 민간기업 투자를 끌어들여 원유 공급을 늘리고 장기적으로 미국 휘발유 가격 압력을 낮추려는 정책 축입니다.",
            "이번 기업별 계약들은 NABEP가 17개 유전·약 650억 배럴에 접근하는 미·베네수엘라 대형 합의와는 별도 트랙으로 진행됩니다.",
            "최대 역풍은 노후 유전·송유관·전력·희석제 부족과 장기간 투자 공백입니다. 현재 약 110만~120만 배럴/일에서 2배 이상 증산하려면 수년이 걸리고, 과거 300만 배럴/일 수준 회복은 더 오래 걸릴 수 있습니다.",
        ],
    }
    if key in fixed:
        return fixed[key]

    candidates = []
    for idx, paragraph in enumerate(enriched.get("body_ko") or []):
        for sentence in base.split_sentences(paragraph):
            sentence = _normalize_korean_output(re.sub(r"\s+", " ", sentence).strip())
            if not sentence or _is_noise(sentence):
                continue
            if not _korean_quality_ok(sentence):
                continue
            score = 0
            if re.search(r"\d", sentence):
                score += 3
            if any(x in sentence for x in ("발표", "결정", "합의", "면제", "재할당", "비축", "생산", "권리", "지분", "가격", "원유", "저장", "계약", "투자")):
                score += 2
            if idx == 0:
                score += 1
            candidates.append((score, sentence))

    selected = []
    for _, sentence in sorted(candidates, key=lambda x: x[0], reverse=True):
        normalized = re.sub(r"\W+", "", sentence)
        if not normalized:
            continue
        if any(normalized in re.sub(r"\W+", "", x) or re.sub(r"\W+", "", x) in normalized for x in selected):
            continue
        selected.append(base.short(sentence, 150))
        if len(selected) >= 4:
            break
    return selected


def event_headline(topic, key, enriched):
    headlines = {
        "rfs_sre_2025_package_20260831": "EPA, 2025 SRE 17억6천만 RIN 면제·재할당 추진",
        "rfs_market_reaction_2025_20260901": "RFS 결정 뒤 RIN 가격 반응 점검",
        "refinery_white_house_meeting_20260901": "트럼프–정유업계 회동: 가격·RFS·정제능력 논의",
        "refinery_gasoline_policy_202609": "휘발유 가격 인하 정책 변화",
        "refinery_capacity_policy_202609": "미국 정제능력·허가 정책 변화",
        "spr_refill_statement_20260830": "베네수엘라산 원유로 SPR 보충 추진…실제 반입은 미확정",
        "spr_venezuela_oil_agreement_20260831": "미국–베네수엘라 대형 석유 합의…SPR 공급 경로 확대",
        "spr_venezuela_quality_bottleneck": "베네수엘라 초중질유, SPR 저장 적합성이 핵심 병목",
        "venezuela_output_double_deals_20260902": "미국, 베네수엘라 산유량 향후 몇 년 내 2배 이상 확대 추진",
    }
    if key in headlines:
        return headlines[key]
    return base.short(_normalize_korean_output(enriched.get("title_ko") or "정책·시장 변화"), 125)


def concise_news_alert(topic, enriched, key, grouped_items):
    source = html.escape(enriched.get("source") or "확인 필요", quote=False)
    pub = html.escape((enriched.get("pub_kst") or "").replace("T", " "), quote=False)
    link = html.escape(enriched.get("original_url") or "", quote=True)
    header = "트럼프 정유업계" if topic == "refinery" else "SPR·베네수엘라 원유"
    headline_text = _normalize_korean_output(event_headline(topic, key, enriched))
    if not _korean_quality_ok(headline_text):
        raise RuntimeError('제목 한국어 품질검사 실패')
    headline = html.escape(headline_text, quote=False)

    lines = [
        f"<b>[{header}]</b>",
        f"<b>{headline}</b>",
        f"{source} · {pub}",
    ]
    if len(grouped_items) > 1:
        lines.append(f"관련 보도 {len(grouped_items)}건 묶음")

    snapshot = compact_snapshot(enriched, key)
    if snapshot:
        lines.append("")
        max_core = 5 if key == "venezuela_output_double_deals_20260902" else 4
        for item in snapshot[:max_core]:
            item = _normalize_korean_output(item)
            if not _korean_quality_ok(item):
                raise RuntimeError('본문 한국어 품질검사 실패')
            lines.append(f"• {html.escape(item, quote=False)}")

    impacts = impact_lines_v2(topic, key)
    if impacts:
        impact = _normalize_korean_output(impacts[0])
        if not _korean_quality_ok(impact):
            raise RuntimeError('의미 문구 한국어 품질검사 실패')
        lines.append("")
        lines.append(f"<b>의미</b> · {html.escape(impact, quote=False)}")

    checks = next_checks_v2(topic, key)
    if checks:
        lines.extend(["", "<b>다음 확인</b>"])
        for check in checks[:2]:
            check = _normalize_korean_output(check)
            if not _korean_quality_ok(check):
                raise RuntimeError('다음 확인 문구 한국어 품질검사 실패')
            lines.append(f"• {html.escape(check, quote=False)}")

    lines.extend(["", f'<a href="{link}">원문</a>'])
    return "\n".join(lines).strip()


base.rw.translate_ko = translate_ko_resilient
base.refinery_event_key = refinery_event_key_v2
base.spr_event_key = spr_event_key_v2
base.event_label = event_label_v2
base.impact_lines = impact_lines_v2
base.next_checks = next_checks_v2
base.readable_news_alert = concise_news_alert


if __name__ == "__main__":
    base.main()
