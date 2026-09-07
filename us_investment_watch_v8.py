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
import us_investment_watch_v6 as v6
import us_investment_watch_v7 as v7  # imports Seoul Economic Daily pin + query expansion

FACT_STATE = Path(__file__).resolve().parent / "us_investment_fact_state.json"

# v8 principle: an article is only a source. The alert unit is a NEW/CHANGED FACT.
# Therefore similar headlines are not suppressed before full-text extraction.


def load_fact_state() -> dict:
    if not FACT_STATE.exists():
        return {"version": 1, "facts": {}}
    try:
        obj = json.loads(FACT_STATE.read_text(encoding="utf-8"))
        if not isinstance(obj, dict):
            raise ValueError
        obj.setdefault("facts", {})
        return obj
    except Exception:
        return {"version": 1, "facts": {}}


def add_fact(out: dict, key: str, value, label: str, status: str, source: str) -> None:
    out[key] = {
        "value": value,
        "label": label,
        "status": status,
        "source": source,
    }


def extract_facts(row: dict) -> dict:
    text = str(row.get("article_text") or "")
    title = str(row.get("title") or "")
    source = str(row.get("resolved_link") or row.get("link") or "")
    blob = f"{title}\n{text}"
    low = blob.lower()
    out: dict = {}

    # Encinal / gas-power project
    if any(x in low for x in ["엔시날", "encinal", "가스복합", "가스발전"]):
        if re.search(r'6\.3\s*(?:기가와트|gw)', blob, re.I):
            add_fact(out, "encinal.capacity_gw", 6.3, "엔시날 총 발전용량", "보도", source)
        if re.search(r'1\.4\s*(?:기가와트|gw)', blob, re.I) and any(x in low for x in ["가스터빈", "1단계"]):
            add_fact(out, "encinal.stage1_gt_gw", 1.4, "엔시날 1단계 가스터빈", "보도", source)
        if re.search(r'4\.9\s*(?:기가와트|gw)', blob, re.I) and "복합" in low:
            add_fact(out, "encinal.stage2_ccgt_gw", 4.9, "엔시날 후속 복합화력", "보도", source)

        # Keep history keys separate: initial -> US request -> negotiated.
        if re.search(r'198\s*억\s*달러', blob) and any(x in low for x in ["당초", "기존", "검토"]):
            add_fact(out, "encinal.initial_cost_usd_eok", 198, "엔시날 당초 검토 사업비", "보도", source)
        if re.search(r'250\s*억\s*달러', blob) and any(x in low for x in ["미국", "요구", "제시", "통보"]):
            add_fact(out, "encinal.us_requested_cost_usd_eok", 250, "미국 측 엔시날 증액 요구", "미국 측 요구 보도", source)
            add_fact(out, "encinal.cost_increase_driver_us_pressure", True, "엔시날 증액이 미국 측 요구에서 촉발", "보도", source)
        if re.search(r'223\s*억\s*달러', blob) and any(x in low for x in ["최종", "절충", "타협", "조정", "의결", "확정"]):
            add_fact(out, "encinal.negotiated_cost_usd_eok", 223, "엔시날 협상 사업비", "보도", source)

    # Nuclear package
    if "원전" in low:
        if re.search(r'8\s*기', blob):
            add_fact(out, "nuclear.reactors", 8, "미국 내 원전 추진 기수", "보도·협의 단계", source)
        if re.search(r'한국형\s*원전.{0,20}2\s*기|2\s*기.{0,20}한국형\s*원전', blob, re.I | re.S):
            add_fact(out, "nuclear.korean_reactors", 2, "한국형 원전 포함 기수", "보도", source)
        if re.search(r'1,?200\s*억\s*달러', blob):
            add_fact(out, "nuclear.us_proposal_usd_eok", 1200, "미국 측 원전 8기 대미투자 제안액", "미국 측 제안", source)
        if re.search(r'1,?300\s*억\s*달러', blob):
            add_fact(out, "nuclear.reported_project_size_usd_eok", 1300, "원전 8기 총사업 규모 보도", "보도", source)
        if any(x in low for x in ["패키지 포함", "합의문에 포함", "프레임워크", "8기 건설 추진"]):
            add_fact(out, "package.nuclear_framework_included", True, "원전 8기 추진 프레임워크 패키지 포함", "보도·협의 단계", source)

    # Schedule
    if re.search(r'(?:9월\s*)?18\s*일', blob) and any(x in low for x in ["서명", "사인", "발표"]):
        add_fact(out, "package.target_sign_date", "2026-09-18", "대미투자 최종 서명 목표일", "보도", source)

    # Official clarification can downgrade confidence without deleting reported facts.
    if "정해진 바" in low and "대미투자" in low and "원전" in low:
        add_fact(out, "nuclear.official_detail_not_final", True, "정부: 구체적인 대미투자 원전 프로젝트는 아직 미확정", "공식 설명", source)

    return out


