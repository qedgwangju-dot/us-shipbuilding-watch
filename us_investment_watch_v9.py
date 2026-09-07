#!/usr/bin/env python3
from __future__ import annotations

import datetime as dt
import html
import json
import re
from pathlib import Path

import us_investment_watch as base
import us_investment_watch_v3 as v3
import us_investment_watch_v5 as v5

FACT_STATE = Path(__file__).resolve().parent / "us_investment_fact_state.json"

# 핵심 원칙
# 1) 특정 기사 URL/제목을 감시 대상으로 고정하지 않는다.
# 2) 기사는 '근거'일 뿐이고 알림 단위는 기존 누적 장부에서 새로 생기거나 바뀐 사실이다.
# 3) 같은 사실을 다룬 반복 기사는 알리지 않는다.
# 4) 원문 본문을 확보한 기사만 사실 추출에 사용한다.
# 5) 달러 금액은 Telegram에서 항상 실행 시점 원화로 병기한다.

CONTENT_QUERIES = [
    '"대미투자" when:2d',
    '"한미 전략적 투자" when:3d',
    '"한미전략투자" when:3d',
    '엔시날 가스발전 한국 미국 투자 when:3d',
    '"원전 8기" 미국 한국 투자 when:3d',
    'AP1000 APR1400 미국 원전 한국 when:3d',
    '"한국형 원전" 미국 8기 when:3d',
    '"1200억달러" 원전 미국 한국 when:3d',
    '"1300억달러" 원전 미국 한국 when:3d',
    '알래스카 LNG 한국 투자 POSCO when:3d',
    '미국 반도체 투자 요구 한국 삼성전자 SK하이닉스 when:3d',
    'I-SPV 리스크 풀링 대미투자 when:3d',
    '대미투자 PPA EPC 가스터빈 when:3d',
    '대미투자 301조 232조 반도체 관세 when:3d',
]
for q in reversed(CONTENT_QUERIES):
    if q not in base.QUERIES:
        base.QUERIES.insert(0, q)

for source in [
    "머니투데이", "서울경제", "조선비즈", "아시아경제", "파이낸셜뉴스", "매일경제", "한국경제",
    "이데일리", "헤럴드경제", "연합뉴스", "뉴시스", "뉴스1", "중앙일보", "서울신문",
]:
    if source not in base.TRUSTED:
        base.TRUSTED.append(source)

for term in [
    "원전 8기", "한국형 원전", "AP1000", "APR1400", "1200억달러", "1300억달러",
    "엔시날", "I-SPV", "리스크 풀링", "PPA", "최종 서명", "최종 사인", "국회 보고",
    "반도체 투자", "알래스카 LNG", "301조", "232조", "증액", "감액", "사업비",
]:
    if term not in base.MATERIAL:
        base.MATERIAL.append(term)


def load_fact_state() -> dict:
    if not FACT_STATE.exists():
        return {"version": 2, "facts": {}}
    try:
        obj = json.loads(FACT_STATE.read_text(encoding="utf-8"))
        if not isinstance(obj, dict):
            raise ValueError
    except Exception:
        return {"version": 2, "facts": {}}
    obj.setdefault("facts", {})
    return obj


