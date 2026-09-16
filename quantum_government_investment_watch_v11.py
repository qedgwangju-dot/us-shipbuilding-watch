import hashlib
import os
import re
from datetime import datetime

import quantum_government_investment_watch as base
import quantum_government_investment_watch_v10  # strict scope + FX validation patches


# URL이 아니라 '사건 상태'를 기억한다. 같은 계약/지원이 다른 언론 URL로 다시 나와도 재발송하지 않는다.
KNOWN_RECAP_NEW_TERMS = [
    "new tranche", "additional tranche", "payment received", "payment released", "disbursed",
    "milestone achieved", "milestone completed", "award amended", "award increased", "award reduced",
    "additional award", "new procurement", "purchase order", "supplier selected", "selected supplier",
    "deployment completed", "installed", "commercial order", "customer order",
    "government sold", "share sale", "repurchase", "additional shares", "new shares",
]


def _known_chips_recap(item) -> bool:
    text = f"{item.get('title','')} {item.get('summary','')} {item.get('article_text','')}".lower().replace(",", "")
    if any(x in text for x in KNOWN_RECAP_NEW_TERMS):
        return False

    # 2026-09-08 이미 기준선으로 확정한 회사별/묶음 CHIPS 지원 재보도
    known_companies = {
        "d-wave": ["d-wave", "dwave", "qbts"],
        "rigetti": ["rigetti", "rgti"],
        "quantinuum": ["quantinuum"],
        "psiquantum": ["psiquantum"],
        "globalfoundries": ["globalfoundries", "global foundries", "gfs"],
    }
    present = [name for name, aliases in known_companies.items() if any(a in text for a in aliases)]
    chips_context = any(x in text for x in ["chips act", "chips and science act", "department of commerce", "commerce department"])
    award_context = any(x in text for x in ["award", "funding", "investment", "equity", "stake", "definitive agreement", "finalize", "finalise"])
    known_amount = any(x in text for x in [
        "$100 million", "$100m", "100 million", "$300 million", "$300m", "300 million",
        "$375 million", "$375m", "375 million", "$675 million", "$675m", "675 million",
        "$2 billion", "$2bn", "2 billion", "$2.0 billion", "$2.013 billion", "2.013 billion",
    ])
    if present and chips_context and award_context and known_amount:
        return True
    if ("nine quantum" in text or "9 quantum" in text or "nine companies" in text) and chips_context and known_amount:
        return True
    return False


def _parse_date_month(value: str) -> str:
    value = (value or "").strip()
    m = re.search(r"(20\d{2})-(\d{2})", value)
    return f"{m.group(1)}-{m.group(2)}" if m else ""


def _fresh(item, max_days=7) -> bool:
    value = (item.get("published_date") or "").strip()
    m = re.match(r"(20\d{2})-(\d{2})-(\d{2})", value)
    if not m:
        return True
    try:
        d = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=base.KST).date()
        age = (datetime.now(base.KST).date() - d).days
        return -1 <= age <= max_days
    except Exception:
        return True


def _event_action(text: str) -> str:
    low = text.lower()
    groups = [
        ("payment", ["milestone payment", "payment received", "payment released", "tranche", "disbursement", "disbursed"]),
        ("procurement", ["procurement", "government purchase", "purchase order", "advance market commitment", "supplier selected", "selected supplier"]),
        ("final_award", ["final award", "definitive agreement", "finalizes", "finalised", "finalized"]),
        ("loi", ["letter of intent"]),
        ("equity", ["equity stake", "minority stake", "non-controlling stake"]),
        ("award", ["award", "funding", "grant", "investment"]),
        ("budget", ["appropriation", "budget request"]),
        ("solicitation", ["solicitation", "request for proposal", "request for information", "notice of funding opportunity", "nofo"]),
    ]
    for name, terms in groups:
        if any(t in low for t in terms):
            return name
    return "policy"


def _program(text: str) -> str:
    low = text.lower()
    for name, terms in [
        ("chips", ["chips act", "chips and science act"]),
        ("qc-adds", ["qc-adds"]),
        ("qbi", ["quantum benchmarking initiative", "qbi"]),
        ("genesis", ["quantum genesis"]),
    ]:
        if any(t in low for t in terms):
            return name
    return "quantum"


def _companies(text: str):
    low = text.lower()
    aliases = {
        "d-wave": ["d-wave", "dwave", "qbts"],
        "rigetti": ["rigetti", "rgti"],
        "quantinuum": ["quantinuum"],
        "psiquantum": ["psiquantum"],
        "globalfoundries": ["globalfoundries", "global foundries", "gfs"],
        "ibm": ["ibm"],
        "atom": ["atom computing"],
        "diraq": ["diraq"],
        "infleqtion": ["infleqtion"],
        "ionq": ["ionq"],
    }
    return sorted(name for name, keys in aliases.items() if any(k in low for k in keys))