def equivalent(a, b) -> bool:
    if isinstance(a, float) or isinstance(b, float):
        try:
            return abs(float(a) - float(b)) < 1e-9
        except Exception:
            return a == b
    return a == b


def krw_for_eok(value: float, fx: float) -> str:
    return v6.krw_text_from_won(value * 100_000_000 * fx)


def fact_line(key: str, item: dict, old_item: dict | None, fx: float) -> str:
    label = str(item.get("label") or key)
    value = item.get("value")
    status = str(item.get("status") or "")

    if key.endswith("_usd_eok") and isinstance(value, (int, float)):
        value_text = f"{value:,.0f}억달러({krw_for_eok(float(value), fx)})"
    elif key.endswith("_gw") and isinstance(value, (int, float)):
        value_text = f"{value:g}GW"
    elif key.endswith("reactors") and isinstance(value, (int, float)):
        value_text = f"{int(value)}기"
    elif isinstance(value, bool):
        value_text = "확인" if value else "해제"
    else:
        value_text = str(value)

    if old_item is not None and not equivalent(old_item.get("value"), value):
        old_value = old_item.get("value")
        if key.endswith("_usd_eok") and isinstance(old_value, (int, float)):
            old_text = f"{old_value:,.0f}억달러({krw_for_eok(float(old_value), fx)})"
        else:
            old_text = str(old_value)
        return f"• <b>{html.escape(label)}</b>  {html.escape(old_text)} → <b>{html.escape(value_text)}</b> · {html.escape(status)}"
    return f"• <b>{html.escape(label)}</b>  {html.escape(value_text)} · {html.escape(status)}"


def build_fact_alert(now, changed: list[tuple[str, dict, dict | None]], fx: float, fx_source: str) -> str:
    parts = ["<b>🇺🇸 대미투자 | 내용 업데이트</b>", ""]
    parts.append("<b>📌 새로 바뀐 내용</b>")
    for key, item, old_item in changed[:10]:
        parts.append(fact_line(key, item, old_item, fx))

    # Show one compact interpretation based on changed keys, not article titles.
    keys = {k for k, _, _ in changed}
    parts.extend(["", "<b>🧭 의미</b>"])
    if any(k.startswith("encinal.") for k in keys) and any(k.startswith("nuclear.") or k.startswith("package.nuclear") for k in keys):
        parts.append("• 엔시날 가스발전과 원전은 <b>별도 사업</b>이지만 같은 대미투자 패키지 안에서 재원 경쟁을 하므로, 한국 실제 출자액과 개별 프로젝트 지분을 분리해서 봅니다.")
    elif any(k.startswith("encinal.") for k in keys):
        parts.append("• 엔시날은 총사업비 변화보다 <b>한국 실제 출자액·PPA·EPC/가스터빈 본계약</b>이 실제 현금흐름과 국내 기업 매출을 결정합니다.")
    elif any(k.startswith("nuclear.") or k.startswith("package.nuclear") for k in keys):
        parts.append("• 원전은 8기·금액보다 <b>노형·개별 사업비·한국 지분·EPC/기자재 본계약</b>이 잠겨야 실제 한국 기업 매출로 연결됩니다.")
    else:
        parts.append("• 같은 제목의 기사 반복은 무시하고, 기존 누적 내용에서 실제 숫자·범위·일정이 바뀔 때만 알립니다.")

    # Sources only for changed facts; title-level duplication is irrelevant.
    srcs: list[str] = []
    for _, item, _ in changed:
        src = str(item.get("source") or "")
        if src and src not in srcs:
            srcs.append(src)
    if srcs:
        parts.extend(["", "<b>🔗 확인 근거</b>"])
        for idx, src in enumerate(srcs[:5], 1):
            parts.append(f'• <a href="{html.escape(src, quote=True)}">근거 {idx}</a>')

    parts.extend([
        "",
        f'<a href="{base.MOU_OFFICIAL_URL}"><b>산업통상부 한미 전략투자 MOU</b></a>',
        base.footer(now, fx, fx_source, "기사 자체가 아니라 누적 내용 변화 기준 · 달러는 원화 병기 · 원문 본문 확보 우선"),
    ])
    return "\n".join(parts)