def krw_text_from_won(won: float) -> str:
    sign = "-" if won < 0 else ""
    won = abs(won)
    jo = int(won // 1_000_000_000_000)
    eok = int(round((won - jo * 1_000_000_000_000) / 100_000_000))
    if eok >= 10_000:
        jo += eok // 10_000
        eok %= 10_000
    if jo:
        return f"{sign}약 {jo}조 {eok:,}억원" if eok else f"{sign}약 {jo}조원"
    return f"{sign}약 {eok:,}억원"


def krw_for_eok(value: float, fx: float) -> str:
    return krw_text_from_won(float(value) * 100_000_000 * fx)


def status_rank(status: str) -> int:
    s = status.lower()
    if "공식" in s or "확정" in s:
        return 5
    if "합의" in s or "청와대 확인" in s or "정부 확인" in s:
        return 4
    if "복수 보도" in s:
        return 3
    if "보도" in s:
        return 2
    if "제안" in s or "전망" in s or "후보" in s:
        return 1
    return 0


def fact(key: str, value, label: str, status: str, source: str, mode: str = "replace") -> dict:
    return {
        "key": key,
        "value": value,
        "label": label,
        "status": status,
        "source": source,
        "mode": mode,
    }


def extract_facts(row: dict) -> list[dict]:
    title = str(row.get("title") or "")
    text = str(row.get("article_text") or "")
    source = str(row.get("resolved_link") or row.get("link") or "")
    blob = f"{title}\n{text}"
    low = blob.lower()
    out: list[dict] = []

    # 엔시날 가스발전
    if any(x in low for x in ["엔시날", "encinal", "가스복합", "가스발전", "lng발전"]):
        if re.search(r'\b6\.3\s*(?:gw|기가와트)', blob, re.I):
            out.append(fact("encinal.capacity_reports_gw", 6.3, "엔시날 발전용량 보도값", "보도", source, "set"))
        if re.search(r'\b6\.4\s*(?:gw|기가와트)', blob, re.I):
            out.append(fact("encinal.capacity_reports_gw", 6.4, "엔시날 발전용량 보도값", "보도", source, "set"))
        if re.search(r'\b1\.3\s*(?:gw|기가와트)', blob, re.I) and any(x in low for x in ["1단계", "단순주기", "가스터빈"]):
            out.append(fact("encinal.stage1_capacity_reports_gw", 1.3, "엔시날 1단계 용량 보도값", "보도", source, "set"))
        if re.search(r'\b1\.4\s*(?:gw|기가와트)', blob, re.I) and any(x in low for x in ["1단계", "가스터빈"]):
            out.append(fact("encinal.stage1_capacity_reports_gw", 1.4, "엔시날 1단계 용량 보도값", "보도", source, "set"))
        if re.search(r'198\s*억\s*달러', blob):
            out.append(fact("encinal.july_preliminary_cost_usd_eok", 198, "7월 엔시날 예비 사업비", "보도", source))
        if re.search(r'168\s*억\s*달러', blob):
            out.append(fact("encinal.negotiation_baseline_usd_eok", 168, "엔시날 협상 초기 기준 사업비", "보도", source))
        if re.search(r'200\s*억\s*달러', blob) and any(x in low for x in ["늘", "증액", "중간", "이어"]):
            out.append(fact("encinal.intermediate_cost_usd_eok", 200, "엔시날 중간 조정 사업비", "보도", source))
        if re.search(r'250\s*억\s*달러', blob) and any(x in low for x in ["미국", "미측", "요구", "제시"]):
            out.append(fact("encinal.us_requested_cost_usd_eok", 250, "미국 측 엔시날 요구 사업비", "미국 측 요구 보도", source))
            out.append(fact("encinal.cost_increase_driver_us_pressure", True, "엔시날 증액이 미국 측 요구에서 촉발", "복수 보도", source))
        if re.search(r'223\s*억\s*달러', blob):
            out.append(fact("encinal.current_reported_cost_usd_eok", 223, "엔시날 현재 협상 사업비", "복수 보도", source))
        if "루이스 에너지" in low or "lewis energy" in low:
            out.append(fact("encinal.project_owner", "Lewis Energy Group", "엔시날 사업주", "보도", source))
        if "ercot" in low:
            out.append(fact("encinal.ercot_sale_option", True, "ERCOT 전력판매 경로 검토", "보도", source))
        if "오프그리드" in low or "직접 공급" in low or "직접 연결" in low:
            out.append(fact("encinal.direct_ai_dc_supply_option", True, "AI 데이터센터 직접 전력공급 검토", "보도", source))
        if "ppa" in low or "장기 전력" in low:
            out.append(fact("encinal.ppa_needed", True, "장기 전력판매계약이 핵심 현금흐름 조건", "보도", source))
        if re.search(r'2030\s*년', blob) and any(x in low for x in ["상업", "가동"]):
            out.append(fact("encinal.stage1_commercial_start_year", 2030, "엔시날 1단계 상업가동 목표", "초기 보도", source))
        if re.search(r'5\s*gw', blob, re.I) and "데이터센터" in low:
            out.append(fact("encinal.nearby_data_center_reported_gw", 5.0, "인근 AI 데이터센터 추진 용량", "초기 보도", source))
        if "1호" in low and any(x in low for x in ["확정", "의결", "낙점", "선정"]):
            out.append(fact("encinal.first_project_selected", True, "대미투자 1호 엔시날 선정", "복수 보도", source))

    # 투자 구조 / I-SPV
    if "i-spv" in low or "리스크 풀링" in low or "risk pooling" in low or "우산형" in low:
        if "리스크 풀링" in low or "risk pooling" in low or "수익을 모" in low:
            out.append(fact("structure.risk_pooling_maintained", True, "여러 프로젝트 수익 통합 관리 구조 유지", "복수 보도", source))
        if any(x in low for x in ["운영계약", "운영안"]):
            out.append(fact("structure.i_spv_operating_terms_under_review", True, "I-SPV 운영계약안 심의·보고 단계", "보도", source))

    # 원전 패키지
    if "원전" in low:
        if re.search(r'8\s*기', blob):
            out.append(fact("nuclear.reactors", 8, "미국 내 원전 추진 기수", "보도·협의 단계", source))
        if re.search(r'1,?200\s*억\s*달러', blob):
            out.append(fact("nuclear.us_proposal_usd_eok", 1200, "미국 측 원전 8기 대미투자 제안액", "미국 측 제안", source))
        if re.search(r'1,?300\s*억\s*달러', blob):
            out.append(fact("nuclear.reported_project_size_usd_eok", 1300, "원전 8기 총사업 규모 보도", "보도", source))
        if re.search(r'ap1000.{0,40}6\s*기|6\s*기.{0,40}ap1000', blob, re.I | re.S):
            out.append(fact("nuclear.ap1000_reactors", 6, "AP1000 포함 기수", "보도", source))
        if re.search(r'apr1400.{0,40}2\s*기|2\s*기.{0,40}apr1400', blob, re.I | re.S) or re.search(r'한국형\s*원전.{0,30}2\s*기', blob, re.I | re.S):
            out.append(fact("nuclear.apr1400_reactors", 2, "APR1400·한국형 원전 포함 기수", "보도", source))
        if any(x in low for x in ["프레임워크", "합의문에 포함", "패키지 포함", "8기 건설 추진"]):
            out.append(fact("package.nuclear_framework_included", True, "원전 8기 추진 프레임워크 패키지 포함", "보도·협의 단계", source))
        if any(x in low for x in ["확약하지", "투자액은 조율", "제안 단계"]):
            out.append(fact("nuclear.us_120b_not_committed", True, "1,200억달러는 한국 확약액이 아님", "복수 보도", source))
        if all(x not in low for x in ["부지 확정", "사업자 확정", "착공 확정"] ) and any(x in low for x in ["구체적인 내용은 명시하지", "부지와 사업자", "포괄적인 협력 목표"]):
            out.append(fact("nuclear.project_details_not_fixed", True, "원전 8기 부지·사업자·노형·착공시점 미확정", "보도·협의 단계", source))

    # 패키지 서명 일정
    if re.search(r'(?:9월\s*)?18\s*일', blob) and any(x in low for x in ["서명", "사인", "발표"]):
        out.append(fact("package.target_sign_date", "2026-09-18", "대미투자 최종 서명 목표일", "보도", source))

    # 알래스카 LNG
    if "알래스카" in low and "lng" in low:
        if any(x in low for x in ["합의문", "포함될", "패키지"]):
            out.append(fact("alaska.final_framework_reported_included", True, "Alaska LNG 최종 패키지 포함 보도", "보도", source))
        if any(x in low for x in ["의결 대상에서 제외", "별도로 검토", "시간 두고"]):
            out.append(fact("alaska.first_project_round_excluded", True, "Alaska LNG 1호 의결 대상 제외·후속 검토", "보도", source))
        if re.search(r'20\s*(?:mtpa|million tonnes|백만t|백만톤)', blob, re.I):
            out.append(fact("alaska.export_capacity_mtpa", 20, "Alaska LNG 연간 수출능력", "공식 개발사", source))
        if re.search(r'16\s*(?:mtpa|million tonnes|백만t|백만톤)', blob, re.I):
            out.append(fact("alaska.financing_required_offtake_mtpa", 16, "Alaska LNG 금융종결 필요 장기계약량", "개발사·보도", source))
        if re.search(r'13\s*(?:mtpa|million tonnes|백만t|백만톤)', blob, re.I):
            out.append(fact("alaska.reported_commitments_mtpa", 13, "Alaska LNG 확보 계약·의향 물량", "보도", source))

    # 반도체 투자 요구
    if "반도체" in low and any(x in low for x in ["대미투자", "미국", "미측"]):
        if any(x in low for x in ["논의 대상", "미국 측의 제기", "미측이 제기", "투자 요구"]):
            out.append(fact("semiconductor.us_investment_request_acknowledged", True, "미국의 반도체 투자 요구가 공식 논의 대상", "청와대 확인", source))
        if any(x in low for x in ["명확하지", "정해지지", "별개인지"]):
            out.append(fact("semiconductor.relationship_to_200b_unclear", True, "반도체 투자의 2,000억달러 패키지 포함 여부 미정", "청와대 확인", source))
        if any(x in low for x in ["포함되지", "빠져"]):
            out.append(fact("semiconductor.first_project_excluded", True, "반도체 투자는 엔시날 1호 프로젝트에서 제외", "보도", source))

    # 관세·대미투자 연계 안전장치
    if "301조" in low and "대미투자" in low:
        out.append(fact("tariff.section301_investment_negotiation_overlap", True, "301조 조사와 대미투자 협상 시계가 겹침", "보도", source))
    if "15%" in blob and any(x in low for x in ["상한", "불리하지", "관세"]):
        out.append(fact("tariff.fifteen_percent_defense_focus", True, "한국 정부가 기존 15% 관세 합의선 방어 중", "정부 발언 보도", source))

    # 공식 설명은 보도 사실을 삭제하지 않고 확정도를 낮추는 별도 사실로 보존
    if "정해진 바" in low and "원전" in low and "대미투자" in low:
        out.append(fact("nuclear.official_detail_not_final", True, "정부: 구체적인 대미투자 원전 프로젝트는 아직 미확정", "공식 설명", source))

    return out


def value_equal(a, b) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) < 1e-9
    return a == b


