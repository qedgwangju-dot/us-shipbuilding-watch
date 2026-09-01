#!/usr/bin/env python3
import datetime as dt
import html
import os
import re
from collections import defaultdict

import refinery_watch as rw
import spr_venezuela_watch as sv

KST = rw.KST

OFFICIAL_HINTS = (
    "environmental protection agency", "epa", "white house", "department of energy",
    "energy.gov", "eia", ".gov",
)
RELIABLE_HINTS = ("reuters", "bloomberg")
MAJOR_HINTS = ("yahoo finance", "cnbc", "associated press", "ap news", "sbs", "wsj", "financial times")
LOW_VALUE_OPINION = ("jezebel", "common dreams")


def now_kst():
    return dt.datetime.now(KST)


def parse_pub(item):
    try:
        return dt.datetime.fromisoformat(item.get("pub_kst") or "")
    except Exception:
        return now_kst()


def source_priority(item):
    text = f"{item.get('source','')} {item.get('source_url','')}".lower()
    if any(x in text for x in OFFICIAL_HINTS):
        return 5
    if any(x in text for x in RELIABLE_HINTS):
        return 4
    if any(x in text for x in MAJOR_HINTS):
        return 3
    if any(x in text for x in LOW_VALUE_OPINION):
        return 1
    return 2


def refinery_event_key(item):
    t = f"{item.get('title','')} {item.get('source','')}".lower()

    if any(x in t for x in ("small refinery exemption", "small-refinery exemption", "sre")):
        if any(x in t for x in ("price", "prices", "jump", "rise", "rose", "rally", "market", "trading")) and "rin" in t:
            return "rfs_market_reaction_2025_20260901"
        if any(x in t for x in ("realloc", "renewable volume obligation", "rvo")):
            return "rfs_reallocation_2026_2027"
        return "rfs_sre_2025_decision"

    if "rin" in t and any(x in t for x in ("realloc", "rvo", "waiver", "exemption")):
        return "rfs_sre_2025_decision"

    if any(x in t for x in ("host oil executives", "refinery executives", "refiners meeting", "meeting with refiners")):
        return "refinery_white_house_meeting_20260901"

    if any(x in t for x in ("gouging", "pump prices", "gasoline prices")) and "trump" in t:
        return "refinery_gasoline_policy_202609"

    if "refinery" in t and any(x in t for x in ("capacity", "permitting", "regulation", "expand")):
        return "refinery_capacity_policy_202609"

    # Unknown articles remain distinct; only known high-overlap policy events are clustered.
    return f"refinery_article_{item.get('id','unknown')}"


def spr_event_key(item):
    t = f"{item.get('title','')} {item.get('source','')}".lower()

    if any(x in t for x in ("historic oil agreement", "65 billion", "north american blue energy", "nabep")):
        return "spr_venezuela_oil_agreement_20260831"

    if any(x in t for x in ("heavy crude", "extra-heavy", "sulfur", "sulphur", "cavern", "storage cost", "too heavy")):
        return "spr_venezuela_quality_bottleneck"

    if any(x in t for x in ("strategic petroleum reserve", "spr")) and any(
        x in t for x in ("refill", "replenish", "fill up", "top out", "topping", "gift")
    ):
        return "spr_refill_statement_20260830"

    return f"spr_article_{item.get('id','unknown')}"


def event_label(topic, key):
    labels = {
        "rfs_sre_2025_decision": "RFS 정책 확정",
        "rfs_reallocation_2026_2027": "RFS 재할당 후속",
        "rfs_market_reaction_2025_20260901": "RIN 시장 반응",
        "refinery_white_house_meeting_20260901": "백악관 정유업계 회동",
        "refinery_gasoline_policy_202609": "휘발유 가격 정책",
        "refinery_capacity_policy_202609": "정제능력 정책",
        "spr_venezuela_oil_agreement_20260831": "백악관 공식 석유 합의",
        "spr_venezuela_quality_bottleneck": "SPR 실행 병목",
        "spr_refill_statement_20260830": "SPR 보충 방침",
    }
    return labels.get(key, "정책·시장 변화" if topic == "refinery" else "SPR·베네수엘라 변화")