def main() -> int:
    if base.ALERT.exists():
        base.ALERT.unlink()

    state = base.load_state()
    facts_state = load_fact_state()
    facts = facts_state.setdefault("facts", {})
    now = dt.datetime.now(base.UTC)
    fx, fx_source = base.get_usdkrw()
    seen = state.setdefault("seen", {})
    unresolved = state.setdefault("unresolved_sources", {})

    # Do NOT pre-filter by title similarity. Every unseen article can contain a new fact.
    candidates: list[dict] = []
    for pinned in [v7.PINNED_SEDAILY, v6.PINNED_EDAILY_120B, v3.PINNED]:
        if pinned.get("id") not in seen:
            candidates.append(pinned)
    for row in v3.collect(now):
        if row.get("id") in seen:
            continue
        candidates.append(row)
        if len(candidates) >= 30:
            break

    ready = v5.strict_rows(candidates, unresolved, now)
    changed: list[tuple[str, dict, dict | None]] = []

    for row in ready:
        row_id = str(row.get("id") or "")
        extracted = extract_facts(row)
        # Mark article as processed whether or not it adds a new fact. Article itself is not the alert unit.
        if row_id:
            seen[row_id] = now.isoformat()

        for key, item in extracted.items():
            old = facts.get(key)
            source = str(item.get("source") or "")
            if old is None or not equivalent(old.get("value"), item.get("value")) or old.get("status") != item.get("status"):
                changed.append((key, item, old))
                facts[key] = {
                    "value": item.get("value"),
                    "label": item.get("label"),
                    "status": item.get("status"),
                    "sources": list(dict.fromkeys(([source] if source else []) + list((old or {}).get("sources") or []))),
                    "updated_at": now.astimezone(base.KST).isoformat(timespec="seconds"),
                }
            elif source:
                sources = list(old.get("sources") or [])
                if source not in sources:
                    sources.append(source)
                    old["sources"] = sources[-10:]

    # Deduplicate changed keys within a run; last row wins for same fact key.
    latest: dict[str, tuple[str, dict, dict | None]] = {}
    for item in changed:
        latest[item[0]] = item
    changed = list(latest.values())

    state["last_checked_at"] = now.astimezone(base.KST).isoformat(timespec="seconds")
    state["last_usdkrw"] = fx
    state["last_fx_source"] = fx_source
    facts_state["last_updated"] = now.astimezone(base.KST).isoformat(timespec="seconds")

    if changed:
        base.ALERT.write_text(build_fact_alert(now, changed, fx, fx_source) + "\n", encoding="utf-8")
        state["last_alert_at"] = now.astimezone(base.KST).isoformat(timespec="seconds")
        print(f"new_fact_updates={len(changed)}")
    else:
        print("new_fact_updates=0")

    base.STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    FACT_STATE.write_text(json.dumps(facts_state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