def merge_fact(old: dict | None, candidate: dict, now: dt.datetime) -> tuple[dict, bool]:
    source = str(candidate.get("source") or "")
    mode = candidate.get("mode") or "replace"
    new_value = candidate.get("value")
    new_status = str(candidate.get("status") or "")

    if old is None:
        stored_value = [new_value] if mode == "set" else new_value
        return {
            "value": stored_value,
            "label": candidate.get("label"),
            "status": new_status,
            "sources": [source] if source else [],
            "updated_at": now.astimezone(base.KST).isoformat(timespec="seconds"),
        }, True

    updated = dict(old)
    sources = list(updated.get("sources") or [])
    if source and source not in sources:
        sources.append(source)
        updated["sources"] = sources[-12:]

    changed = False
    if mode == "set":
        existing = updated.get("value")
        values = list(existing) if isinstance(existing, list) else [existing]
        if not any(value_equal(v, new_value) for v in values):
            values.append(new_value)
            try:
                values = sorted(values)
            except Exception:
                pass
            updated["value"] = values
            changed = True
    else:
        if not value_equal(updated.get("value"), new_value):
            updated["value"] = new_value
            changed = True

    if status_rank(new_status) > status_rank(str(updated.get("status") or "")):
        updated["status"] = new_status
        changed = True

    if changed:
        updated["label"] = candidate.get("label") or updated.get("label")
        updated["updated_at"] = now.astimezone(base.KST).isoformat(timespec="seconds")
    return updated, changed