def impact_lines(topic, key):
    if key == "rfs_sre_2025_decision":
        return [
            "소규모 정유사: 2025년 RFS 준수 부담이 직접 완화됩니다.",
            "대형 정유사·바이오연료 업계: 면제분을 2026·2027 RVO에 어떻게 재할당하느냐가 다음 손익 변수입니다.",
            "RIN 시장: 면제 규모와 재할당 방식이 동시에 바뀌면서 가격 변동성이 커질 수 있습니다.",
        ]
    if key == "rfs_reallocation_2026_2027":
        return [
            "현재 면제의 즉시 수혜와 향후 재할당 부담을 분리해서 봐야 합니다.",
            "최종 RVO가 확정될 때 정유사별 실질 규제비용과 바이오연료 수요가 다시 바뀔 수 있습니다.",
        ]
    if key == "rfs_market_reaction_2025_20260901":
        return [
            "정책 문구보다 RIN 가격이 실제 규제비용 기대를 얼마나 바꿨는지 확인하는 구간입니다.",
        ]
    if key == "refinery_white_house_meeting_20260901":
        return [
            "회동 자체보다 허가 완화·RFS·증설·정비 정책이 실제 행정조치로 이어지는지가 핵심입니다.",
            "가동률이 높은 상황에서는 단순 증산 요구보다 신규 정제능력과 규제비용 변화가 더 중요합니다.",
        ]
    if key == "spr_venezuela_oil_agreement_20260831":
        return [
            "단순 SPR 보충 발언보다 한 단계 진전된 정부 간 공급권·지배구조 합의입니다.",
            "실제 SPR 효과는 NABEP 생산 증가 → 미국 인수 → DOE 반입 → EIA 재고 순증으로 확인해야 합니다.",
        ]
    if key == "spr_venezuela_quality_bottleneck":
        return [
            "베네수엘라산 원유의 품질·저장 적합성이 SPR 보충 속도를 제한할 수 있는 핵심 실패모드입니다.",
            "정치적 합의와 실제 저장 가능한 배럴 수를 분리해서 봐야 합니다.",
        ]
    if key == "spr_refill_statement_20260830":
        return [
            "정책 방향은 확인됐지만 실제 조달 계약과 SPR 입고 물량이 아직 더 중요한 다음 단계입니다.",
        ]
    return []


def next_checks(topic, key):
    if key in ("rfs_sre_2025_decision", "rfs_reallocation_2026_2027", "rfs_market_reaction_2025_20260901"):
        return [
            "EPA의 2026·2027 RVO 재할당 최종안과 10월 말 이전 발표 여부",
            "RIN 가격과 정유사별 실제 규제비용 변화",
            "9월 1일 백악관 정유업계 회동에서 RFS가 추가 조정되는지",
        ]
    if key == "refinery_white_house_meeting_20260901":
        return [
            "실제 참석사와 백악관 공식 회의 결과",
            "정제능력 증설·허가·정비 관련 행정조치",
            "RFS·SRE·RIN 추가 조정 여부",
        ]
    if key == "spr_venezuela_oil_agreement_20260831":
        return [
            "NABEP 17개 유전의 실제 생산계획·설비투자·첫 증산 시점",
            "국무부의 20% 원가 인수권이 실제 구매계약으로 전환되는지",
            "DOE의 SPR 조달·반입 공고와 EIA SPR 재고 순증",
        ]
    if key == "spr_venezuela_quality_bottleneck":
        return [
            "DOE가 허용하는 원유 품질·저장 규격과 실제 베네수엘라산 원유 사양",
            "혼합·개질·운송 추가비용과 저장기지 지정 여부",
            "SPR 순증분이 베네수엘라 신규 반입인지 기존 교환 반환인지",
        ]
    if key == "spr_refill_statement_20260830":
        return [
            "DOE 조달 공고의 배럴 수·가격·반입일·저장기지",
            "기존 SPR 교환계약 반환물량과 중복 여부",
            "EIA SPR 주간 재고가 실제 순증으로 전환되는 시점",
        ]
    return []


