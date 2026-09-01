#!/usr/bin/env python3
from deep_translator import MyMemoryTranslator
import readable_energy_alert_runner as base

_original_refinery_event_key = base.refinery_event_key
_original_event_label = base.event_label
_original_impact_lines = base.impact_lines
_original_next_checks = base.next_checks
_original_translate_ko = base.rw.translate_ko


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
        # The Aug. 31 SRE decision and its 2026-27 reallocation proposal are one policy package.
        return "rfs_sre_2025_package_20260831"

    return _original_refinery_event_key(item)


def event_label_v2(topic, key):
    if key == "rfs_sre_2025_package_20260831":
        return "RFS 정책 패키지 확정"
    return _original_event_label(topic, key)


def impact_lines_v2(topic, key):
    if key == "rfs_sre_2025_package_20260831":
        return [
            "소규모 정유사: 2025년 RFS 준수 부담이 즉시 완화됩니다.",
            "대형 정유사: 2026·2027 RVO 재할당 방식에 따라 향후 의무 부담이 일부 이동할 수 있습니다.",
            "바이오연료·농가: 면제 자체보다 초과 면제분의 100% 재할당이 실제 수요를 얼마나 복원하는지가 핵심입니다.",
            "RIN 시장: 면제와 재할당이 동시에 움직여 가격 변동성이 커질 수 있습니다.",
        ]
    return _original_impact_lines(topic, key)


def next_checks_v2(topic, key):
    if key == "rfs_sre_2025_package_20260831":
        return [
            "EPA가 10월 말 이전 제시할 2026·2027 RVO 재할당 최종안",
            "2025 RVO 준수기한 2026년 10월 1일 연장 시행과 추가 규칙",
            "RIN 가격과 소규모·대형 정유사별 실제 규제비용 변화",
            "9월 1일 백악관 정유업계 회동에서 RFS 추가 완화·수정이 나오는지",
        ]
    return _original_next_checks(topic, key)


base.rw.translate_ko = translate_ko_resilient
base.refinery_event_key = refinery_event_key_v2
base.event_label = event_label_v2
base.impact_lines = impact_lines_v2
base.next_checks = next_checks_v2


if __name__ == "__main__":
    base.main()
