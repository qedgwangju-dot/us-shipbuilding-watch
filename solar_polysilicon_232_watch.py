import hashlib
import html
import json
import os
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

import monitor

STATE_FILE = Path("solar_polysilicon_232_state.json")
TIMEOUT = 30
HEADERS = monitor.HEADERS
KST = ZoneInfo("Asia/Seoul")
ET = ZoneInfo("America/New_York")

PROCLAMATION_URL = "https://www.whitehouse.gov/presidential-actions/2026/08/adjusting-imports-of-polysilicon-and-its-derivatives-into-the-united-states/"
BIS_232_URL = "https://www.bis.gov/about-bis/bis-leadership-and-offices/SIES/section-232-investigations"
FEDERAL_REGISTER_API = "https://www.federalregister.gov/api/v1/documents.json"

OFFICIAL_LIST_SOURCES = [
    ("미 상무부 보도자료", "https://www.commerce.gov/news/press-releases"),
    ("미 무역대표부 보도자료", "https://ustr.gov/about-us/policy-offices/press-office/press-releases"),
    ("미 세관국경보호국 뉴스", "https://www.cbp.gov/newsroom"),
]

KEYWORDS = [
    "polysilicon", "solar cell", "solar cells", "solar module", "solar modules",
    "proclamation 11052", "section 232", "minimum import price", "minimum import prices",
    "mip", "onshoring", "stockpiling", "ingot", "wafer",
]

MATERIAL_TERMS = [
    "final rule", "temporary final rule", "interim final rule", "notice",
    "onshoring", "stockpiling", "minimum import price", "tariff",
    "proclamation 11052", "section 232", "customs", "cbp",
    "trade agreement partner", "trade and security agreement",
    "import prohibition", "waiver", "htsus",
]

KNOWN_BASELINE_DOCS = {"2026-16400"}  # Proclamation 11052 Federal Register publication

def normalize_url(url: str) -> str:
    p = urlparse(url or "")
    if not p.scheme or not p.netloc:
        return url or ""
    return f"{p.scheme}://{p.netloc}{p.path.rstrip('/')}"

def clean(text: str) -> str:
    return " ".join(BeautifulSoup(text or "", "html.parser").get_text(" ", strip=True).split())

def get(url: str) -> str:
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        return r.text
    except Exception:
        r = requests.get(f"https://r.jina.ai/{url}", headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        return r.text

def load_state():
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}

def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

def ko_date(value: str) -> str:
    value = (value or "").strip()
    for fmt in ("%Y-%m-%d", "%B %d, %Y", "%b %d, %Y"):
        try:
            d = datetime.strptime(value, fmt)
            return f"{d.year}년 {d.month}월 {d.day}일"
        except Exception:
            pass
    return value

def now_kst() -> str:
    d = datetime.now(KST)
    return f"{d.year}년 {d.month}월 {d.day}일 {d.strftime('%H:%M')} KST"

def usd_krw():
    urls = [
        ("https://api.frankfurter.dev/v2/rate/USD/KRW?providers=ECB", "ECB"),
        ("https://api.frankfurter.dev/v2/rate/USD/KRW", "Frankfurter 중앙은행 집계"),
    ]
    last = None
    for url, provider in urls:
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            r.raise_for_status()
            data = r.json()
            rate = float(data.get("rate") or (data.get("rates") or {}).get("KRW"))
            if rate > 0:
                return rate, str(data.get("date") or ""), provider
        except Exception as e:
            last = e
    raise RuntimeError(f"USD/KRW 환율 조회 실패: {last}")

def krw_unit(usd: float, unit: str, rate: float) -> str:
    won = usd * rate
    if unit == "kg":
        if won >= 10000:
            return f"약 {won/10000:,.2f}만원/kg"
        return f"약 {won:,.0f}원/kg"
    if unit == "W":
        return f"약 {won:,.0f}원/W"
    return f"약 {won:,.0f}원"

