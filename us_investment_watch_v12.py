#!/usr/bin/env python3
from __future__ import annotations

import re

import us_investment_watch as base
import us_investment_watch_v9 as v9
import us_investment_watch_v11 as v11

# v12 corrected: 대미투자 원전 노형 비교는 AP1000 vs APR1400에 집중한다.
# - AP1000 = Westinghouse 미국 노형
# - APR1400 = 한국형 대형 노형
# - 기사에서 6×AP1000 + 2×APR1400, APR1400 2기 우선 건설 등 노형 배분 변화만 누적
# - APR1000은 이번 대미투자 비교의 기본 감시축에서 제외한다. 실제 기사에서 별도 핵심 사실로 등장할 때만 일반 원문 감시기가 잡는다.

for query in [
    '"AP1000" "APR1400" 미국 원전 한국 when:3d',
    '"APR1400 2기" 미국 원전 when:3d',
    '"APR1400" 우선 건설 미국 when:3d',
    '"AP1000 6기" "APR1400 2기" when:3d',
]:
    if query not in base.QUERIES:
        base.QUERIES.insert(0, query)

for term in ["AP1000", "APR1400", "APR1400 2기", "AP1000 6기", "우선 건설"]:
    if term not in base.MATERIAL:
        base.MATERIAL.append(term)

_ORIGINAL_EXTRACT = v9.extract_facts
_ORIGINAL_BUILD_ALERT = v9.build_alert


def extract_facts_v12(row: dict) -> list[dict]:
    out = list(_ORIGINAL_EXTRACT(row))
    title = str(row.get('title') or '')
    text = str(row.get('article_text') or '')
    source = str(row.get('resolved_link') or row.get('link') or '')
    blob = f'{title}\n{text}'
    low = blob.lower()

    # APR1400 2기 '우선 건설'은 단순 포함보다 확정도가 높은 별도 사실로 누적한다.
    if re.search(r'apr[- ]?1400.{0,80}2\s*기.{0,80}(우선|먼저)', blob, re.I | re.S) or \
       re.search(r'(우선|먼저).{0,80}apr[- ]?1400.{0,80}2\s*기', blob, re.I | re.S):
        out.append(v9.fact(
            'nuclear.apr1400_two_units_priority', True,
            'APR1400 2기 우선 건설 방향', '보도·협의 단계', source,
        ))

    if re.search(r'ap1000.{0,50}6\s*기|6\s*기.{0,50}ap1000', blob, re.I | re.S):
        out.append(v9.fact(
            'nuclear.ap1000_reactors', 6,
            'AP1000 포함 기수', '보도', source,
        ))

    if re.search(r'apr[- ]?1400.{0,50}2\s*기|2\s*기.{0,50}apr[- ]?1400', blob, re.I | re.S) or \
       re.search(r'한국형\s*원전.{0,40}2\s*기', blob, re.I | re.S):
        out.append(v9.fact(
            'nuclear.apr1400_reactors', 2,
            'APR1400·한국형 원전 포함 기수', '보도', source,
        ))

    return out


def _changed_value(changes, key: str):
    for k, new, _old in changes:
        if k == key:
            return new.get('value')
    return None


def _model_context(changes) -> str:
    keys = {k for k, _n, _o in changes}
    if not any(k in keys for k in [
        'nuclear.ap1000_reactors',
        'nuclear.apr1400_reactors',
        'nuclear.apr1400_two_units_priority',
    ]):
        return ''

    lines = [
        '<b>⚙️ AP1000 vs APR1400</b>',
        '• <b>AP1000</b> — Westinghouse · 약 1.11GW 순전기출력 · 수동형 안전계통이 핵심 · 미국 Vogtle 3·4호기 운전 실적',
        '• <b>APR1400</b> — KEPCO/KHNP · 1.4GW급 · 한국·UAE 운전 실적 · 2019년 미국 NRC 설계인증',
        '• <b>미국 사업 의미</b> — AP1000은 미국 현지 실적·공급망 우위, APR1400은 더 큰 기당 출력과 한국 설계·주기기·시공·운영 몫 확대 가능성이 핵심',
    ]

    ap = _changed_value(changes, 'nuclear.ap1000_reactors')
    apr = _changed_value(changes, 'nuclear.apr1400_reactors')
    if isinstance(ap, (int, float)) and isinstance(apr, (int, float)):
        # AP1000은 공식 Westinghouse 명목 순전기출력 1.11GW, APR1400은 KHNP 1.4GW급 설비용량을 사용한 단순 용량 검산.
        total = float(ap) * 1.11 + float(apr) * 1.4
        lines.append(f'• 보도 조합 단순 용량: AP1000 {int(ap)}기 + APR1400 {int(apr)}기 ≈ <b>{total:.2f}GW</b>')
    if 'nuclear.apr1400_two_units_priority' in keys:
        lines.append('• <b>APR1400 2기 우선 건설</b>이 최종 합의문에 들어가면 단순 2기 수주보다 미국 내 첫 실증 레퍼런스 확보가 더 큰 재평가 요인')

    lines.append('• 다음 확인: 9월 18일 서명문 → 2기 부지·사업자 → 한국 실제 출자액 → EPC·주기기 본계약 → 미국 현지 공급망·인허가 일정')
    return '\n'.join(lines)


def build_alert_v12(now, changes, fx: float, fx_source: str) -> str:
    alert = _ORIGINAL_BUILD_ALERT(now, changes, fx, fx_source)
    context = _model_context(changes)
    if not context:
        return alert
    marker = f'\n\n<a href="{base.MOU_OFFICIAL_URL}"><b>산업통상부 한미 전략투자 MOU</b></a>'
    if marker in alert:
        return alert.replace(marker, f'\n\n{context}{marker}', 1)
    return alert + '\n\n' + context


v9.extract_facts = extract_facts_v12
v9.build_alert = build_alert_v12


def main() -> int:
    return v11.main()


if __name__ == '__main__':
    raise SystemExit(main())
