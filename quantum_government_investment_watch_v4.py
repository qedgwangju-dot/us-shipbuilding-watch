import os
import re

import requests

import quantum_government_investment_watch as base
import quantum_government_investment_watch_v3 as v3
import quantum_government_investment_watch_v2 as v2


# 이번 웹감시는 AI 원격접근 전용 봇이 아니라 기존 메인 정책/HBM 수신 봇으로 보낸다.
def send_telegram_main_bot(text: str):
    token = (os.environ.get("QUANTUM_TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (os.environ.get("QUANTUM_TELEGRAM_CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    if not token or not chat_id:
        raise RuntimeError("QUANTUM_TELEGRAM_BOT_TOKEN/TELEGRAM_BOT_TOKEN 또는 TELEGRAM_CHAT_ID가 없습니다.")

    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": text[:4096],
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
        },
        timeout=base.TIMEOUT,
    )
    r.raise_for_status()


base.send_telegram = send_telegram_main_bot


_original_fetch_news_rss = base.fetch_news_rss

# 이미 2026-09-08 공식자료로 확인된 3사 CHIPS 최종계약의 단순 재기사/주가해설은 알림하지 않는다.
MARKET_COMMENTARY = [
    r"\bwhy\b.*\bstock\b",
    r"\bstock\b.*\bpopp?ed\b",
    r"\bstock\b.*\bjump(?:s|ed)?\b",
    r"\bshares?\b.*\bsoar(?:s|ed)?\b",
    r"\bquantum stocks?\b",
    r"\bstock price\b",
    r"\bwhat happened\b.*\bstock\b",
]

KNOWN_3COMPANY_EVENT = [
    "d-wave", "rigetti", "quantinuum",
]

MATERIAL_NEW_FACTS = [
    "equity percentage", "stake percentage", "ownership percentage", "share price",
    "exercise price", "warrant", "milestone payment", "payment milestone", "payment schedule",
    "disbursement", "tranche", "procurement", "government purchase", "purchase order",
    "advance market commitment", "qc-adds", "supplier selection", "selected supplier",
    "delivery", "deployment", "customer order", "commercial order", "contract term",
    "globalfoundries", "monarch quantum", "foundry agreement", "manufacturing agreement",
    "final award amount changed", "award increased", "award reduced",
]


def _looks_like_known_recap(item) -> bool:
    text = f"{item.get('title','')} {item.get('summary','')}".lower()
    has_all_three = all(x in text for x in KNOWN_3COMPANY_EVENT)
    has_money = any(x in text for x in ["$300 million", "$300m", "300 million", "three hundred million"])
    has_government = any(x in text for x in ["government", "commerce", "chips", "u.s."])
    return (has_all_three and has_government) or (has_all_three and has_money)


def _has_material_new_fact(item) -> bool:
    text = f"{item.get('title','')} {item.get('summary','')}".lower()
    return any(x in text for x in MATERIAL_NEW_FACTS)


def _is_market_commentary(item) -> bool:
    title = base.strip_source_suffix(item.get("title", "")).lower()
    return any(re.search(pat, title) for pat in MARKET_COMMENTARY)


def fetch_news_rss_filtered(source):
    items = _original_fetch_news_rss(source)
    out = {}
    for url, item in items.items():
        if _is_market_commentary(item):
            print(f"[QUANTUM DEDUPE] 주가해설 제외: {item.get('title','')}")
            continue
        if _looks_like_known_recap(item) and not _has_material_new_fact(item):
            print(f"[QUANTUM DEDUPE] 2026-09-08 공식 3사 계약 단순 재기사 제외: {item.get('title','')}")
            continue
        out[url] = item
    return out


base.fetch_news_rss = fetch_news_rss_filtered


# 메시지 생성은 v3의 정부조달·시장창출 확장 형식을 그대로 사용한다.
base.build_message = v3.build_message


if __name__ == "__main__":
    v2.main()
