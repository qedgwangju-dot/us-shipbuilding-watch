#!/usr/bin/env python3
from __future__ import annotations

import re

import us_investment_watch as base
import us_investment_watch_v9 as v9
import us_investment_watch_v11 as v11

# v12: 원전 관련 대미투자 알림의 고정 비교 + 자동 계산 기준
# - 비교군은 AP1000 vs APR1400으로 고정
# - AP1000 1기 = 1.11GW, APR1400 1기 = 1.40GW
# - 원전 관련 내용 변화가 있으면 노형별 기수×용량, 총 GW를 항상 자동 계산
# - 총사업비/제안액이 있으면 1기당 사업비, GW당 사업비, 전략투자 대비 비중도 자동 계산
# - 달러 계산값은 실행 시점 환율로 원화 병기
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


def _fmt_num(value) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _krw_from_eok(value_eok: float, fx: float) -> str:
    try:
        return v9.krw_for_eok(float(value_eok), float(fx))
    except Exception:
        won = float(value_eok) * 100_000_000 * float(fx)
        jo = int(won // 1_000_000_000_000)
        eok = int(round((won - jo * 1_000_000_000_000) / 100_000_000))
        if eok >= 10_000:
            jo += eok // 10_000
            eok %= 10_000
        return f'약 {jo}조 {eok:,}억원' if eok else f'약 {jo}조원'


def _model_context(changes, fx: float) -> str:
    if not _is_nuclear_change(changes):
        return ''

    ap_count = _fmt_count(_current_value(changes, 'nuclear.ap1000_reactors'))
    apr_count = _fmt_count(_current_value(changes, 'nuclear.apr1400_reactors'))
    total_reactors = _fmt_count(_current_value(changes, 'nuclear.reactors'))
    project_size = _fmt_num(_current_value(changes, 'nuclear.reported_project_size_usd_eok'))
    us_proposal = _fmt_num(_current_value(changes, 'nuclear.us_proposal_usd_eok'))
    strategic_total = _fmt_num(_current_value(changes, 'strategy.total_usd_eok'))

    diff_gw = APR1400_GW_PER_UNIT - AP1000_GW_PER_UNIT
    diff_pct = diff_gw / AP1000_GW_PER_UNIT * 100

    lines = [
        '<b>⚙️ AP1000 vs APR1400 고정 비교</b>',
        f'• <b>AP1000</b>: 1기 = <b>{AP1000_GW_PER_UNIT:.2f}GW</b> · Westinghouse 노형 · 미국 NRC 설계인증 · 미국 현지 건설·운전 레퍼런스 보유',
        f'• <b>APR1400</b>: 1기 = <b>{APR1400_GW_PER_UNIT:.2f}GW</b> · KEPCO·KHNP 한국형 노형 · 2019년 미국 NRC 설계인증 · 한국·UAE 건설·운전 레퍼런스',
        f'• 1기 용량 차이: APR1400이 AP1000보다 <b>+{diff_gw:.2f}GW</b>(약 <b>+{diff_pct:.1f}%</b>)',
    ]

    total_gw = 0.0
    known_model_count = 0
    if ap_count is not None:
        total_gw += ap_count * AP1000_GW_PER_UNIT
        known_model_count += ap_count
    if apr_count is not None:
        total_gw += apr_count * APR1400_GW_PER_UNIT
        known_model_count += apr_count

    if ap_count is not None or apr_count is not None:
        lines.extend(['', '<b>⚡ 자동 용량 계산</b>'])
        if ap_count is not None:
            ap_gw = ap_count * AP1000_GW_PER_UNIT
            lines.append(f'• AP1000 {ap_count}기 × {AP1000_GW_PER_UNIT:.2f}GW = <b>{ap_gw:.2f}GW</b>')
        if apr_count is not None:
            apr_gw = apr_count * APR1400_GW_PER_UNIT
            lines.append(f'• APR1400 {apr_count}기 × {APR1400_GW_PER_UNIT:.2f}GW = <b>{apr_gw:.2f}GW</b>')
        if ap_count is not None and apr_count is not None:
            lines.append(f'• 합계 = <b>{total_gw:.2f}GW</b>')
            if known_model_count > 0:
                lines.append(f'• 평균 1기당 용량 = <b>{total_gw / known_model_count:.2f}GW</b>')
        if total_reactors is not None and known_model_count and total_reactors != known_model_count:
            lines.append(f'• 주의: 전체 {total_reactors}기 보도와 노형별 확인 합계 {known_model_count}기가 달라 <b>미분류 {total_reactors-known_model_count}기</b> 존재')
        lines.append('• 기수 구성이 바뀌면 같은 1기당 용량 기준으로 자동 재계산')

    if project_size is not None and total_gw > 0 and known_model_count > 0:
        per_unit = project_size / known_model_count
        per_gw = project_size / total_gw
        lines.extend(['', '<b>💰 자동 사업비 계산</b>'])
        lines.append(
            f'• 총사업규모 {project_size:,.0f}억달러({_krw_from_eok(project_size, fx)}) ÷ {known_model_count}기 = '
            f'<b>1기당 약 {per_unit:,.1f}억달러({_krw_from_eok(per_unit, fx)})</b>'
        )
        lines.append(
            f'• 총사업규모 ÷ {total_gw:.2f}GW = <b>1GW당 약 {per_gw:,.1f}억달러({_krw_from_eok(per_gw, fx)})</b>'
        )
        lines.append('• 단순 평균값이며 실제 개별 원전 사업비는 노형·부지·금융·송전·인허가 범위에 따라 달라짐')

    if us_proposal is not None and strategic_total is not None and strategic_total > 0:
        share = us_proposal / strategic_total * 100
        lines.extend(['', '<b>📊 대미투자 비중 자동 계산</b>'])
        lines.append(
            f'• 미국 원전 제안 {us_proposal:,.0f}억달러({_krw_from_eok(us_proposal, fx)}) ÷ '
            f'전략투자 {strategic_total:,.0f}억달러({_krw_from_eok(strategic_total, fx)}) = <b>{share:.1f}%</b>'
        )
        lines.append(
            f'• 남는 전략투자 한도 단순 계산 = <b>{strategic_total-us_proposal:,.0f}억달러({_krw_from_eok(strategic_total-us_proposal, fx)})</b>'
        )

    lines.extend([
        '',
        '<b>🏗️ 한국 기업 가치사슬 비교</b>',
        '• <b>AP1000</b>: Westinghouse 설계·노형 → 한국 기업은 주기기·설계·조달·시공·일부 기자재 참여 가능성이 핵심',
        '• <b>APR1400</b>: KHNP 사업개발·운영 → 한국전력기술 설계 → 두산에너빌리티 주기기 → 한국 건설사 시공 → 한전KPS 정비까지 한국 가치사슬 참여 폭이 더 넓어질 수 있음',
        '• 위 역할은 일반 가치사슬 비교이며 <b>미국 개별 프로젝트의 확정 계약·수주 범위는 별도 확인</b>',
    ])

    return '\n'.join(lines)


def build_alert_v12(now, changes, fx: float, fx_source: str) -> str:
    alert = _ORIGINAL_BUILD_ALERT(now, changes, fx, fx_source)
    context = _model_context(changes, fx)
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