def split_sentences(text):
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return []
    return [x.strip() for x in re.split(r"(?<=[.!?。])\s+", text) if x.strip()]


def short(text, limit=210):
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def build_snapshot(enriched):
    candidates = []
    for idx, paragraph in enumerate(enriched.get("body_ko") or []):
        for sentence in split_sentences(paragraph):
            score = 0
            if re.search(r"\d", sentence):
                score += 3
            if any(x in sentence for x in ("발표", "결정", "합의", "면제", "재할당", "비축", "생산", "권리", "지분", "가격")):
                score += 2
            if idx == 0:
                score += 1
            candidates.append((score, sentence))

    selected = []
    for _, sentence in sorted(candidates, key=lambda x: x[0], reverse=True):
        normalized = re.sub(r"\W+", "", sentence)
        if not normalized:
            continue
        if any(normalized in re.sub(r"\W+", "", x) or re.sub(r"\W+", "", x) in normalized for x in selected):
            continue
        selected.append(short(sentence, 190))
        if len(selected) >= 4:
            break

    if not selected:
        for p in enriched.get("body_ko") or []:
            selected.append(short(p, 190))
            if len(selected) >= 3:
                break
    return selected


def secondary_titles(items, primary_id, max_items=2):
    out = []
    for item in items:
        if item.get("id") == primary_id:
            continue
        try:
            title_ko = rw.translate_ko(item.get("title") or "")
        except Exception:
            title_ko = ""
        source = item.get("source") or "추가 출처"
        if title_ko:
            out.append(f"{source}: {short(title_ko, 120)}")
        else:
            out.append(source)
        if len(out) >= max_items:
            break
    return out


def readable_news_alert(topic, enriched, key, grouped_items):
    title = html.escape(enriched.get("title_ko") or "", quote=False)
    source = html.escape(enriched.get("source") or "확인 필요", quote=False)
    pub = html.escape((enriched.get("pub_kst") or "").replace("T", " "), quote=False)
    link = html.escape(enriched.get("original_url") or "", quote=True)
    label = html.escape(event_label(topic, key), quote=False)
    header = "트럼프 정유업계 고유가 대응" if topic == "refinery" else "SPR·베네수엘라 원유"

    lines = [
        f"<b>[{header}]</b>",
        f"<b>{label} | {title}</b>",
        f"출처: {source} | 발표: {pub}",
    ]

    if len(grouped_items) > 1:
        lines.append(f"동일 사건 관련 보도 <b>{len(grouped_items)}건 묶음</b>")

    snapshot = build_snapshot(enriched)
    if snapshot:
        lines.extend(["", "<b>한눈에 보기</b>"])
        lines.extend(f"• {html.escape(x, quote=False)}" for x in snapshot)

    lines.extend(["", "<b>상세</b>"])
    for paragraph in enriched.get("body_ko") or []:
        lines.append(f"• {html.escape(short(paragraph, 420), quote=False)}")

    impacts = impact_lines(topic, key)
    if impacts:
        lines.extend(["", "<b>투자 영향</b>"])
        lines.extend(f"• {html.escape(x, quote=False)}" for x in impacts)

    secs = secondary_titles(grouped_items, enriched.get("id"))
    if secs:
        lines.extend(["", "<b>추가 보도·반응</b>"])
        lines.extend(f"• {html.escape(x, quote=False)}" for x in secs)

    checks = next_checks(topic, key)
    if checks:
        lines.extend(["", "<b>다음 확인</b>"])
        lines.extend(f"• {html.escape(x, quote=False)}" for x in checks)

    lines.extend(["", f'<a href="{link}">원문</a>'])
    return "\n".join(lines).strip()