def fetch_fr_documents():
    params = {
        "per_page": 100,
        "order": "newest",
        "conditions[term]": "polysilicon",
    }
    r = requests.get(FEDERAL_REGISTER_API, params=params, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    items = {}
    for x in data.get("results", []):
        title = clean(x.get("title") or "")
        abstract = clean(x.get("abstract") or "")
        text = f"{title} {abstract}".lower()
        if not any(k in text for k in ("polysilicon", "proclamation 11052")):
            continue
        doc = str(x.get("document_number") or "").strip()
        if not doc:
            continue
        items[doc] = {
            "id": doc,
            "source": "Federal Register",
            "title": title,
            "abstract": abstract,
            "publication_date": x.get("publication_date") or "",
            "effective_on": x.get("effective_on") or "",
            "url": x.get("html_url") or x.get("pdf_url") or "",
            "type": x.get("type") or "",
            "action": clean(x.get("action") or ""),
        }
    return items

def fetch_official_listing(name: str, url: str):
    raw = get(url)
    host = urlparse(url).netloc.lower().removeprefix("www.")
    soup = BeautifulSoup(raw, "html.parser")
    items = {}
    for a in soup.find_all("a", href=True):
        title = clean(a.get_text(" ", strip=True))
        if len(title) < 8:
            continue
        href = normalize_url(urljoin(url, a["href"]))
        cand_host = urlparse(href).netloc.lower().removeprefix("www.")
        if not cand_host.endswith(host):
            continue
        low = f"{title} {href}".lower()
        if not any(k in low for k in KEYWORDS):
            continue
        items[href] = {
            "id": href,
            "source": name,
            "title": title,
            "abstract": "",
            "publication_date": "",
            "effective_on": "",
            "url": href,
            "type": "official",
            "action": "",
        }
    return items

def policy_category(item):
    text = f"{item.get('title','')} {item.get('abstract','')} {item.get('action','')}".lower()
    if "stockpiling" in text:
        return "stockpiling"
    if "onshoring" in text:
        return "onshoring"
    if "minimum import price" in text or "mip" in text:
        return "mip"
    if "tariff" in text or "duty" in text:
        return "tariff"
    if "trade agreement partner" in text or "trade and security agreement" in text:
        return "partner"
    if "htsus" in text:
        return "htsus"
    if "waiver" in text or "import prohibition" in text:
        return "waiver"
    if "proclamation 11052" in text or "section 232" in text:
        return "section232"
    return "policy"

def event_key(item):
    cat = policy_category(item)
    date = item.get("publication_date") or item.get("effective_on") or ""
    title = re.sub(r"[^a-z0-9]+", " ", (item.get("title") or "").lower()).strip()
    title_key = " ".join(title.split()[:14])
    return f"{cat}|{date}|{title_key}"

def fetch_policy_snapshot():
    raw = get(PROCLAMATION_URL)
    text = clean(raw)
    patterns = {
        "poly_mip": r"\$21 per kilogram for polysilicon",
        "ingot_wafer_mip": r"\$100 per kilogram for polysilicon ingots and wafers",
        "cell_mip": r"\$0\.22 per watt for solar cells",
        "module_mip": r"\$0\.38 per watt for solar modules",
        "tariff_15": r"15 percent ad valorem",
        "effective_dec4": r"December 4, 2026",
        "korea_total15": r"Japan, Korea, Taiwan.*?shall be equal to 15 percent",
        "onshoring": r"program to incentivize investment in United States production",
    }
    snapshot = {k: bool(re.search(v, text, re.I)) for k, v in patterns.items()}
    snapshot["hash"] = hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()
    return snapshot

def title_ko(item):
    cat = policy_category(item)
    mapping = {
        "stockpiling": "미 상무부, 12월 4일 시행 앞두고 폴리실리콘 사재기 차단 규칙 공개",
        "onshoring": "미 상무부, 미국 내 폴리실리콘·잉곳·웨이퍼·셀 온쇼어링 절차 공개",
        "mip": "미국, 폴리실리콘·태양광 최소수입가격 제도 후속 조치",
        "tariff": "미국, 폴리실리콘 파생제품 Section 232 관세 후속 조치",
        "partner": "미국, 폴리실리콘 MIP·관세의 무역협정국 적용조건 변경",
        "htsus": "미국, 폴리실리콘 Section 232 관세품목표 후속 개정",
        "waiver": "미 상무부, 폴리실리콘 수입금지·면제 절차 변경",
        "section232": "미국 폴리실리콘 Section 232 정책 후속 공식 변화",
    }
    return mapping.get(cat, "미국 태양광·폴리실리콘 Section 232 중요 공식 변화")

def translate_summary(item):
    abstract = item.get("abstract") or item.get("action") or ""
    if not abstract:
        return ""
    try:
        ko = monitor.translate_piece(abstract)
        ko = monitor.compact_korean(ko, 180)
        if monitor.has_korean(ko):
            return ko
    except Exception:
        pass
    return ""

def build_message(item):
    rate, fx_date, fx_provider = usd_krw()
    cat = policy_category(item)
    url = html.escape(item.get("url") or PROCLAMATION_URL, quote=True)
    source = html.escape(item.get("source") or "미국 공식자료")
    pub = item.get("publication_date") or ""
    eff = item.get("effective_on") or ""
    lines = []
    if pub:
        lines.append(f"공식 게재일: <b>{html.escape(ko_date(pub))}</b>")
    if eff:
        lines.append(f"효력 시작일: <b>{html.escape(ko_date(eff))}</b>")

    bullets = []
    if cat == "stockpiling":
        bullets += [
            "단계: Proclamation 11052 시행 전 사재기 차단용 임시 최종규칙",
            "적용기간: 2026년 9월 22일부터 2026년 12월 3일까지 — 12월 4일 MIP·Section 232 관세 본시행 직전까지",
            "핵심: 기존 수입자의 사재기 여부를 감시하고, 2026년 8월 6일 이후 등록한 신규 수입자의 과도한 선반입을 제한하며 필요시 수입금지 가능",
            "면제: 수입금지 면제 신청 창구는 2026년 9월 22일부터 12월 3일까지 운영",
        ]
    elif cat == "onshoring":
        bullets += [
            "단계: 미국 내 업스트림 생산시설 투자 인센티브 구체화",
            "대상: 원재료 폴리실리콘·잉곳·웨이퍼·셀. 모듈은 온쇼어링 인센티브 대상 Covered Products에서 제외",
            "매출 연결: 승인기업은 투자규모에 상응하는 생산장비·Covered Products 수입에 Section 232 관세 경감 가능",
            "다음 확인: 승인기업 실명·미국 투자액·연간 생산능력·착공일·관세감면 한도",
        ]
    else:
        summary = translate_summary(item)
        if summary:
            bullets.append(f"공식 변화: {summary}")

    bullets += [
        "국가안보 축: 백악관은 태양광급 폴리실리콘·태양광 제품을 방산 프로그램과 AI 혁신에 연결되는 전략 공급망으로 명시. 후속 규정은 단순 관세보다 미국 내 폴리실리콘·잉곳·웨이퍼·셀 생산 확대와 온쇼어링 승인기업 실명이 중요",
        f"12월 4일 MIP 기준: 폴리실리콘 21달러/kg({krw_unit(21,'kg',rate)}), 잉곳·웨이퍼 100달러/kg({krw_unit(100,'kg',rate)}), 셀 0.22달러/W({krw_unit(0.22,'W',rate)}), 모듈 0.38달러/W({krw_unit(0.38,'W',rate)})",
        "한국산 관세 주의: 한국·일본·대만·EU 등은 '기존 관세 + 추가 Section 232 관세'의 합계가 15%가 되도록 규정되어 있어 모든 제품에 15%가 추가로 더 붙는 구조로 단순화하면 안 됨",
        "다음 확인: MIP 조정, 15% 관세 세부품목, 한국 등 무역협정국 예외·동등 MIP 합의, 온쇼어링 승인기업, CBP 집행지침, 실제 수입제한·위반사례",
    ]

    timeline = "\n".join(lines)
    if timeline:
        timeline += "\n"

    title = title_ko(item)
    if not monitor.has_korean(title):
        raise RuntimeError(f"한국어 제목 생성 실패로 발송 차단: {item.get('title','')}")

    return (
        "🚨 <b>미국 태양광·폴리실리콘 Section 232 중요 변화</b>\n\n"
        f"<b>{html.escape(title)}</b>\n"
        f"출처: <a href=\"{url}\">{source}</a>\n"
        f"{timeline}"
        f"확인시각: {now_kst()}\n\n"
        + "\n".join(f"• {html.escape(x)}" for x in bullets[:7])
        + f"\n환산 기준: 1달러={rate:,.2f}원 · {html.escape(fx_provider)} {html.escape(ko_date(fx_date))}"
        + f"\n\n<a href=\"{url}\"><b>원문</b></a>"
    )

def bootstrap_message():
    rate, fx_date, fx_provider = usd_krw()
    fr_url = "https://www.federalregister.gov/documents/2026/09/23/2026-19537/measures-to-restrict-stockpiling-of-polysilicon-and-polysilicon-derivatives-under-proclamation"
    wh = html.escape(PROCLAMATION_URL, quote=True)
    fr = html.escape(fr_url, quote=True)
    return (
        "🚨 <b>미국 태양광·폴리실리콘 Section 232 중요 변화</b>\n\n"
        "<b>미 상무부, 12월 4일 MIP 시행 앞두고 폴리실리콘 사재기 차단 규칙 발효</b>\n"
        f"출처: <a href=\"{fr}\">Federal Register·BIS 임시 최종규칙</a> · <a href=\"{wh}\">백악관 Proclamation 11052</a>\n\n"
        "정책 타임라인\n"
        "• <b>2025년 7월 1일</b> 미 상무부, 폴리실리콘·파생제품 Section 232 국가안보 조사 개시\n"
        "• <b>2026년 8월 6일</b> 트럼프 대통령, Proclamation 11052 서명\n"
        "• <b>2026년 8월 11일</b> Federal Register 공식 게재\n"
        "• <b>2026년 9월 22일</b> BIS 사재기 차단 임시 최종규칙 발효\n"
        "• <b>2026년 12월 3일</b> 사재기 차단 임시규칙 종료 예정\n"
        "• <b>2026년 12월 4일 00:01 ET → 2026년 12월 4일 14:01 KST</b> MIP·Section 232 관세 본시행\n\n"
        f"• MIP: 폴리실리콘 21달러/kg({krw_unit(21,'kg',rate)}), 잉곳·웨이퍼 100달러/kg({krw_unit(100,'kg',rate)}), 셀 0.22달러/W({krw_unit(0.22,'W',rate)}), 모듈 0.38달러/W({krw_unit(0.38,'W',rate)})\n"
        "• 관세: 파생제품 15% 체계. 한국·일본·대만·EU 등은 기존 Column 1 관세와 Section 232 추가관세 합계가 15%가 되도록 적용\n"
        "• 온쇼어링: 미국 내 폴리실리콘·잉곳·웨이퍼·셀 신설·증설 계획을 승인받으면 투자규모에 연동해 장비·Covered Products의 Section 232 관세감면 가능. 모듈은 Covered Products에서 제외\n"
        "• 새 변화: BIS가 기존 수입자의 사재기를 감시하고 2026년 8월 6일 이후 신규 수입자의 선반입을 제한하며, 필요시 회사·계열사의 수입금지 조치 가능\n"
        "• 투자 확인: 정책 자체보다 OCI TerraSus 등 비미국 업스트림의 미국향 판매경로, Qcells 미국 잉곳·웨이퍼·셀 내재화, 온쇼어링 승인기업 실명이 실제 매출 민감도를 가름\n"
        "• 다음 확인: MIP 수정·무역협정국 별도합의·온쇼어링 신청/승인·CBP 집행지침·수입금지 기업·12월 4일 실제 시행\n"
        f"환산 기준: 1달러={rate:,.2f}원 · {html.escape(fx_provider)} {html.escape(ko_date(fx_date))}\n"
        f"확인시각: {now_kst()}\n\n"
        f"<a href=\"{fr}\"><b>원문</b></a>"
    )

def send_telegram(text: str):
    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    if not token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN 또는 TELEGRAM_CHAT_ID가 없습니다.")
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": text[:4096],
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
        },
        timeout=TIMEOUT,
    )
    r.raise_for_status()