def fact_value_text(key: str, value, fx: float) -> str:
    if key.endswith("_usd_eok") and isinstance(value, (int, float)):
        return f"{float(value):,.0f}억달러({krw_for_eok(float(value), fx)})"
    if key.endswith("_gw") and isinstance(value, (int, float)):
        return f"{float(value):g}GW"
    if key.endswith("_mtpa") and isinstance(value, (int, float)):
        return f"{float(value):g}MTPA"
    if key.endswith("reactors") and isinstance(value, (int, float)):
        return f"{int(value)}기"
    if isinstance(value, bool):
        return "확인" if value else "해제"
    if isinstance(value, list):
        suffix = "GW" if key.endswith("_gw") else ""
        return " / ".join(f"{v:g}{suffix}" if isinstance(v, (int, float)) else str(v) for v in value)
    return str(value)


def build_alert(now: dt.datetime, changes: list[tuple[str, dict, dict | None]], fx: float, fx_source: str) -> str:
    parts = ["<b>🇺🇸 대미투자 | 내용 변화</b>", "", "<b>📌 새로 반영된 사실</b>"]
    for key, new, old in changes[:12]:
        label = html.escape(str(new.get("label") or key))
        new_text = html.escape(fact_value_text(key, new.get("value"), fx))
        status = html.escape(str(new.get("status") or ""))
        if old is not None and not value_equal(old.get("value"), new.get("value")):
            old_text = html.escape(fact_value_text(key, old.get("value"), fx))
            parts.append(f"• <b>{label}</b>  {old_text} → <b>{new_text}</b> · {status}")
        else:
            parts.append(f"• <b>{label}</b>  {new_text} · {status}")

    keys = {k for k, _, _ in changes}
    parts.extend(["", "<b>🧭 의미</b>"])
    if any(k.startswith("nuclear.") for k in keys):
        parts.append("• 원전은 <b>기수·제안액·노형을 분리</b>합니다. 1,200억달러 제안액과 1,300억달러 총사업 규모를 같은 숫자로 합치지 않습니다.")
    elif any(k.startswith("encinal.") for k in keys):
        parts.append("• 엔시날은 <b>총사업비와 한국 실제 출자액을 분리</b>하고, PPA·EPC·가스터빈 본계약이 확인될 때 매출 단계로 올립니다.")
    elif any(k.startswith("semiconductor.") for k in keys):
        parts.append("• 반도체는 미국의 투자 요구 존재와 <b>2,000억달러 패키지 포함 여부·실제 팹 투자 확정</b>을 분리합니다.")
    elif any(k.startswith("alaska.") for k in keys):
        parts.append("• Alaska LNG는 패키지 언급과 실제 FID·금융종결·장기 구매계약을 분리합니다.")
    else:
        parts.append("• 기사 수가 아니라 <b>기존 누적 내용의 숫자·조건·일정·확정도 변화</b>만 알립니다.")

    source_urls: list[str] = []
    for _, new, _ in changes:
        for src in new.get("sources") or []:
            if src and src not in source_urls:
                source_urls.append(src)
    if source_urls:
        parts.extend(["", "<b>🔗 근거</b>"])
        for i, src in enumerate(source_urls[:5], 1):
            parts.append(f'• <a href="{html.escape(src, quote=True)}">확인 {i}</a>')

    parts.extend([
        "",
        f'<a href="{base.MOU_OFFICIAL_URL}"><b>산업통상부 한미 전략투자 MOU</b></a>',
        base.footer(now, fx, fx_source, "기사 링크 고정감시 없음 · 내용 누적장부 변화 기준 · 달러는 원화 병기"),
    ])
    return "\n".join(parts)