def readable_spr_data_alert(current):
    value = current["value_kbbl"]
    prev = current.get("prev_kbbl")
    delta = value - prev if prev is not None else None
    ratio = value / sv.SPR_CAPACITY_KBBL * 100
    shortage = sv.SPR_CAPACITY_KBBL - value
    if delta is None:
        change = "전주 비교 불가"
    else:
        sign = "+" if delta > 0 else ""
        change = f"{sign}{delta / 1000:,.3f}백만 배럴"
    verdict = "순증 → 실제 보충·반환 유입 신호" if delta and delta > 0 else "아직 보충 효과 미확인" if delta is not None else "확인 필요"
    source = html.escape(current["source"], quote=True)
    return (
        "<b>[SPR 공식 수치]</b>\n"
        f"<b>{current['date']} | {value / 1000:,.3f}백만 배럴</b>\n\n"
        "<b>한눈에 보기</b>\n"
        f"• 전주 변화: <b>{change}</b>\n"
        f"• 저장능력 7억1,400만 배럴 대비: <b>{ratio:.1f}%</b>\n"
        f"• 완전 충전까지: <b>{shortage / 1000:,.3f}백만 배럴</b> 부족\n\n"
        f"<b>판정</b>\n• {verdict}\n"
        "• 증가분이 나와도 베네수엘라 신규 반입과 기존 교환계약 반환물량을 DOE 자료로 분리 확인\n\n"
        f'<a href="{source}">EIA 원문</a>'
    )


def readable_import_data_alert(current):
    value = current["value_kbd"]
    prev = current.get("prev_kbd")
    delta = value - prev if prev is not None else None
    sign = "+" if delta is not None and delta > 0 else ""
    change = "비교 불가" if delta is None else f"{sign}{delta:,}천 배럴/일"
    weekly = value * 7 / 1000
    source = html.escape(current["source"], quote=True)
    return (
        "<b>[베네수엘라산 원유 미국 유입]</b>\n"
        f"<b>{current['date']} | {value:,}천 배럴/일</b>\n\n"
        "<b>한눈에 보기</b>\n"
        f"• 전주 변화: <b>{change}</b>\n"
        f"• 7일 단순 환산: <b>{weekly:,.3f}백만 배럴</b>\n\n"
        "<b>판정</b>\n"
        "• 미국 전체 수입량이지 SPR 직접 입고량은 아님\n"
        "• 정유사 투입분과 SPR 저장분을 DOE 조달·반입 자료로 분리 확인\n\n"
        f'<a href="{source}">EIA 원문</a>'
    )


def should_skip_opinion(topic, item, key):
    source = (item.get("source") or "").lower()
    if not any(x in source for x in LOW_VALUE_OPINION):
        return False
    # Opinion pieces are only useful when they add a distinct factual bottleneck.
    if topic == "spr" and key == "spr_venezuela_quality_bottleneck":
        return False
    return True


def choose_primary(group):
    return sorted(group, key=lambda x: (source_priority(x), parse_pub(x)), reverse=True)


def process_refinery(token, chat_id):
    state = rw.load_state()
    state.setdefault("events", {})
    items, errors = rw.collect_items()
    seen = set(state.get("seen") or [])

    if not state.get("initialized"):
        state["initialized"] = True
        state["seen"] = [x["id"] for x in items[:300]]
        state["last_run_kst"] = now_kst().isoformat(timespec="seconds")
        rw.save_state(state)
        print(f"refinery_readable_baseline=true news={len(state['seen'])}")
        return [], errors

    cutoff = now_kst() - dt.timedelta(days=3)
    fresh = []
    for item in items:
        if item["id"] in seen:
            continue
        if parse_pub(item) < cutoff:
            continue
        fresh.append(item)

    groups = defaultdict(list)
    for item in fresh:
        groups[refinery_event_key(item)].append(item)

    messages = []
    newly_seen = []
    for key, group in groups.items():
        newly_seen.extend(x["id"] for x in group)
        candidates = choose_primary(group)
        old = state["events"].get(key) or {}
        best_priority = source_priority(candidates[0]) if candidates else 0
        if old and best_priority <= int(old.get("priority", 0)):
            continue

        enriched = None
        chosen = None
        for item in candidates:
            if should_skip_opinion("refinery", item, key):
                continue
            try:
                enriched = rw.enrich_korean(item)
                chosen = item
                break
            except Exception as exc:
                print(f"refinery_readable_enrich_skip={item['id']} error={exc}")
        if not enriched:
            continue

        messages.append(readable_news_alert("refinery", enriched, key, group))
        state["events"][key] = {
            "priority": source_priority(chosen),
            "last_pub": chosen.get("pub_kst"),
            "title": chosen.get("title"),
        }

    state["seen"] = list(dict.fromkeys(newly_seen + list(seen)))[:800]
    state["last_run_kst"] = now_kst().isoformat(timespec="seconds")
    rw.save_state(state)

    sent = []
    for message in messages:
        results = rw.send_telegram(token, chat_id, message)
        sent.extend((x.get("result") or {}).get("message_id") for x in results)
    print(f"refinery_readable_ok=true messages={len(messages)} ids={sent}")
    return sent, errors


