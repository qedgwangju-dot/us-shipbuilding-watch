#!/usr/bin/env python3
from __future__ import annotations

import re

import us_investment_watch as base
import us_investment_watch_v9 as v9
import us_investment_watch_v11 as v11

# v12: 원전 노형 오인 방지 + 규제상태/출력 맥락 강화
# - AP1000(웨스팅하우스)과 APR1000(한국형)은 완전히 다른 노형으로 분리
# - APR1000이 기사에 등장하면 별도 사실로 누적
# - 미국 사업 알림에서 노형별 NRC/EUR 상태와 명목 출력 차이를 함께 보여줌
# - 현재 보도상 6×AP1000 + 2×APR1400 같은 조합은 명목출력까지 검산

for query in [
    '"APR1000" 미국 원전 한국 when:3d',
    '"APR1000" 대미투자 when:3d',
    '"AP1000" "APR1000" 원전 when:3d',
]:
    if query not in base.QUERIES:
        base.QUERIES.insert(0, query)

for term in ["APR1000", "APR1000 2기", "APR1000 미국", "APR1000 NRC"]:
    if term not in base.MATERIAL:
        base.MATERIAL.append(term)

_ORIGINAL_EXTRACT = v9.extract_facts
_ORIGINAL_BUILD_ALERT = v9.build_alert


def _reactor_count(blob: str, model: str) -> int | None:
    pats = [
        rf'{re.escape(model)}.{{0,50}}?(\d+)\s*기',
        rf'(\d+)\s*기.{{0,50}}?{re.escape(model)}',
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

    # APR1000은 AP1000과 별도 노형. 기사에 실제 등장할 때만 누적한다.
    if re.search(r'\bAPR1000\b', blob, re.I):
        out.append(v9.fact(
            'nuclear.apr1000_mentioned', True,
            'APR1000 별도 노형 언급', '보도', source,
        ))
        count = _reactor_count(blob, 'APR1000')
        if count is not None:
            out.append(v9.fact(
                'nuclear.apr1000_reactors', count,
                'APR1000 포함 기수', '보도', source,
            ))
        if any(x in low for x in ['eur', '유럽사업자요건', '유럽 인증']):
            out.append(v9.fact(
                'nuclear.apr1000_eur_context', True,
                'APR1000 유럽사업자요건 인증 맥락', '공식자료 교차확인 필요', source,
            ))
        if 'nrc' in low or '미국 원자력규제위원회' in low:
            out.append(v9.fact(
                'nuclear.apr1000_us_regulatory_issue_mentioned', True,
                'APR1000 미국 인허가 쟁점 언급', '보도', source,
            ))

    return out


def _changed_value(changes, key: str):
    for k, new, _old in changes:
        if k == key:
            return new.get('value')
    return None


def _model_context(changes) -> str:
    keys = {k for k, _n, _o in changes}
    model_change = any(k in keys for k in [
        'nuclear.ap1000_reactors',
        'nuclear.apr1000_reactors',
        'nuclear.apr1000_mentioned',
        'nuclear.apr1400_reactors',
    ])
    if not model_change:
        return ''

    lines = [
        '<b>⚙️ 노형 판별</b>',
        '• <b>AP1000</b> = 웨스팅하우스 미국 노형 · 명목 순전기출력 약 1.11GW · 미국 NRC 설계인증',
        '• <b>APR1400</b> = 한국형 대형 노형 · 1.4GW급 · 2019년 미국 NRC 설계인증',
        '• <b>APR1000</b> = 한국형 1.05GW급 중형 노형 · 2023년 유럽사업자요건 인증 · 현재 미국 NRC 설계인증 목록에는 없음',
    ]

    ap = _changed_value(changes, 'nuclear.ap1000_reactors')
    apr14 = _changed_value(changes, 'nuclear.apr1400_reactors')
    if isinstance(ap, (int, float)) and isinstance(apr14, (int, float)):
        total_gw = float(ap) * 1.11 + float(apr14) * 1.4
        lines.append(
            f'• 현재 변화값 단순 합산: AP1000 {int(ap)}기 + APR1400 {int(apr14)}기 ≈ <b>{total_gw:.2f}GW</b> 명목 출력'
        )
    lines.append('• 미국 프로젝트에서 APR1000이 새로 거론되면 AP1000 오기인지, 실제 한국형 APR1000 제안인지 원문 본문에서 다시 구분')
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
