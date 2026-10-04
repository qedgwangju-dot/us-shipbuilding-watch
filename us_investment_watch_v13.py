#!/usr/bin/env python3
from __future__ import annotations

import datetime as dt
import re
import sys

import us_investment_watch as base
import us_investment_watch_v9 as v9
import us_investment_watch_v12 as v12

# v13: 자금 집행·웨스팅하우스 지분 인수 단계 추적
# - 특정 기사 URL이 아니라 '첫 송금/첫 납입/지분 인수' 내용 변화를 추적
# - 원문 본문이 확보된 기사에서만 사실을 누적하는 기존 v11/v12 원칙 유지
# - 송금일/송금액, 지분율/기업가치가 나오면 자동 계산
# - 웨스팅하우스 현재 소유구조 기준: Brookfield 51%, Cameco 49% (Cameco 공식자료)

# v14-compatible upgrade inside the active v13 route:
# Project Power official framework is tracked as a milestone state machine.
# Do not create a second Telegram owner: the Janus workflow delegates U.S. nuclear-build
# alerts to this watcher, so this remains the single owner for Project Power.
PROJECT_POWER_MOTIR_URL = "https://www.motir.go.kr/kor/article/ATCL3f49a5a8c/172253/view"
PROJECT_POWER_WESTINGHOUSE_URL = "https://info.westinghousenuclear.com/news/u.s.-korea-framework-advances-deployment-of-westinghouse-nuclear-technology-in-the-united-states"
PROJECT_POWER_CAMECO_URL = "https://www.cameco.com/media/news/cameco-acknowledges-united-states-and-republic-of-korea-announcement-of-framework-for"

_ORIGINAL_RSS_ITEMS = base.rss_items


def _project_power_official_rows(now: dt.datetime) -> list[dict]:
    published = now.astimezone(base.UTC).isoformat()
    return [
        {
            "id": "project_power_official_motir_172253",
            "title": "한미 전략투자 프로젝트 추진 계획 발표",
            "description": "Project Power 한미 원전 프레임워크 공식 원문",
            "source": "산업통상부",
            "link": PROJECT_POWER_MOTIR_URL,
            "published": published,
            "tags": ["원전", "Project Power"],
        },
        {
            "id": "project_power_official_westinghouse_framework",
            "title": "U.S. – Korea Framework Advances Deployment of Westinghouse Nuclear Technology in the United States",
            "description": "Westinghouse 공식 Project Power 프레임워크",
            "source": "Westinghouse",
            "link": PROJECT_POWER_WESTINGHOUSE_URL,
            "published": published,
            "tags": ["원전", "Project Power", "Westinghouse"],
        },
        {
            "id": "project_power_official_cameco_framework",
            "title": "Cameco acknowledges United States and Republic of Korea announcement of Framework for the Deployment of Nuclear Power",
            "description": "Cameco 공식 Project Power 프레임워크",
            "source": "Cameco",
            "link": PROJECT_POWER_CAMECO_URL,
            "published": published,
            "tags": ["원전", "Project Power", "Cameco"],
        },
    ]


def _rss_items_v13(now: dt.datetime) -> list[dict]:
    rows = _project_power_official_rows(now) + list(_ORIGINAL_RSS_ITEMS(now))
    out: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        key = str(row.get("id") or "")
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        out.append(row)
    return out


base.rss_items = _rss_items_v13
v9.OFFICIAL_SOURCE_MARKERS = tuple(dict.fromkeys(
    list(v9.OFFICIAL_SOURCE_MARKERS)
    + ["info.westinghousenuclear.com", "cameco.com", "brookfield.com", "bam.brookfield.com"]
))

