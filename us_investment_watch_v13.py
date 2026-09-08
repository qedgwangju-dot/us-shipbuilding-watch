#!/usr/bin/env python3
from __future__ import annotations

import datetime as dt
import re

import us_investment_watch as base
import us_investment_watch_v9 as v9
import us_investment_watch_v12 as v12

# v13: 자금 집행·웨스팅하우스 지분 인수 단계 추적
# - 특정 기사 URL이 아니라 '첫 송금/첫 납입/지분 인수' 내용 변화를 추적
# - 원문 본문이 확보된 기사에서만 사실을 누적하는 기존 v11/v12 원칙 유지
# - 송금일/송금액, 지분율/기업가치가 나오면 자동 계산
# - 웨스팅하우스 현재 소유구조 기준: Brookfield 51%, Cameco 49% (Cameco 공식자료)

for query in [
    '"대미투자" "첫 송금" when:3d',
    '"대미투자" "첫 납입" when:3d',
    '"대미투자" "29일" 송금 when:3d',
    '"웨스팅하우스 지분" 대미투자 when:3d',
    '"웨스팅하우스" 지분 인수 한국 when:3d',
    '"웨스팅하우스" 지분 매입 한국 when:3d',
]:
    if query not in base.QUERIES:
        base.QUERIES.insert(0, query)

for term in [
    '첫 송금', '첫 납입', '자금 송금', '투자금 납입',
    '웨스팅하우스 지분', '지분 인수', '지분 매입', '지분 확보',
]:
    if term not in base.MATERIAL:
        base.MATERIAL.append(term)

_ORIGINAL_EXTRACT = v9.extract_facts
_ORIGINAL_BUILD_ALERT = v9.build_alert


def _published_date(row: dict) -> dt.date | None:
    raw = str(row.get('published') or '')
    if not raw:
        return None
    try:
        return dt.datetime.fromisoformat(raw.replace('Z', '+00:00')).date()
    except Exception:
        return None


def _remittance_date(blob: str, row: dict) -> str | None:
    # 명시 날짜 우선
    m = re.search(r'(2026)[.\-/년\s]+(\d{1,2})[.\-/월\s]+(\d{1,2})\s*일?', blob)
    if m:
        return f'{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}'
    m = re.search(r'(\d{1,2})\s*월\s*(\d{1,2})\s*일.{0,50}?(?:첫\s*송금|첫\s*납입|송금|납입)', blob, re.S)
    if not m:
        m = re.search(r'(?:첫\s*송금|첫\s*납입|송금|납입).{0,50}?(\d{1,2})\s*월\s*(\d{1,2})\s*일', blob, re.S)
    if m:
        return f'2026-{int(m.group(1)):02d}-{int(m.group(2)):02d}'

    # '이달/오는 29일 첫 송금'은 기사 게시월을 기준으로 보수적으로 해석
    m = re.search(r'(?:이달\s*|오는\s*)?(\d{1,2})\s*일.{0,40}?(?:첫\s*송금|첫\s*납입)', blob, re.S)
    if not m:
        m = re.search(r'(?:첫\s*송금|첫\s*납입).{0,40}?(?:이달\s*|오는\s*)?(\d{1,2})\s*일', blob, re.S)
    if m:
        pub = _published_date(row)
        if pub:
            day = int(m.group(1))
            try:
                return dt.date(pub.year, pub.month, day).isoformat()
            except Exception:
                return None
    return None


def _nearby_usd_eok(blob: str, anchor_re: str) -> float | None:
    for m in re.finditer(anchor_re, blob, re.I):
        s = max(0, m.start() - 120)
        e = min(len(blob), m.end() + 160)
        window = blob[s:e]
        # 억달러 표기
        nums = re.findall(r'([0-9][0-9,]*(?:\.[0-9]+)?)\s*억\s*달러', window)
        if nums:
            try:
                return float(nums[0].replace(',', ''))
            except Exception:
                pass
        # 십억달러/B 표기 -> 억달러 변환
        bnums = re.findall(r'(?:\$|USD\s*)?([0-9]+(?:\.[0-9]+)?)\s*(?:billion|B)\b', window, re.I)
        if bnums:
            try:
                return float(bnums[0]) * 10.0
            except Exception:
                pass
    return None


