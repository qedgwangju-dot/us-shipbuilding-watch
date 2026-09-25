import html
import json
import os
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

STATE_FILE = Path("space_pv_state.json")
TIMEOUT = 30
KST = ZoneInfo("Asia/Seoul")
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; Space-PV-Watch/1.0)"}

DOE_PIA_URL = "https://www.energy.gov/cmei/systems/space-photovoltaics-research-and-development-partnership-intermediary-agreement"
DOE_ARTICLE_URL = "https://www.energy.gov/cmei/articles/does-office-critical-minerals-and-energy-innovation-announces-first-major-investment"
DOE_FUNDING_URL = "https://www.energy.gov/cmei/systems/funding-opportunities-integrated-energy-systems"

def get(url):
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r.text

def clean(text):
    return " ".join(BeautifulSoup(text or "", "html.parser").get_text(" ", strip=True).split())

def load_state():
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}

def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

def now_kst():
    d = datetime.now(KST)
    return f"{d.year}년 {d.month}월 {d.day}일 {d.strftime('%H:%M')} KST"

def snapshot():
    text = clean(get(DOE_PIA_URL))
    values = {}

    m = re.search(r"award up to \$([0-9.]+) million", text, re.I)
    values["total_funding_usd_m"] = float(m.group(1)) if m else 12.0

    m = re.search(r"Submission Deadline for Full Applications:\s*([^|]+?)\s*Expected", text, re.I)
    values["deadline"] = clean(m.group(1)) if m else "October 8, 2026 at 11:59 p.m. ET"

    m = re.search(r"Expected\s*Timeframe\s*for\s*Selection\s*Notifications:\s*([^|]+?)\s*Expected", text, re.I)
    values["selection"] = clean(m.group(1)) if m else "December 2026"

    m = re.search(r"Expected\s*Timeframe\s*for\s*Award\s*Negotiations:\s*([^|]+)", text, re.I)
    values["negotiation"] = clean(m.group(1))[:80] if m else "January-February 2027"

    # 수혜자/선정/조달이 생길 때만 상태변화로 본다.
    values["has_recipients"] = bool(re.search(r"recipient|selected project|selection announced|awardee", text, re.I))
    values["has_procurement"] = bool(re.search(r"procurement|purchase order|contract award", text, re.I))
    return values

def semantic_key(s):
    return json.dumps(s, ensure_ascii=False, sort_keys=True)

def usdkrw():
    r = requests.get("https://api.frankfurter.dev/v2/rate/USD/KRW?providers=ECB", headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    return float(data["rate"]), str(data.get("date") or "")

def build_message(old, new):
    rate, fx_date = usdkrw()
    krw_eok = new["total_funding_usd_m"] * 1_000_000 * rate / 100_000_000
    changes = []
    for key, label in [
        ("total_funding_usd_m", "총 지원규모"),
        ("deadline", "신청 마감"),
        ("selection", "선정 통보 일정"),
        ("negotiation", "지원협상 일정"),
        ("has_recipients", "선정기업·기관 공개"),
        ("has_procurement", "조달·계약 공개"),
    ]:
        if old.get(key) != new.get(key):
            changes.append(f"{label}: {old.get(key)} → {new.get(key)}")

    url = html.escape(DOE_PIA_URL, quote=True)
    bullets = [
        "달라진 점: " + (" / ".join(changes[:4]) if changes else "DOE 공식 우주태양광 지원 상태가 변경"),
        f"현재 총 지원규모: 최대 {new['total_funding_usd_m']:g}백만달러(약 {krw_eok:,.0f}억원)",
        f"현재 일정: 신청 마감 {new['deadline']} · 선정 통보 {new['selection']} · 지원협상 {new['negotiation']}",
        "구분: 지상 태양광 Section 232·폴리실리콘 관세와 별도인 우주용 태양광 연구개발·미국 내 제조역량 정책",
        "다음 확인: 선정기업·기관 실명, 개별 지원액, 우주비행 실증 파트너, 생산능력, 실제 조달·계약",
    ]

    return (
        "🚨 <b>미국 우주태양광 정부지원·조달 중요 변화</b>\n\n"
        "<b>DOE 우주태양광 연구개발·미국 제조지원 상태 변화</b>\n"
        f"출처: <a href=\"{url}\">미 에너지부 공식 우주태양광 PIA</a>\n"
        f"확인시각: {now_kst()}\n\n"
        + "\n".join(f"• {html.escape(x)}" for x in bullets)
        + f"\n환산 기준: 1달러={rate:,.2f}원 · ECB {fx_date}"
        + f"\n\n<a href=\"{url}\"><b>원문</b></a>"
    )

def send(text):
    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    if not token or not chat_id:
        raise RuntimeError("Telegram secret missing")
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id":chat_id, "text":text[:4096], "parse_mode":"HTML", "link_preview_options":{"is_disabled":True}},
        timeout=TIMEOUT,
    )
    r.raise_for_status()

def main():
    old_state = load_state()
    current = snapshot()

    # 첫 실행은 8월 31일 기존 공고를 새 뉴스처럼 재발송하지 않고 기준선만 저장.
    if not old_state.get("initialized"):
        save_state({"initialized": True, "snapshot": current})
        print("[SPACEPV BASELINE] 2026-08-31 DOE 기존 공고를 기준선으로 저장, 재발송 없음")
        return

    old = old_state.get("snapshot") or {}
    if semantic_key(old) == semantic_key(current):
        print("[SPACEPV DONE] 실질 상태 변화 없음")
        return

    send(build_message(old, current))
    save_state({"initialized": True, "snapshot": current})
    print("[SPACEPV SENT] DOE 공식 우주태양광 상태 변화")

if __name__ == "__main__":
    main()