def _amounts(text: str):
    low = text.lower().replace(",", "")
    found = []
    for m in re.finditer(r"\$\s*([0-9]+(?:\.[0-9]+)?)\s*(billion|million|bn|mn|b|m)\b", low):
        found.append(f"{m.group(1)}{m.group(2)[0]}")
    return sorted(set(found))[:4]


def _event_keys(item):
    text = f"{item.get('title','')} {item.get('summary','')} {item.get('article_text','')}"
    comps = _companies(text) or ["sector"]
    action = _event_action(text)
    program = _program(text)
    month = _parse_date_month(item.get("published_date", "")) or "unknown"
    amounts = ",".join(_amounts(text)) or "na"
    return [f"{c}|{program}|{action}|{month}|{amounts}" for c in comps]


def main():
    old = base.load_state()
    old_sources = old.get("sources") or {}
    old_events = set(old.get("event_keys") or [])
    new = dict(old)
    new_sources = dict(old_sources)
    candidates = []

    for source in base.OFFICIAL_SOURCES:
        name = source["name"]
        try:
            current = base.fetch_html_source(source)
            print(f"[QUANTUM OK] {name}: {len(current)}개")
        except Exception as e:
            print(f"[QUANTUM WARN] {name}: {e}")
            continue
        prev = set(old_sources.get(name, []))
        cur = set(current.keys())
        if old.get("initialized") and name in old_sources:
            for url in sorted(cur - prev):
                x = dict(current[url]); x["_state_source"] = name; candidates.append(x)
        else:
            print(f"[QUANTUM BASELINE] {name}: 현재 자료를 기준선으로 저장")
        new_sources[name] = sorted(prev | cur)[-800:]

    for source in base.NEWS_RSS:
        name = source["name"] + "::" + hashlib.sha1(source["url"].encode()).hexdigest()[:8]
        try:
            current = base.fetch_news_rss(source)
            print(f"[QUANTUM OK] {name}: {len(current)}개")
        except Exception as e:
            print(f"[QUANTUM WARN] {name}: {e}")
            continue
        prev = set(old_sources.get(name, []))
        cur = set(current.keys())
        if old.get("initialized") and name in old_sources:
            for url in sorted(cur - prev):
                x = dict(current[url]); x["_state_source"] = name; candidates.append(x)
        else:
            print(f"[QUANTUM BASELINE] {name}: 현재 자료를 기준선으로 저장")
        new_sources[name] = sorted(prev | cur)[-800:]

    new["sources"] = new_sources
    new["initialized"] = True
    new["bootstrap_sent"] = bool(old.get("bootstrap_sent", True))

    run_events = set()
    alerts = []
    for item in candidates:
        enriched = base.fetch_article_meta(dict(item))
        if not _fresh(enriched):
            print(f"[QUANTUM STALE] 과거 자료 제외: {enriched.get('published_date','')} | {enriched.get('title','')}")
            continue
        if _known_chips_recap(enriched):
            print(f"[QUANTUM EVENT DEDUPE] 기존 CHIPS 사건 재기사 제외: {enriched.get('title','')}")
            continue
        keys = _event_keys(enriched)
        if any(k in old_events or k in run_events for k in keys):
            print(f"[QUANTUM EVENT DEDUPE] 동일 사건 상태 제외: {enriched.get('title','')}")
            continue
        enriched["_event_keys"] = keys
        alerts.append(enriched)
        run_events.update(keys)

    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    if alerts and (not token or not chat_id):
        print("[QUANTUM PENDING] Telegram Secret이 없어 신규 사건 알림 보류")
        return

    sent_events = set(old_events)
    sent = 0
    for item in alerts[:8]:
        try:
            base.send_telegram(base.build_message(item))
            sent += 1
            sent_events.update(item.get("_event_keys") or [])
            print(f"[QUANTUM SENT] {item.get('source')} - {item.get('title')}")
        except Exception as e:
            print(f"[QUANTUM SEND FAIL] {e}")
            # 이미 성공한 이벤트 키는 보존하고, 실패한 URL은 다음 실행에서 다시 볼 수 있도록 source 상태에서 제거
            src = item.get("_state_source")
            if src and item.get("url") in new_sources.get(src, []):
                new_sources[src] = [u for u in new_sources[src] if u != item.get("url")]
            break

    new["sources"] = new_sources
    new["event_keys"] = sorted(sent_events)[-1500:]
    base.save_state(new)
    print(f"[QUANTUM DONE] 신규 사건 알림 {sent}건")


if __name__ == "__main__":
    main()
