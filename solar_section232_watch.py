#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import html
import json
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

STATE_FILE = Path("solar_section232_state.json")
TIMEOUT = 30
KST = ZoneInfo("Asia/Seoul")
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; Solar-Section232-Watch/1.0)"}

PROCLAMATION_URL = "https://www.whitehouse.gov/presidential-actions/2026/08/adjusting-imports-of-polysilicon-and-its-derivatives-into-the-united-states/"
BIS_232_URL = "https://www.bis.gov/about-bis/bis-leadership-and-offices/SIES/section-232-investigations"
FEDERAL_RULE_URL = "https://public-inspection.federalregister.gov/2026-19537.pdf"
DOE_SPACE_PV_URL = "https://www.energy.gov/cmei/articles/does-office-critical-minerals-and-energy-innovation-announces-first-major-investment"

RSS_QUERIES = [
    ("Section 232 폴리실리콘·태양광 시행규칙", '"polysilicon" "Section 232" (BIS OR Commerce OR CBP OR "Federal Register") when:14d'),
    ("미국 태양광 온쇼어링·회사별 합의", '("polysilicon" OR "solar cell" OR "solar wafer") ("onshoring agreement" OR "company-specific" OR "Department of Commerce") when:30d'),
    ("한국 태양광 미국 온쇼어링", '(OCI OR "Hanwha Qcells" OR "Hanwha Solutions") (polysilicon OR wafer OR cell OR solar) (Section 232 OR onshoring OR Commerce) when:30d'),
    ("우주 태양광 정부지원·조달", '("space solar" OR "space photovoltaics" OR "space PV") (DOE OR NASA OR Space Force OR funding OR procurement) when:30d'),
]

MATERIAL_TERMS = [
    "minimum import price", "mip", "section 232", "proclamation 11052",
    "onshoring", "tariff", "15 percent", "15%", "stockpiling", "import prohibition",
    "waiver", "federal register", "cbp", "customs and border protection",
    "company-specific", "solar cell", "solar module", "solar wafer", "polysilicon",
    "space photovoltaics", "space pv", "procurement", "funding opportunity",
]

NEW_EVENT_TERMS = [
    "effective", "final rule", "temporary final rule", "amendment", "adjust",
    "approved", "approval", "agreement", "waiver", "prohibition", "restrict",
    "new tariff", "tariff adjustment", "mip", "minimum import price",
    "onshoring plan", "company-specific", "funding", "award", "procurement",
    "capacity", "plant", "facility", "construction", "production",
]

MARKET_ONLY_TERMS = [
    "stock jumps", "shares rise", "stock rises", "price target", "buy rating",
    "top pick", "매수 기회", "목표주가", "주가", "급등", "상승세",
]

