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


def translate_ko_resilient(text):
    try:
        return _original_translate_ko(text)
    except Exception as first_error:
        cleaned = base.rw.clean_paragraph(text)
        if not cleaned:
            return ""
        if base.rw.looks_like_error_page(cleaned):
            raise first_error
        if base.rw.has_hangul(cleaned) and sum(1 for ch in cleaned if "가" <= ch <= "힣") >= max(4, len(cleaned) // 8):
            return cleaned

        translated_parts = []
        last_error = first_error
        for chunk in _chunks(cleaned):
            try:
                translated = MyMemoryTranslator(source="en-GB", target="ko-KR").translate(text=chunk)
                translated_parts.append(base.rw.validate_korean_translation(chunk, translated))
            except Exception as exc:
                last_error = exc
                raise RuntimeError(f"한국어 번역 이중 실패: {last_error}") from exc
        combined = " ".join(translated_parts).strip()
        return base.rw.validate_korean_translation(cleaned, combined)


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

    # Wright 장관의 '향후 몇 년 내 산유량 2배 이상' 발언과 후속 기업 계약은 하나의 사건으로 묶는다.
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
        # 이 사건은 핵심 5줄 안에 투자 의미와 실패모드를 함께 넣어 별도 판정 구역을 만들지 않는다.
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
            "현재 Reuters가 최종 협정 대상으로 직접 확인한 기업은 Chevron·Eni·ONGC·GeoPark·GE Vernova이며, Bloomberg는 Shell·BP 등을 포함한 10여 건 이상의 계약을 예고했습니다. Repsol은 Eni와 기존 합작 연결이 있습니다.",
            "이는 Chevron의 대규모 사업 확대 등 민간기업 투자를 끌어들여 원유 공급을 늘리고 장기적으로 미국 휘발유 가격 압력을 낮추려는 정책 축입니다.",
            "이번 기업별 계약들은 NABEP가 17개 유전·약 650억 배럴에 접근하는 미·베네수엘라 대형 합의와는 별도 트랙으로 진행됩니다.",
            "최대 역풍은 노후 유전·송유관·전력·희석제 부족과 장기간 투자 공백입니다. 현재 약 110만~120만 배럴/일에서 2배 이상 증산하려면 수년이 걸리고, 과거 300만 배럴/일 수준 회복은 더 오래 걸릴 수 있습니다.",
        ],
    }
    if key in fixed:
        return fixed[key]

    candidates = []
    for idx, paragraph in enumerate(enriched.get("body_ko") or []):
        for sentence in base.split_sentences(paragraph):
            sentence = re.sub(r"\s+", " ", sentence).strip()
            if not sentence or _is_noise(sentence):
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
    return base.short(enriched.get("title_ko") or "정책·시장 변화", 125)


def concise_news_alert(topic, enriched, key, grouped_items):
    source = html.escape(enriched.get("source") or "확인 필요", quote=False)
    pub = html.escape((enriched.get("pub_kst") or "").replace("T", " "), quote=False)
    link = html.escape(enriched.get("original_url") or "", quote=True)
    header = "트럼프 정유업계" if topic == "refinery" else "SPR·베네수엘라 원유"
    headline = html.escape(event_headline(topic, key, enriched), quote=False)

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
        lines.extend(f"• {html.escape(x, quote=False)}" for x in snapshot[:max_core])

    # 일반 사건은 투자 판단 한 줄만 붙이고, 고정 5줄형 사건은 핵심 안에 포함한다.
    impacts = impact_lines_v2(topic, key)
    if impacts:
        lines.append("")
        lines.append(f"<b>의미</b> · {html.escape(impacts[0], quote=False)}")

    checks = next_checks_v2(topic, key)
    if checks:
        lines.extend(["", "<b>다음 확인</b>"])
        lines.extend(f"• {html.escape(x, quote=False)}" for x in checks[:2])

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