def main():
    old = load_state()
    new = dict(old)
    old_docs = set(old.get("fr_docs", []))
    old_urls = set(old.get("official_urls", []))
    old_events = set(old.get("event_keys", []))
    alerts = []

    try:
        fr = fetch_fr_documents()
    except Exception as e:
        print(f"[SOLAR232 WARN] Federal Register 조회 실패: {e}")
        fr = {}

    official = {}
    for name, url in OFFICIAL_LIST_SOURCES:
        try:
            found = fetch_official_listing(name, url)
            official.update(found)
            print(f"[SOLAR232 OK] {name}: {len(found)}개")
        except Exception as e:
            print(f"[SOLAR232 WARN] {name} 조회 실패: {e}")

    try:
        snapshot = fetch_policy_snapshot()
    except Exception as e:
        print(f"[SOLAR232 WARN] Proclamation 기준선 조회 실패: {e}")
        snapshot = old.get("policy_snapshot") or {}

    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()

    if not old.get("initialized"):
        if not token or not chat_id:
            print("[SOLAR232 PENDING] Telegram Secret이 없어 기준 알림 보류")
            return
        send_telegram(bootstrap_message())
        print("[SOLAR232 BOOTSTRAP SENT] Proclamation 11052 + 2026-19537")
        new["initialized"] = True
        new["bootstrap_sent"] = True
        new["fr_docs"] = sorted(set(fr.keys()))
        new["official_urls"] = sorted(set(official.keys()))
        new["event_keys"] = []
        new["policy_snapshot"] = snapshot
        save_state(new)
        return

    for doc, item in fr.items():
        if doc in KNOWN_BASELINE_DOCS or doc in old_docs:
            continue
        key = event_key(item)
        if key in old_events:
            continue
        alerts.append(item)

    for url, item in official.items():
        if url in old_urls:
            continue
        text = f"{item.get('title','')} {item.get('abstract','')}".lower()
        if not any(t in text for t in MATERIAL_TERMS):
            continue
        key = event_key(item)
        if key in old_events:
            continue
        alerts.append(item)

    # 같은 사건을 여러 공식기관이 동시에 올려도 한 번만 보낸다.
    deduped = []
    run_keys = set()
    for item in alerts:
        key = event_key(item)
        if key in run_keys:
            continue
        run_keys.add(key)
        deduped.append(item)

    if deduped and (not token or not chat_id):
        print("[SOLAR232 PENDING] 신규 정책 변화가 있으나 Telegram Secret 없음")
        return

    sent_keys = set(old_events)
    sent = 0
    for item in deduped[:8]:
        send_telegram(build_message(item))
        sent += 1
        sent_keys.add(event_key(item))
        print(f"[SOLAR232 SENT] {item.get('source')} - {item.get('title')}")

    if old.get("policy_snapshot") and snapshot and snapshot.get("hash") != old.get("policy_snapshot", {}).get("hash"):
        print("[SOLAR232 SNAPSHOT] Proclamation 정책 기준값 변화 감지 — 공식 후속문서와 함께 검증 필요")

    new["initialized"] = True
    new["bootstrap_sent"] = bool(old.get("bootstrap_sent", True))
    new["fr_docs"] = sorted(set(old_docs) | set(fr.keys()))[-1000:]
    new["official_urls"] = sorted(set(old_urls) | set(official.keys()))[-1500:]
    new["event_keys"] = sorted(sent_keys)[-1500:]
    new["policy_snapshot"] = snapshot
    save_state(new)
    print(f"[SOLAR232 DONE] 신규 알림 {sent}건")

if __name__ == "__main__":
    main()