def process_spr(token, chat_id):
    state = sv.load_state()
    state.setdefault("events", {})
    data_errors = []

    spr = None
    ven = None
    try:
        spr = sv.fetch_spr_latest()
    except Exception as exc:
        data_errors.append(f"SPR: {exc}")
    try:
        ven = sv.fetch_venezuela_imports_latest()
    except Exception as exc:
        data_errors.append(f"Venezuela imports: {exc}")

    news, source_errors = sv.collect_news()

    if not state.get("initialized"):
        state["initialized"] = True
        state["seen_news"] = [x["id"] for x in news[:300]]
        if spr:
            state["spr"] = spr
        if ven:
            state["venezuela_imports"] = ven
        sv.save_state(state)
        print(f"spr_readable_baseline=true news={len(state['seen_news'])}")
        return [], data_errors + source_errors

    messages = []
    if spr:
        old = state.get("spr") or {}
        if old.get("date") and spr["date"] != old.get("date"):
            messages.append(readable_spr_data_alert(spr))
        state["spr"] = spr

    if ven:
        old = state.get("venezuela_imports") or {}
        if old.get("date") and ven["date"] != old.get("date"):
            messages.append(readable_import_data_alert(ven))
        state["venezuela_imports"] = ven

    seen = set(state.get("seen_news") or [])
    cutoff = now_kst() - dt.timedelta(days=3)
    fresh = []
    for item in news:
        if item["id"] in seen:
            continue
        if parse_pub(item) < cutoff:
            continue
        fresh.append(item)

    groups = defaultdict(list)
    for item in fresh:
        groups[spr_event_key(item)].append(item)

    newly_seen = []
    for key, group in groups.items():
        newly_seen.extend(x["id"] for x in group)
        candidates = choose_primary(group)
        old = state["events"].get(key) or {}
        best_priority = source_priority(candidates[0]) if candidates else 0
        if old and best_priority <= int(old.get("priority", 0)):
            continue

        enriched = None
        chosen = None
        for item in candidates:
            if should_skip_opinion("spr", item, key):
                continue
            try:
                enriched = rw.enrich_korean(item)
                chosen = item
                break
            except Exception as exc:
                print(f"spr_readable_enrich_skip={item['id']} error={exc}")
        if not enriched:
            continue

        messages.append(readable_news_alert("spr", enriched, key, group))
        state["events"][key] = {
            "priority": source_priority(chosen),
            "last_pub": chosen.get("pub_kst"),
            "title": chosen.get("title"),
        }

    state["seen_news"] = list(dict.fromkeys(newly_seen + list(seen)))[:800]
    sv.save_state(state)

    sent = []
    for message in messages:
        results = rw.send_telegram(token, chat_id, message)
        sent.extend((x.get("result") or {}).get("message_id") for x in results)
    print(f"spr_readable_ok=true messages={len(messages)} ids={sent}")
    return sent, data_errors + source_errors


def main():
    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()
    if not token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID secret is missing")

    bot_username = rw.validate_telegram(token)
    ref_sent, ref_errors = process_refinery(token, chat_id)
    spr_sent, spr_errors = process_spr(token, chat_id)
    print(
        f"readable_energy_alerts_ok=true bot=@{bot_username} "
        f"refinery_sent={len(ref_sent)} spr_sent={len(spr_sent)} "
        f"errors={len(ref_errors) + len(spr_errors)}"
    )
    if ref_errors:
        print("refinery_errors=" + " | ".join(ref_errors))
    if spr_errors:
        print("spr_errors=" + " | ".join(spr_errors))


if __name__ == "__main__":
    main()
