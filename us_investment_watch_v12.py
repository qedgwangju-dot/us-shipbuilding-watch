#!/usr/bin/env python3
from __future__ import annotations

import re

import us_investment_watch as base
import us_investment_watch_v9 as v9
import us_investment_watch_v11 as v11

# v12: 원전 관련 대미투자 알림의 고정 비교 기준
# - 비교군은 AP1000 vs APR1400으로 고정
# - 알림 계산 기준: AP1000 1기 = 1.11GW, APR1400 1기 = 1.40GW
# - 원전 관련 내용 변화가 있으면 노형별 1기 용량, 현재 기수×용량, 합계 GW를 항상 표시
# - 한국 기업 가치사슬 차이를 같이 보여주되 실제 계약·역할은 별도 확인으로 구분

AP1000_GW_PER_UNIT = 1.11
APR1400_GW_PER_UNIT = 1.40

for query in [
    '"AP1000" "APR1400" 미국 원전 한국 when:3d',
    '"APR1400" 미국 우선 건설 when:3d',
    '"AP1000 6기" "APR1400 2기" when:3d',
    '"한국형 원전" APR1400 미국 when:3d',
]:
    if query not in base.QUERIES:
        base.QUERIES.insert(0, query)

for term in [
    "AP1000", "APR1400", "AP1000 6기", "APR1400 2기",
    "APR1400 우선", "우선적으로 건설", "한국형 원전 2기",
]:
    if term not in base.MATERIAL:
        base.MATERIAL.append(term)

_ORIGINAL_EXTRACT = v9.extract_facts
_ORIGINAL_BUILD_ALERT = v9.build_alert


def _reactor_count(blob: str, model: str) -> int | None:
    pats = [
        rf'{re.escape(model)}.{{0,60}}?(\d+)\s*기',
        rf'(\d+)\s*기.{{0,60}}?{re.escape(model)}',
    ]
    for pat in pats:
        m = re.search(pat, blob, re.I | re.S)
        if m:
            try:
                return int(m.group(1))
            except Exception:
                return None
    return None


def extract_facts_v12(row: dict) -> list[dict]:
    out = list(_ORIGINAL_EXTRACT(row))
    title = str(row.get('title') or '')
    text = str(row.get('article_text') or '')
    source = str(row.get('resolved_link') or row.get('link') or '')
    blob = f'{title}\n{text}'
    low = blob.lower()

    if '원전' in low or 'ap1000' in low or 'apr1400' in low:
        ap_count = _reactor_count(blob, 'AP1000')
        if ap_count is not None:
            out.append(v9.fact(
                'nuclear.ap1000_reactors', ap_count,
                'AP1000 포함 기수', '보도', source,
            ))

        apr_count = _reactor_count(blob, 'APR1400')
        if apr_count is None and re.search(r'한국형\s*원전.{0,40}?2\s*기', blob, re.I | re.S):
            apr_count = 2
        if apr_count is not None:
            out.append(v9.fact(
                'nuclear.apr1400_reactors', apr_count,
                'APR1400·한국형 원전 포함 기수', '보도', source,
            ))

        if (
            re.search(r'APR[- ]?1400.{0,80}?우선.{0,40}?건설', blob, re.I | re.S)
            or re.search(r'APR[- ]?1400\s*2\s*기.{0,80}?우선', blob, re.I | re.S)
            or ('apr1400' in low and '우선적으로 건설' in low)
        ):
            out.append(v9.fact(
                'nuclear.apr1400_priority_build_reported', True,
                'APR1400 우선 건설 방향', '보도·협의 단계', source,
            ))

    return out


def _changed_value(changes, key: str):
    for k, new, _old in changes:
        if k == key:
            return new.get('value')
    return None


def _state_value(key: str):
    try:
        state = v9.load_fact_state()
        fact_obj = (state.get('facts') or {}).get(key) or {}
        return fact_obj.get('value')
    except Exception:
        return None


def _current_value(changes, key: str):
    changed = _changed_value(changes, key)
    if changed is not None:
        return changed
    return _state_value(key)


def _is_nuclear_change(changes) -> bool:
    for key, _new, _old in changes:
        if key.startswith('nuclear.') or key.startswith('package.nuclear'):
            return True
    return False


def _fmt_count(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    return None


def _model_context(changes) -> str:
    if not _is_nuclear_change(changes):
        return ''

    ap_count = _fmt_count(_current_value(changes, 'nuclear.ap1000_reactors'))
    apr_count = _fmt_count(_current_value(changes, 'nuclear.apr1400_reactors'))

    diff_gw = APR1400_GW_PER_UNIT - AP1000_GW_PER_UNIT
    diff_pct = diff_gw / AP1000_GW_PER_UNIT * 100

    lines = [
        '<b>⚙️ AP1000 vs APR1400 고정 비교</b>',
        f'• <b>AP1000</b>: 1기 = <b>{AP1000_GW_PER_UNIT:.2f}GW</b> · Westinghouse 노형 · 미국 NRC 설계인증 · 미국 현지 건설·운전 레퍼런스 보유',
        f'• <b>APR1400</b>: 1기 = <b>{APR1400_GW_PER_UNIT:.2f}GW</b> · KEPCO·KHNP 한국형 노형 · 2019년 미국 NRC 설계인증 · 한국·UAE 건설·운전 레퍼런스',
        f'• 1기 용량 차이: APR1400이 AP1000보다 <b>+{diff_gw:.2f}GW</b>(약 <b>+{diff_pct:.1f}%</b>)',
        '',
        '<b>🏗️ 한국 기업 가치사슬 비교</b>',
        '• <b>AP1000</b>: Westinghouse 설계·노형 → 한국 기업은 주기기·설계·조달·시공·일부 기자재 참여 가능성이 핵심',
        '• <b>APR1400</b>: KHNP 사업개발·운영 → 한국전력기술 설계 → 두산에너빌리티 주기기 → 한국 건설사 시공 → 한전KPS 정비까지 한국 가치사슬 참여 폭이 더 넓어질 수 있음',
        '• 위 역할은 일반 가치사슬 비교이며 <b>미국 개별 프로젝트의 확정 계약·수주 범위는 별도 확인</b>',
    ]

    if ap_count is not None or apr_count is not None:
        lines.extend(['', '<b>⚡ 현재 누적 보도 기준 용량</b>'])
        total = 0.0
        if ap_count is not None:
            ap_gw = ap_count * AP1000_GW_PER_UNIT
            total += ap_gw
            lines.append(
                f'• AP1000 {ap_count}기 × {AP1000_GW_PER_UNIT:.2f}GW = <b>{ap_gw:.2f}GW</b>'
            )
        if apr_count is not None:
            apr_gw = apr_count * APR1400_GW_PER_UNIT
            total += apr_gw
            lines.append(
                f'• APR1400 {apr_count}기 × {APR1400_GW_PER_UNIT:.2f}GW = <b>{apr_gw:.2f}GW</b>'
            )
        if ap_count is not None and apr_count is not None:
            lines.append(f'• 합계 = <b>{total:.2f}GW</b>')
        lines.append('• 기수 구성이 바뀌면 같은 1기당 용량 기준으로 자동 재계산')

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