def _acquisition_pct(blob: str) -> float | None:
    patterns = [
        r'지분\s*([0-9]+(?:\.[0-9]+)?)\s*%\s*(?:를\s*)?(?:인수|매입|확보|취득)',
        r'([0-9]+(?:\.[0-9]+)?)\s*%\s*(?:의\s*)?지분.{0,30}?(?:인수|매입|확보|취득)',
        r'(?:인수|매입|확보|취득).{0,30}?지분\s*([0-9]+(?:\.[0-9]+)?)\s*%',
    ]
    for pat in patterns:
        m = re.search(pat, blob, re.I | re.S)
        if m:
            try:
                return float(m.group(1))
            except Exception:
                return None
    return None


def _valuation_eok(blob: str) -> float | None:
    # 웨스팅하우스 기업가치가 명시된 경우만 사용. 일반 원전 사업비와 혼동 금지.
    pats = [
        r'웨스팅하우스.{0,120}?기업가치.{0,40}?([0-9][0-9,]*(?:\.[0-9]+)?)\s*억\s*달러',
        r'기업가치.{0,40}?([0-9][0-9,]*(?:\.[0-9]+)?)\s*억\s*달러.{0,120}?웨스팅하우스',
    ]
    for pat in pats:
        m = re.search(pat, blob, re.I | re.S)
        if m:
            try:
                return float(m.group(1).replace(',', ''))
            except Exception:
                return None
    return None


def extract_facts_v13(row: dict) -> list[dict]:
    out = list(_ORIGINAL_EXTRACT(row))
    title = str(row.get('title') or '')
    text = str(row.get('article_text') or '')
    source = str(row.get('resolved_link') or row.get('link') or '')
    blob = f'{title}\n{text}'
    low = blob.lower()

    if any(x in low for x in ['첫 송금', '첫 납입', '자금 송금', '투자금 납입']):
        out.append(v9.fact(
            'execution.first_remittance_reported', True,
            '대미투자 첫 송금·납입 일정 등장', '보도', source,
        ))
        date_value = _remittance_date(blob, row)
        if date_value:
            out.append(v9.fact(
                'execution.first_remittance_date', date_value,
                '대미투자 첫 송금·납입 예정일', '보도', source,
            ))
        amount = _nearby_usd_eok(blob, r'(?:첫\s*송금|첫\s*납입|자금\s*송금|투자금\s*납입)')
        if amount is not None:
            out.append(v9.fact(
                'execution.first_remittance_usd_eok', amount,
                '대미투자 첫 송금·납입 금액', '보도', source,
            ))

    if '웨스팅하우스' in low and '지분' in low and any(x in low for x in ['인수', '매입', '확보', '취득', '산다']):
        out.append(v9.fact(
            'nuclear.westinghouse_stake_acquisition_reported', True,
            '웨스팅하우스 지분 인수·매입 추진', '보도·협의 단계', source,
        ))
        pct = _acquisition_pct(blob)
        if pct is not None:
            out.append(v9.fact(
                'nuclear.westinghouse_stake_pct', pct,
                '한국 측 웨스팅하우스 지분 인수 비율', '보도', source,
            ))
        val = _valuation_eok(blob)
        if val is not None:
            out.append(v9.fact(
                'nuclear.westinghouse_valuation_usd_eok', val,
                '웨스팅하우스 기업가치 보도값', '보도', source,
            ))

    return out


def _state_value(key: str):
    try:
        st = v9.load_fact_state()
        return ((st.get('facts') or {}).get(key) or {}).get('value')
    except Exception:
        return None


def _changed_value(changes, key: str):
    for k, new, _old in changes:
        if k == key:
            return new.get('value')
    return None


def _current_value(changes, key: str):
    v = _changed_value(changes, key)
    return _state_value(key) if v is None else v