def get(url: str) -> str:
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        return r.text
    except Exception:
        r = requests.get("https://r.jina.ai/" + url, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        return r.text

def norm(text: str) -> str:
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

def rss_url(query: str) -> str:
    return "https://news.google.com/rss/search?q=" + quote_plus(query) + "&hl=en-US&gl=US&ceid=US:en"

def parse_rss(name: str, query: str):
    url = rss_url(query)
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    root = ET.fromstring(r.text)
    out = {}
    for item in root.findall(".//item"):
        title = norm(item.findtext("title") or "")
        link = norm(item.findtext("link") or "")
        pub = norm(item.findtext("pubDate") or "")
        src = item.find("source")
        source = norm(src.text if src is not None and src.text else name)
        blob = title.lower()
        if not title or not link or not any(x in blob for x in MATERIAL_TERMS):
            continue
        if any(x in blob for x in MARKET_ONLY_TERMS) and not any(x in blob for x in NEW_EVENT_TERMS):
            continue
        published = ""
        if pub:
            try:
                dt = parsedate_to_datetime(pub)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                published = dt.astimezone(KST).strftime("%Y-%m-%d %H:%M KST")
            except Exception:
                published = pub
        out[link] = {"title": title, "url": link, "source": source, "published": published}
    return out

def current_static_facts():
    facts = {}

    # Proclamation 11052 baseline
    p = norm(get(PROCLAMATION_URL))
    facts["proclamation"] = {
        "signed": "2026-08-06",
        "effective": "2026-12-04",
        "mip_polysilicon_usd_per_kg": 21,
        "mip_ingot_wafer_usd_per_kg": 100,
        "mip_cell_usd_per_w": 0.22,
        "mip_module_usd_per_w": 0.38,
        "derivative_tariff_pct": 15,
        "korea_total_tariff_pct": 15,
        "url": PROCLAMATION_URL,
        "hash": hashlib.sha256(p.encode()).hexdigest(),
    }

    # Anti-stockpiling temporary final rule
    try:
        f = norm(get(FEDERAL_RULE_URL))
        facts["anti_stockpiling"] = {
            "effective": "2026-09-22",
            "expires": "2026-12-03",
            "document": "2026-19537",
            "url": FEDERAL_RULE_URL,
            "hash": hashlib.sha256(f.encode()).hexdigest(),
        }
    except Exception as e:
        print(f"[SOLAR WARN] Federal Register rule fetch failed: {e}")

    # DOE space PV funding
    try:
        d = norm(get(DOE_SPACE_PV_URL))
        facts["space_pv"] = {
            "announced": "2026-08-31",
            "funding_usd_m": 12,
            "url": DOE_SPACE_PV_URL,
            "hash": hashlib.sha256(d.encode()).hexdigest(),
        }
    except Exception as e:
        print(f"[SOLAR WARN] DOE space PV fetch failed: {e}")

    return facts

def usdkrw():
    try:
        r = requests.get("https://api.frankfurter.app/latest", params={"from":"USD","to":"KRW"}, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        rate = float((data.get("rates") or {}).get("KRW"))
        return rate, str(data.get("date") or "")
    except Exception:
        return None, ""

def krw(won: float) -> str:
    if won >= 1_000_000_000_000:
        jo = int(won // 1_000_000_000_000)
        eok = round((won - jo*1_000_000_000_000)/100_000_000)
        return f"약 {jo}조{eok:,}억원" if eok else f"약 {jo}조원"
    if won >= 100_000_000:
        return f"약 {won/100_000_000:,.0f}억원"
    if won >= 10_000:
        return f"약 {won/10_000:,.0f}만원"
    return f"약 {won:,.0f}원"

def kdate(s: str) -> str:
    try:
        d = datetime.strptime(s, "%Y-%m-%d")
        return f"{d.year}년 {d.month}월 {d.day}일"
    except Exception:
        return s

def send(text: str):
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

def baseline_message():
    rate, fxdate = usdkrw()
    mips = []
    if rate:
        mips = [
            f"폴리실리콘 21달러/kg({rate*21:,.0f}원/kg)",
            f"잉곳·웨이퍼 100달러/kg({rate*100:,.0f}원/kg)",
            f"태양전지 0.22달러/W({rate*0.22:,.0f}원/W)",
            f"태양광 모듈 0.38달러/W({rate*0.38:,.0f}원/W)",
        ]
    else:
        mips = ["폴리실리콘 21달러/kg", "잉곳·웨이퍼 100달러/kg", "태양전지 0.22달러/W", "태양광 모듈 0.38달러/W"]

    url = html.escape(PROCLAMATION_URL, quote=True)
    return (
        "🚨 <b>미국 태양광·폴리실리콘 Section 232 중요 변화</b>\n\n"
        "<b>태양광·반도체 폴리실리콘 공급망, 미국 국가안보 Section 232 체계로 편입</b>\n"
        f"출처: <a href=\"{url}\">백악관 Proclamation 11052</a>\n\n"
        f"• <b>{kdate('2025-07-01')}</b> 상무부, 폴리실리콘·파생제품 Section 232 국가안보 조사 개시\n"
        f"• <b>{kdate('2026-08-06')}</b> 대통령 Proclamation 11052 서명\n"
        f"• <b>{kdate('2026-09-22')}</b> BIS, 12월 시행 전 재고선취를 막는 임시 반재고축적 규칙 발효\n"
        f"• <b>{kdate('2026-12-04')}</b> 최소수입가격(MIP)+파생제품 15% 관세 시행\n"
        f"• MIP: {' · '.join(mips)}\n"
        "• 한국산: Section 232 추가관세와 일반 관세를 합친 총 관세율을 15%로 맞추는 특별처리\n"
        "• 온쇼어링: 미국 내 폴리실리콘·잉곳·웨이퍼·셀 투자계획 승인 시 생산장비·필요 투입물의 Section 232 관세 혜택 가능. 모듈은 Covered Products에 포함되지 않음\n"
        "• 다음 확인: BIS·CBP 시행세칙, MIP 조정, 회사별 온쇼어링 승인, OCI·한화 계열 미국 증설/원재료 계약, 재고축적 제재, 우주태양광 정부조달\n"
        + (f"환산 기준: 1달러={rate:,.2f}원 · {fxdate}\n" if rate else "")
        + f"\n<a href=\"{url}\"><b>원문</b></a>"
    )

def article_key(item):
    text = (item.get("title") or "").lower()
    company = "sector"
    for c, aliases in {
        "oci":["oci"], "hanwha":["hanwha","qcells","q cells"], "firstsolar":["first solar"],
        "bis":["bureau of industry and security","bis"], "cbp":["cbp","customs and border protection"],
    }.items():
        if any(a in text for a in aliases):
            company = c
            break
    action = "policy"
    for a, terms in [
        ("onshoring",["onshoring","company-specific"]),
        ("mip",["minimum import price","mip"]),
        ("tariff",["tariff","section 232"]),
        ("waiver",["waiver"]),
        ("enforcement",["stockpiling","import prohibition","restrict"]),
        ("funding",["funding","award","procurement"]),
        ("capacity",["capacity","plant","facility","construction","production"]),
    ]:
        if any(t in text for t in terms):
            action = a
            break
    date = (item.get("published") or "")[:10] or "unknown"
    return f"{company}|{action}|{date}"

def article_message(item):
    title = norm(item["title"])
    url = html.escape(item["url"], quote=True)
    pub = item.get("published") or ""
    return (
        "🚨 <b>미국 태양광·폴리실리콘 Section 232 중요 변화</b>\n\n"
        f"<b>{html.escape(title)}</b>\n"
        f"출처: <a href=\"{url}\">{html.escape(item.get('source') or '보도')}</a>\n"
        + (f"공개일: <b>{html.escape(pub)}</b>\n" if pub else "")
        + "\n• 단계: 신규 정책·시행·온쇼어링·공급망 변화 후보\n"
        "• 판정 원칙: 같은 8월 6일 Proclamation 11052 재기사는 제외하고, MIP·관세·시행일·회사별 온쇼어링 승인·실제 설비투자·정부조달처럼 상태가 바뀐 경우만 후속 알림\n"
        "• 다음 확인: 공식 BIS·CBP·Federal Register·회사 공시로 재검증\n\n"
        f"<a href=\"{url}\"><b>원문</b></a>"
    )

def main():
    old = load_state()
    facts = current_static_facts()
    new = dict(old)
    new["facts"] = facts

    rss_current = {}
    candidates = []
    for name, query in RSS_QUERIES:
        try:
            rows = parse_rss(name, query)
        except Exception as e:
            print(f"[SOLAR WARN] {name}: {e}")
            continue
        key = hashlib.sha1(query.encode()).hexdigest()[:10]
        prev = set((old.get("rss") or {}).get(key, []))
        cur = set(rows.keys())
        if old.get("initialized"):
            for u in sorted(cur - prev):
                candidates.append(rows[u])
        rss_current[key] = sorted(prev | cur)[-500:]
        print(f"[SOLAR OK] {name}: {len(rows)}")

    new["rss"] = rss_current

    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()

    if not old.get("initialized"):
        if not token or not chat:
            print("[SOLAR PENDING] Telegram secret missing")
            return
        send(baseline_message())
        print("[SOLAR BOOTSTRAP SENT] Proclamation 11052 / anti-stockpiling / Dec 4")
        new["initialized"] = True
        new["event_keys"] = []
        save_state(new)
        return

    old_events = set(old.get("event_keys") or [])
    run_events = set()
    sent_events = set(old_events)
    sent = 0
    for item in candidates:
        ek = article_key(item)
        if ek in old_events or ek in run_events:
            print(f"[SOLAR DEDUPE] {item['title']}")
            continue
        # 단순 시장전망/주가반응은 차단
        low = item["title"].lower()
        if any(x in low for x in MARKET_ONLY_TERMS) and not any(x in low for x in NEW_EVENT_TERMS):
            continue
        if not token or not chat:
            print("[SOLAR PENDING] new event but no Telegram secret")
            return
        send(article_message(item))
        sent += 1
        run_events.add(ek)
        sent_events.add(ek)
        print(f"[SOLAR SENT] {item['source']} - {item['title']}")
        if sent >= 6:
            break

    new["initialized"] = True
    new["event_keys"] = sorted(sent_events)[-1000:]
    save_state(new)
    print(f"[SOLAR DONE] 신규 알림 {sent}건")

if __name__ == "__main__":
    main()
