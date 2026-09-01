#!/usr/bin/env python3
import readable_energy_alert_runner as base

_original_refinery_event_key = base.refinery_event_key
_original_event_label = base.event_label
_original_impact_lines = base.impact_lines
_original_next_checks = base.next_checks


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


base.refinery_event_key = refinery_event_key_v2
base.event_label = event_label_v2
base.impact_lines = impact_lines_v2
base.next_checks = next_checks_v2


if __name__ == "__main__":
    base.main()