def _krw(value_eok: float, fx: float) -> str:
    try:
        return v9.krw_for_eok(float(value_eok), float(fx))
    except Exception:
        return ''


def _execution_context(now, changes, fx: float) -> str:
    keys = {k for k, _n, _o in changes}
    relevant = any(k.startswith('execution.') or k.startswith('nuclear.westinghouse_') for k in keys)
    if not relevant:
        return ''

    lines: list[str] = []
    rem_date = _current_value(changes, 'execution.first_remittance_date')
    rem_amt = _current_value(changes, 'execution.first_remittance_usd_eok')
    stake = _current_value(changes, 'nuclear.westinghouse_stake_pct')
    valuation = _current_value(changes, 'nuclear.westinghouse_valuation_usd_eok')
    stake_flag = _current_value(changes, 'nuclear.westinghouse_stake_acquisition_reported')

    if rem_date or 'execution.first_remittance_reported' in keys:
        lines.extend(['<b>💸 자금 집행 단계</b>'])
        if rem_date:
            try:
                target = dt.date.fromisoformat(str(rem_date))
                today = now.date() if hasattr(now, 'date') else dt.date.today()
                days = (target - today).days
                suffix = f' · 현재 기준 <b>D-{days}</b>' if days >= 0 else f' · <b>{abs(days)}일 경과</b>'
                lines.append(f'• 첫 송금·납입 예정일: <b>{target.strftime("%Y-%m-%d")}</b>{suffix}')
            except Exception:
                lines.append(f'• 첫 송금·납입 예정일: <b>{rem_date}</b>')
        if isinstance(rem_amt, (int, float)):
            lines.append(f'• 첫 송금·납입 금액: <b>{rem_amt:,.1f}억달러({_krw(rem_amt, fx)})</b>')
        else:
            lines.append('• 첫 송금 금액은 원문에서 별도 확인 필요')
        lines.append('• 반드시 <b>프로젝트 자금요청·SPV 출자·예치금·기타 송금 중 어떤 법적 성격인지</b> 구분')

    if stake_flag or any(k.startswith('nuclear.westinghouse_') for k in keys):
        if lines:
            lines.append('')
        lines.extend([
            '<b>🏢 웨스팅하우스 지분</b>',
            '• 현재 공식 소유구조: <b>Brookfield 51% · Cameco 49%</b>',
        ])
        if isinstance(stake, (int, float)):
            lines.append(f'• 한국 측 인수 보도 지분율: <b>{stake:.2f}%</b>')
        else:
            lines.append('• 한국 측 지분율·매입가격·매도주체는 아직 확정 확인 필요')
        if isinstance(stake, (int, float)) and isinstance(valuation, (int, float)):
            cost = valuation * stake / 100.0
            lines.append(
                f'• 기업가치 {valuation:,.0f}억달러 기준 단순 지분대금 = '
                f'<b>{cost:,.1f}억달러({_krw(cost, fx)})</b>'
            )
        lines.append('• 지분율만큼 중요한 것: <b>이사회 의석·의결권·APR1400 사업권·지식재산권·한국 기자재 우선권</b>')

    return '\n'.join(lines)


def build_alert_v13(now, changes, fx: float, fx_source: str) -> str:
    alert = _ORIGINAL_BUILD_ALERT(now, changes, fx, fx_source)
    context = _execution_context(now, changes, fx)
    if not context:
        return alert
    marker = f'\n\n<a href="{base.MOU_OFFICIAL_URL}"><b>산업통상부 한미 전략투자 MOU</b></a>'
    if marker in alert:
        return alert.replace(marker, f'\n\n{context}{marker}', 1)
    return alert + '\n\n' + context


v9.extract_facts = extract_facts_v13
v9.build_alert = build_alert_v13


def main() -> int:
    # v12 import 시 AP1000/APR1400 고정 비교와 자동 계산 기능도 함께 유지
    return v12.v11.main()


if __name__ == '__main__':
    raise SystemExit(main())
