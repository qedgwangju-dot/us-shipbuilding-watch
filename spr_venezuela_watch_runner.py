#!/usr/bin/env python3
import datetime as dt
import os

from spr_venezuela_watch import (
    collect_news,
    enrich_korean,
    fetch_spr_latest,
    fetch_venezuela_imports_latest,
    format_import_alert,
    format_news_alert,
    format_spr_alert,
    load_state,
    now_kst,
    save_state,
    send_telegram,
    validate_telegram,
)


def main():
    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()
    if not token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID secret is missing")

    bot_username = validate_telegram(token)
    state = load_state()

    spr = None
    ven = None
    data_errors = []
    try:
        spr = fetch_spr_latest()
    except Exception as exc:
        data_errors.append(f"SPR: {exc}")
    try:
        ven = fetch_venezuela_imports_latest()
    except Exception as exc:
        data_errors.append(f"Venezuela imports: {exc}")

    news, source_errors = collect_news()

    # 첫 실행은 과거 기사/공식수치를 재전송하지 않고 기준선만 저장한다.
    if not state.get("initialized"):
        state["initialized"] = True
        state["seen_news"] = [x["id"] for x in news[:300]]
        if spr:
            state["spr"] = spr
        if ven:
            state["venezuela_imports"] = ven
        save_state(state)
        print(
            f"spr_venezuela_baseline_initialized=true bot=@{bot_username} "
            f"spr={spr} venezuela_imports={ven} news_baseline={len(state['seen_news'])}"
        )
        if data_errors:
            print("data_errors=" + " | ".join(data_errors))
        if source_errors:
            print("source_errors=" + " | ".join(source_errors))
        return

    messages = []

    if spr:
        old_spr = state.get("spr") or {}
        if old_spr.get("date") and spr["date"] != old_spr.get("date"):
            messages.append(format_spr_alert(spr, old_spr))
        state["spr"] = spr

    if ven:
        old_ven = state.get("venezuela_imports") or {}
        if old_ven.get("date") and ven["date"] != old_ven.get("date"):
            messages.append(format_import_alert(ven))
        state["venezuela_imports"] = ven

    seen = set(state.get("seen_news") or [])
    delivered = []
    cutoff = now_kst() - dt.timedelta(days=3)
    attempted = 0

    for item in news:
        if item["id"] in seen:
            continue
        try:
            pub = dt.datetime.fromisoformat(item["pub_kst"])
            if pub < cutoff:
                continue
        except Exception:
            pass

        attempted += 1
        try:
            enriched = enrich_korean(item)
            messages.append(format_news_alert(enriched))
            delivered.append(item["id"])
        except Exception as exc:
            # 영어 원문·오류문으로 대체하지 않고 다음 실행에서 재시도한다.
            print(f"spr_news_translation_skipped={item['id']} error={exc}")

        # 번역 장애 기사 때문에 한 실행이 길어지지 않게 신규 후보 6건까지만 시도한다.
        if len(delivered) >= 4 or attempted >= 6:
            break

    sent_ids = []
    for message in messages:
        results = send_telegram(token, chat_id, message)
        sent_ids.extend((x.get("result") or {}).get("message_id") for x in results)

    state["seen_news"] = list(dict.fromkeys(delivered + list(seen)))[:600]
    save_state(state)

    print(
        f"spr_venezuela_watch_ok=true bot=@{bot_username} messages={len(messages)} "
        f"message_ids={sent_ids} attempted_news={attempted}"
    )
    if data_errors:
        print("data_errors=" + " | ".join(data_errors))
    if source_errors:
        print("source_errors=" + " | ".join(source_errors))


if __name__ == "__main__":
    main()