def main() -> int:
    if base.ALERT.exists():
        base.ALERT.unlink()

    now = dt.datetime.now(base.UTC)
    fx, fx_source = base.get_usdkrw()
    runtime_state = base.load_state()
    unresolved = runtime_state.setdefault("unresolved_sources", {})
    facts_state = load_fact_state()
    facts = facts_state.setdefault("facts", {})

    # 링크 고정감시/PIN 없음. 매 실행마다 주제 검색 결과의 원문을 읽고 내용만 장부와 비교한다.
    candidates = base.rss_items(now)[:30]
    ready = v5.strict_rows(candidates, unresolved, now)

    changes: list[tuple[str, dict, dict | None]] = []
    for row in ready:
        for candidate in extract_facts(row):
            key = str(candidate["key"])
            old = facts.get(key)
            merged, changed = merge_fact(old, candidate, now)
            facts[key] = merged
            if changed:
                changes.append((key, merged, old))

    # 같은 실행에서 동일 사실이 여러 기사에 의해 갱신되면 마지막 상태만 1회 알림.
    dedup: dict[str, tuple[str, dict, dict | None]] = {}
    for item in changes:
        dedup[item[0]] = item
    changes = list(dedup.values())

    runtime_state["last_checked_at"] = now.astimezone(base.KST).isoformat(timespec="seconds")
    runtime_state["last_usdkrw"] = fx
    runtime_state["last_fx_source"] = fx_source
    runtime_state["monitor_mode"] = "cumulative_fact_changes_only"
    # 과거 기사 seen 목록은 더 이상 알림 판단에 사용하지 않는다.
    runtime_state.pop("recent_titles", None)
    runtime_state.pop("seen", None)

    facts_state["version"] = 2
    facts_state["last_updated"] = now.astimezone(base.KST).isoformat(timespec="seconds")

    if changes:
        base.ALERT.write_text(build_alert(now, changes, fx, fx_source) + "\n", encoding="utf-8")
        runtime_state["last_alert_at"] = now.astimezone(base.KST).isoformat(timespec="seconds")
        print(f"new_fact_updates={len(changes)}")
    else:
        print("new_fact_updates=0")

    base.STATE.write_text(json.dumps(runtime_state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    FACT_STATE.write_text(json.dumps(facts_state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