for query in [
    '"대미투자" "첫 송금" when:3d',
    '"대미투자" "첫 납입" when:3d',
    '"대미투자" "29일" 송금 when:3d',
    '"웨스팅하우스 지분" 대미투자 when:3d',
    '"웨스팅하우스" 지분 인수 한국 when:3d',
    '"웨스팅하우스" 지분 매입 한국 when:3d',
    '"Project Power" AP1000 APR1400 when:7d',
    '"한미 원전 프레임워크" AP1000 APR1400 when:7d',
    '"AP1000 6기" "APR1400 2기" when:7d',
    '"장주기 기자재" 원전 선지급 when:7d',
    '"장납기 기자재" 원전 선지급 when:7d',
    '"Westinghouse" Korea "5%" "10%" when:7d',
    '"웨스팅하우스" "5%" "10%" 한국 when:7d',
    '"definitive agreements" Korea Westinghouse nuclear when:7d',
    '"federal sites" AP1000 Korea when:7d',
]:
    if query not in base.QUERIES:
        base.QUERIES.insert(0, query)

for term in [
    '첫 송금', '첫 납입', '자금 송금', '투자금 납입',
    '웨스팅하우스 지분', '지분 인수', '지분 매입', '지분 확보',
    'Project Power', '한미 원전 프레임워크', '장주기 기자재', '장납기 기자재',
    '선지급', '최종계약', 'definitive agreement', 'federal sites',
    'cornerstone equity', 'AP1000', 'APR1400',
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
    """송금/납입 표현과 같은 문장·구절에 직접 연결된 날짜만 읽는다."""
    anchor = r'(?:첫\s*송금|첫\s*납입|자금\s*송금|투자금\s*납입)'
    same_clause = r'[^\n.!?。]{0,25}'
    patterns = [
        rf'{anchor}{same_clause}(2026)[.\-/년\s]+(\d{{1,2}})[.\-/월\s]+(\d{{1,2}})\s*일?',
        rf'(2026)[.\-/년\s]+(\d{{1,2}})[.\-/월\s]+(\d{{1,2}})\s*일?{same_clause}{anchor}',
    ]
    for pat in patterns:
        m = re.search(pat, blob, re.I)
        if m:
            return f'{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}'

    m = re.search(rf'{anchor}{same_clause}(\d{{1,2}})\s*월\s*(\d{{1,2}})\s*일', blob, re.I)
    if not m:
        m = re.search(rf'(\d{{1,2}})\s*월\s*(\d{{1,2}})\s*일{same_clause}{anchor}', blob, re.I)
    if m:
        return f'2026-{int(m.group(1)):02d}-{int(m.group(2)):02d}'

    # 월이 생략된 경우에도 같은 구절 안에 있는 날짜만 기사 게시월과 결합한다.
    same_short_clause = r'[^\n.!?。]{0,20}'
    m = re.search(rf'{anchor}{same_short_clause}(?:이달\s*|오는\s*)?(\d{{1,2}})\s*일', blob, re.I)
    if not m:
        m = re.search(rf'(?:이달\s*|오는\s*)?(\d{{1,2}})\s*일{same_short_clause}{anchor}', blob, re.I)
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
    """자금집행 표현과 직접 결합된 금액만 읽는다.

    '3,500억달러 전체 대미투자 ... 첫 송금'처럼 멀리 떨어진 배경 숫자는 제외한다.
    """
    number = r'([0-9][0-9,]*(?:\.[0-9]+)?)\s*억\s*달러'
    for m in re.finditer(anchor_re, blob, re.I):
        before = blob[max(0, m.start() - 35):m.start()]
        after = blob[m.end():min(len(blob), m.end() + 35)]
        for window in (after, before):
            n = re.search(number, window, re.I)
            if n:
                try:
                    value = float(n.group(1).replace(',', ''))
                except Exception:
                    continue
                # 전체 약속액 3,500억달러는 첫 송금 금액일 수 없다.
                if value >= 2000:
                    continue
                return value
            b = re.search(r'(?:\$|USD\s*)?([0-9]+(?:\.[0-9]+)?)\s*(?:billion|B)\b', window, re.I)
            if b:
                value = float(b.group(1)) * 10.0
                if value >= 2000:
                    continue
                return value
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

    official_project_power = (
        ('172253' in source and 'motir.go.kr' in source.lower())
        or ('info.westinghousenuclear.com' in source.lower() and 'framework' in low and 'korea' in low)
        or ('cameco.com' in source.lower() and 'framework' in low and 'korea' in low)
        or ('project power' in low and ('motir.go.kr' in source.lower() or 'go.kr' in source.lower()))
    )
    framework_context = (
        'project power' in low
        or '한미 원전 프레임워크' in low
        or ('framework' in low and 'ap1000' in low and 'apr1400' in low and 'korea' in low)
    )

    if official_project_power:
        out.append(v9.fact(
            'nuclear.project_power_framework_official', True,
            'Project Power 한미 원전 프레임워크 공식화', '정부·기업 공식자료', source,
        ))
        if re.search(r'(?:\$|us\$?\s*)?120\s*billion|1,?200\s*억\s*달러', blob, re.I):
            out.append(v9.fact(
                'nuclear.framework_investment_cap_usd_eok', 1200,
                'Project Power 한국 투자 상한', '정부·기업 공식자료', source,
            ))
            # 과거 "1,200억달러는 한국 확약액이 아님" 상태를 공식 발표로 해제한다.
            out.append(v9.fact(
                'nuclear.us_120b_not_committed', False,
                '1,200억달러 한국 투자 프레임워크 공식화', '정부·기업 공식자료', source,
            ))
        if re.search(r'(?:six|6)\s+(?:westinghouse\s+)?ap1000|ap1000\s*6\s*기', blob, re.I):
            out.append(v9.fact(
                'nuclear.ap1000_reactors', 6,
                'AP1000 공식 포함 기수', '정부·기업 공식자료', source,
            ))
        if re.search(r'(?:two|2)\s+(?:korean\s+)?apr1400|apr1400\s*(?:최대\s*)?2\s*기', blob, re.I):
            out.append(v9.fact(
                'nuclear.apr1400_reactors', 2,
                'APR1400 공식 포함 기수', '정부·기업 공식자료', source,
            ))
        if re.search(r'(?:eight|8)\s+(?:large\s+)?nuclear\s+reactors|대형원전\s*8\s*기|원전\s*(?:최대\s*)?8\s*기', blob, re.I):
            out.append(v9.fact(
                'nuclear.reactors', 8,
                'Project Power 미국 대형원전 총 기수', '정부·기업 공식자료', source,
            ))
        out.append(v9.fact(
            'package.nuclear_framework_included', True,
            '원전 8기 Project Power 프레임워크 포함', '정부·기업 공식자료', source,
        ))

    if framework_context:
        if re.search(r'beginning\s+with\s+the\s+deployment\s+of\s+two\s+ap1000|❶\s*ap1000\s*\(2\s*기\)|1\s*단계[^\n]{0,80}ap1000[^\n]{0,30}2\s*기', blob, re.I):
            out.append(v9.fact(
                'nuclear.project_power_phase1_ap1000_reactors', 2,
                'Project Power 1단계 AP1000 기수', '공식 프레임워크', source,
            ))
        if 'federal sites' in low or '연방정부' in low:
            out.append(v9.fact(
                'nuclear.project_power_federal_sites', True,
                '미국 연방정부 지정 부지 배치 방향', '공식 프레임워크', source,
            ))
        if re.search(r'non[- ]binding|terms\s+of\s+the\s+transaction\s+are\s+non', blob, re.I):
            out.append(v9.fact(
                'nuclear.project_power_terms_nonbinding', True,
                'Project Power 세부 거래조건 비구속', '기업 공식자료', source,
            ))
        if re.search(r'subject\s+to\s+(?:the\s+)?(?:negotiation\s+and\s+execution\s+of\s+)?definitive\s+agreements|subject\s+to\s+final\s+negotiations', blob, re.I):
            out.append(v9.fact(
                'nuclear.project_power_definitive_agreements_required', True,
                '개별 최종계약·최종협상 필요', '기업 공식자료', source,
            ))
        if re.search(r'cornerstone\s+equity\s+investment[^\n]{0,80}(?:between\s+)?5\s*%[^\n]{0,30}10\s*%', blob, re.I):
            out.extend([
                v9.fact(
                    'nuclear.westinghouse_stake_pct_min', 5,
                    '한국 측 Westinghouse 지분투자 하한', '기업 공식 프레임워크', source,
                ),
                v9.fact(
                    'nuclear.westinghouse_stake_pct_max', 10,
                    '한국 측 Westinghouse 지분투자 상한', '기업 공식 프레임워크', source,
                ),
            ])
        if re.search(r'upfront\s+payment', blob, re.I) and re.search(r'guaranteed\s+scope\s+of\s+work', blob, re.I):
            out.append(v9.fact(
                'nuclear.project_power_apr1400_westinghouse_scope', True,
                'APR1400에서 Westinghouse 선급금·보장 업무범위', '기업 공식 프레임워크', source,
            ))
        if re.search(r'fuel[- ]fabrication\s+services', blob, re.I):
            out.append(v9.fact(
                'nuclear.project_power_westinghouse_fuel_services', True,
                'APR1400 Westinghouse 핵연료 가공 서비스 계약 경로', '기업 공식 프레임워크', source,
            ))
        if re.search(r'waiver\s+under\s+the\s+2025\s+settlement|2025[^\n]{0,80}settlement[^\n]{0,80}waiver', blob, re.I):
            out.append(v9.fact(
                'nuclear.project_power_settlement_waiver_contemplated', True,
                '2025 지식재산권 합의 예외 적용 예정', '기업 공식 프레임워크', source,
            ))
        if re.search(r'\$\s*17\.5\s*billion|17\.5\s*billion', blob, re.I) and re.search(r'long[- ]lead', blob, re.I):
            out.append(v9.fact(
                'nuclear.project_power_doe_long_lead_conditional_loan_usd_eok', 175,
                'DOE 장주기 기자재 조건부 대출지원', '기업 공식자료', source,
            ))
        if re.search(r'(?:100\s*억\s*달러|\$\s*10\s*billion|10\s*billion)', blob, re.I) and re.search(r'선지급|장주기|장납기|long[- ]lead', blob, re.I):
            status = '정부 공식 검토' if re.search(r'검토|예정|consider|could|may', blob, re.I) else '정부·기업 공식자료'
            out.append(v9.fact(
                'nuclear.project_power_long_lead_prepayment_cap_usd_eok', 100,
                '장주기 기자재 선지급 검토 상한', status, source,
            ))

        # Future milestone promotion: only explicit completion language can advance a stage.
        if re.search(r'한미\s*원전\s*프레임워크[^\n]{0,80}(?:최종\s*)?(?:서명|체결)\s*(?:완료|했다|되었다)|framework[^\n]{0,80}(?:was|has\s+been)\s+signed', blob, re.I):
            if not re.search(r'서명할\s*예정|will\s+sign|expected\s+to\s+sign', blob, re.I):
                out.append(v9.fact(
                    'nuclear.project_power_signature_complete', True,
                    'Project Power 최종 서명 완료', '공식·교차검증 필요', source,
                ))
        if re.search(r'(?:waiver|예외)[^\n]{0,100}(?:granted|executed|effective|완료|확정|체결)', blob, re.I):
            out.append(v9.fact(
                'nuclear.project_power_settlement_waiver_complete', True,
                '2025 지식재산권 합의 예외 적용 완료', '공식·교차검증 필요', source,
            ))
        if re.search(r'(?:장주기|장납기|long[- ]lead)', blob, re.I) and re.search(r'(?:purchase\s+order|발주\s*(?:완료|확정|계약)|선지급\s*(?:완료|집행)|payment\s+made)', blob, re.I):
            if not re.search(r'검토|계획|예정|consider|may|could', blob, re.I):
                out.append(v9.fact(
                    'nuclear.project_power_long_lead_po_complete', True,
                    '장주기 기자재 실제 발주·선지급 발생', '공식·교차검증 필요', source,
                ))
        if re.search(r'ap1000', blob, re.I) and re.search(r'(?:부지|site)[^\n]{0,80}(?:확정|선정|selected|designated)', blob, re.I):
            out.append(v9.fact(
                'nuclear.project_power_phase1_site_selected', True,
                '1단계 AP1000 부지 선정', '공식·교차검증 필요', source,
            ))
        if re.search(r'ap1000', blob, re.I) and re.search(r'(?:epc|설계.{0,8}조달.{0,8}시공)[^\n]{0,100}(?:계약\s*체결|contract\s+(?:signed|awarded)|award)', blob, re.I):
            out.append(v9.fact(
                'nuclear.project_power_phase1_epc_contract', True,
                '1단계 AP1000 EPC 본계약', '공식·교차검증 필요', source,
            ))
        if re.search(r'westinghouse', low) and re.search(r'(?:5\s*%|10\s*%|지분)', blob, re.I) and re.search(r'(?:acquisition\s+closed|closing\s+completed|인수\s*완료|매입\s*완료|취득\s*완료)', blob, re.I):
            out.append(v9.fact(
                'nuclear.project_power_westinghouse_equity_closed', True,
                '한국 측 Westinghouse 지분투자 거래종결', '공식·교차검증 필요', source,
            ))
        if re.search(r'apr[- ]?1400', blob, re.I) and re.search(r'(?:definitive\s+agreement|epc\s+contract|본계약|최종계약)[^\n]{0,80}(?:signed|executed|체결|확정)', blob, re.I):
            out.append(v9.fact(
                'nuclear.project_power_apr1400_contract', True,
                'APR1400 미국 사업 본계약', '공식·교차검증 필요', source,
            ))

    if any(x in low for x in ['첫 송금', '첫 납입', '자금 송금', '투자금 납입']):
        negated = any(x in low for x in [
            '송금 규모·시기, 1호 사업 발표 등은 확정된 바가 없습니다',
            '송금 규모·시기는 확정된 바가 없습니다',
            '송금 규모와 시기는 확정된 바가 없습니다',
            '송금 규모·시기 미확정',
            '송금 규모와 시기 미확정',
        ])
        if negated:
            out.append(v9.fact(
                'execution.official_remittance_not_final', True,
                '정부: 대미투자 송금 규모·시기 미확정', '공식 설명', source,
            ))
        else:
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
    stake_min = _current_value(changes, 'nuclear.westinghouse_stake_pct_min')
    stake_max = _current_value(changes, 'nuclear.westinghouse_stake_pct_max')
    # 2026-09-30 공식 프레임워크의 5~10% 범위가 존재하면 과거 15% 보도값을
    # 현재 확정 지분율처럼 다시 노출하지 않는다.
    official_stake_range = (
        isinstance(stake_min, (int, float)) and isinstance(stake_max, (int, float))
    )
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
        if official_stake_range:
            lines.append(
                f'• 공식 프레임워크 지분투자 범위: <b>{stake_min:g}~{stake_max:g}%</b> '
                '· 과거 15% 보도값은 현재 기준으로 사용하지 않음'
            )
            lines.append('• 정확한 인수가·매도주체·거래종결 조건은 아직 최종 확정 전')
        elif isinstance(stake, (int, float)):
            lines.append(f'• 한국 측 인수 보도 지분율: <b>{stake:.2f}%</b>')
        else:
            lines.append('• 한국 측 지분율·매입가격·매도주체는 아직 확정 확인 필요')
        if (not official_stake_range) and isinstance(stake, (int, float)) and isinstance(valuation, (int, float)):
            cost = valuation * stake / 100.0
            lines.append(
                f'• 기업가치 {valuation:,.0f}억달러 기준 단순 지분대금 = '
                f'<b>{cost:,.1f}억달러({_krw(cost, fx)})</b>'
            )
        lines.append('• 지분율만큼 중요한 것: <b>이사회 의석·의결권·APR1400 사업권·지식재산권·한국 기자재 우선권</b>')

    return '\n'.join(lines)


def _project_power_context(changes, fx: float) -> str:
    keys = {k for k, _n, _o in changes}
    relevant = any(k.startswith('nuclear.project_power_') for k in keys) or any(
        k in keys for k in [
            'nuclear.framework_investment_cap_usd_eok',
            'nuclear.ap1000_reactors',
            'nuclear.apr1400_reactors',
            'nuclear.reactors',
            'nuclear.us_120b_not_committed',
            'nuclear.westinghouse_stake_pct_min',
            'nuclear.westinghouse_stake_pct_max',
        ]
    )
    if not relevant:
        return ''

    framework = bool(_current_value(changes, 'nuclear.project_power_framework_official'))
    signed = bool(_current_value(changes, 'nuclear.project_power_signature_complete'))
    waiver = bool(_current_value(changes, 'nuclear.project_power_settlement_waiver_complete'))
    longlead = bool(_current_value(changes, 'nuclear.project_power_long_lead_po_complete'))
    site = bool(_current_value(changes, 'nuclear.project_power_phase1_site_selected'))
    epc = bool(_current_value(changes, 'nuclear.project_power_phase1_epc_contract'))
    equity = bool(_current_value(changes, 'nuclear.project_power_westinghouse_equity_closed'))
    apr_contract = bool(_current_value(changes, 'nuclear.project_power_apr1400_contract'))
    cap = _current_value(changes, 'nuclear.framework_investment_cap_usd_eok')
    ap = _current_value(changes, 'nuclear.ap1000_reactors')
    apr = _current_value(changes, 'nuclear.apr1400_reactors')
    stake_min = _current_value(changes, 'nuclear.westinghouse_stake_pct_min')
    stake_max = _current_value(changes, 'nuclear.westinghouse_stake_pct_max')
    prepay = _current_value(changes, 'nuclear.project_power_long_lead_prepayment_cap_usd_eok')

    def mark(value: bool) -> str:
        return '완료' if value else '대기'

    lines = ['<b>⚛️ Project Power 단계 추적</b>']
    if isinstance(cap, (int, float)):
        lines.append(f'• 공식 프레임워크 투자 상한: <b>{cap:,.0f}억달러({_krw(cap, fx)})</b>')
    if isinstance(ap, (int, float)) and isinstance(apr, (int, float)):
        lines.append(f'• 노형 구성: <b>AP1000 {ap:g}기 + APR1400 {apr:g}기</b>')
    if isinstance(stake_min, (int, float)) and isinstance(stake_max, (int, float)):
        lines.append(f'• Westinghouse 지분투자 경로: <b>{stake_min:g}~{stake_max:g}%</b> · 거래종결 전')
    if isinstance(prepay, (int, float)):
        lines.append(f'• 장주기 기자재 선지급 검토 상한: <b>{prepay:,.0f}억달러({_krw(prepay, fx)})</b> · 실제 집행과 구분')
    lines.extend([
        f'• ① 공식 프레임워크: <b>{mark(framework)}</b>',
        f'• ② 최종 서명: <b>{mark(signed)}</b>',
        f'• ③ 2025 지식재산권 합의 예외 적용 완료: <b>{mark(waiver)}</b>',
        f'• ④ 장주기 기자재 실제 PO·선지급: <b>{mark(longlead)}</b>',
        f'• ⑤ 1단계 AP1000 부지 선정: <b>{mark(site)}</b>',
        f'• ⑥ 1단계 AP1000 EPC 본계약: <b>{mark(epc)}</b>',
        f'• ⑦ Westinghouse 지분투자 거래종결: <b>{mark(equity)}</b>',
        f'• ⑧ APR1400 미국 본계약: <b>{mark(apr_contract)}</b>',
        '• 같은 단계의 반복기사·주가반응은 재전송하지 않고 <b>위 단계가 실제로 상승할 때만</b> 다시 알림',
    ])
    return '\n'.join(lines)


def build_alert_v13(now, changes, fx: float, fx_source: str) -> str:
    alert = _ORIGINAL_BUILD_ALERT(now, changes, fx, fx_source)
    blocks = [x for x in [_project_power_context(changes, fx), _execution_context(now, changes, fx)] if x]
    if not blocks:
        return alert
    context = '\n\n'.join(blocks)
    marker = f'\n\n<a href="{base.MOU_OFFICIAL_URL}"><b>산업통상부 한미 전략투자 MOU</b></a>'
    if marker in alert:
        return alert.replace(marker, f'\n\n{context}{marker}', 1)
    return alert + '\n\n' + context


v9.extract_facts = extract_facts_v13
v9.build_alert = build_alert_v13


def main() -> int:
    # v12 import 시 AP1000/APR1400 고정 비교와 자동 계산 기능도 함께 유지
    return v12.v11.main()


def _self_test() -> int:
    sample = {
        'title': '전체 3,500억달러 대미투자…첫 송금 규모·시기는 미확정',
        'article_text': '2026-07-24 협상 자료. 정부는 첫 송금 규모·시기는 확정된 바가 없습니다.',
        'source': '대한민국 정책브리핑',
        'resolved_link': 'https://www.korea.kr/example',
        'published': '2026-09-20T00:00:00+00:00',
    }
    blob = sample['title'] + '\n' + sample['article_text']
    if _remittance_date(blob, sample) is not None:
        raise RuntimeError('unrelated date leaked into remittance date')
    if _nearby_usd_eok(blob, r'(?:첫\s*송금|첫\s*납입|자금\s*송금|투자금\s*납입)') is not None:
        raise RuntimeError('total investment leaked into remittance amount')
    keys = {x['key'] for x in extract_facts_v13(sample)}
    if 'execution.official_remittance_not_final' not in keys:
        raise RuntimeError(f'official remittance negation not captured: {keys}')
    if 'execution.first_remittance_usd_eok' in keys or 'execution.first_remittance_date' in keys:
        raise RuntimeError(f'negated remittance produced concrete value: {keys}')

    reactor = '미국 원전 전체 8기 가운데 AP1000 6기, APR1400 2기 검토'
    if v12._reactor_count(reactor, 'AP1000') != 6:
        raise RuntimeError('AP1000 6-unit parse regression')
    if v12._reactor_count(reactor, 'APR1400') != 2:
        raise RuntimeError('APR1400 2-unit parse regression')
    ambiguous = '미국 원전 전체 8기 검토. AP1000과 APR1400 노형을 협의'
    if v12._reactor_count(ambiguous, 'AP1000') is not None or v12._reactor_count(ambiguous, 'APR1400') is not None:
        raise RuntimeError('total reactor count leaked into model count')

    official = {
        'title': 'U.S. – Korea Framework Advances Deployment of Westinghouse Nuclear Technology in the United States',
        'article_text': (
            'The framework agreement commits up to $120 billion of investment by Korea to help finance '
            'eight large nuclear reactors, including six Westinghouse AP1000 reactors and two Korean APR1400 reactors. '
            'The reactors will be deployed on federal sites. The framework agreement also provides for a cornerstone '
            'equity investment of between 5% and 10% in Westinghouse by Korea. Terms of the transaction are non-binding '
            'and are subject to final negotiations. Westinghouse will benefit from an upfront payment, guaranteed scope '
            'of work and a contract to provide fuel-fabrication services.'
        ),
        'source': 'Westinghouse',
        'resolved_link': PROJECT_POWER_WESTINGHOUSE_URL,
        'published': '2026-10-01T00:00:00+00:00',
    }
    ofacts = {x['key']: x['value'] for x in extract_facts_v13(official)}
    expected = {
        'nuclear.project_power_framework_official': True,
        'nuclear.framework_investment_cap_usd_eok': 1200,
        'nuclear.ap1000_reactors': 6,
        'nuclear.apr1400_reactors': 2,
        'nuclear.reactors': 8,
        'nuclear.us_120b_not_committed': False,
        'nuclear.project_power_federal_sites': True,
        'nuclear.project_power_terms_nonbinding': True,
        'nuclear.westinghouse_stake_pct_min': 5,
        'nuclear.westinghouse_stake_pct_max': 10,
    }
    for key, value in expected.items():
        if ofacts.get(key) != value:
            raise RuntimeError(f'project power official parse regression: {key}={ofacts.get(key)!r}')

    planned = {
        'title': '한미 원전 프레임워크 서명 예정',
        'article_text': '한미 원전 프레임워크에는 한국전력과 한국수력원자력이 서명할 예정이다.',
        'source': '산업통상부',
        'resolved_link': PROJECT_POWER_MOTIR_URL,
        'published': '2026-10-01T00:00:00+00:00',
    }
    pkeys = {x['key'] for x in extract_facts_v13(planned)}
    if 'nuclear.project_power_signature_complete' in pkeys:
        raise RuntimeError('planned signature promoted to completed milestone')

    print('us_investment_v13_self_test=passed project_power_milestones=passed')
    return 0


if __name__ == '__main__':
    if '--self-test' in sys.argv:
        raise SystemExit(_self_test())
    raise SystemExit(main())
