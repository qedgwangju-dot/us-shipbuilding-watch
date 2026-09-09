import hashlib
import os
import re

import quantum_government_investment_watch as base


def main():
    old = base.load_state()
    new = dict(old)
    alerts = []
    old_sources = old.get("sources") or {}

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
                alerts.append(current[url])
        else:
            print(f"[QUANTUM BASELINE] {name}: 현재 자료를 기준선으로 저장")

        new.setdefault("sources", {})[name] = sorted(prev | cur)[-600:]

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
                alerts.append(current[url])
        else:
            print(f"[QUANTUM BASELINE] {name}: 현재 자료를 기준선으로 저장")

        new.setdefault("sources", {})[name] = sorted(prev | cur)[-600:]

    token = (os.environ.get("QUANTUM_TELEGRAM_BOT_TOKEN") or os.environ.get("AI_REMOTE_TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (os.environ.get("QUANTUM_TELEGRAM_CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID") or "").strip()

    # 실제 최초 구축 때만 1회 기준 알림. bootstrap_sent가 이미 true면 절대 재발송하지 않는다.
    if not old.get("initialized"):
        if not old.get("bootstrap_sent"):
            if token and chat_id:
                base.send_telegram(base.build_message(base.bootstrap_item()))
                print("[QUANTUM BOOTSTRAP SENT] 2026-09-08 3사 최종계약 기준 알림")
                new["bootstrap_sent"] = True
            else:
                print("[QUANTUM PENDING] Telegram Secret이 없어 첫 알림 보류")
                return
        new["initialized"] = True
        base.save_state(new)
        return

    # URL만 다른 동일 재게시 제거 + 같은 기업/같은 단계/같은 날짜의 사실상 중복 제거.
    seen_semantic = set()
    filtered = []
    for item in alerts:
        title = base.strip_source_suffix(item.get("title", ""))
        title_key = re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()
        prefix = " ".join(title_key.split()[:16])
        date = item.get("published_date") or ""
        semantic = f"{date}|{prefix}"
        if semantic in seen_semantic:
            continue
        seen_semantic.add(semantic)
        filtered.append(item)

    if filtered and (not token or not chat_id):
        print("[QUANTUM PENDING] 새 변화가 있으나 Telegram Secret이 없어 상태를 갱신하지 않음")
        return

    sent = 0
    for item in filtered[:8]:
        try:
            base.send_telegram(base.build_message(item))
            sent += 1
            print(f"[QUANTUM SENT] {item['source']} - {item['title']}")
        except Exception as e:
            print(f"[QUANTUM SEND FAIL] {e}")
            return

    new["initialized"] = True
    new["bootstrap_sent"] = bool(old.get("bootstrap_sent"))
    base.save_state(new)
    print(f"[QUANTUM DONE] 신규 알림 {sent}건")


if __name__ == "__main__":
    main()
