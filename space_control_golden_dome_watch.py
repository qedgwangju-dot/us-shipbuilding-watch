import hashlib
import html
import json
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

import monitor

STATE_FILE = Path("space_control_golden_dome_state.json")
TIMEOUT = 30
HEADERS = monitor.HEADERS
KST = ZoneInfo("Asia/Seoul")

OFFICIAL_SOURCES = [
    {"name": "미 공군 공식 뉴스", "url": "https://www.af.mil/News/"},
    {"name": "미 우주군 공식 뉴스", "url": "https://www.spaceforce.mil/News/"},
    {"name": "미 우주시스템사령부", "url": "https://www.ssc.spaceforce.mil/Newsroom/"},
    {"name": "미 우주개발청", "url": "https://www.sda.mil/"},
]

REPORT_SOURCES = [
    {"name": "Air & Space Forces", "url": "https://www.airandspaceforces.com/category/space/"},
]

NEWS_RSS = [
    {
        "name": "우주통제·대우주 감시",
        "url": "https://news.google.com/rss/search?q=%22on-orbit+space+control%22+OR+counterspace+OR+%22space+control+weapons%22&hl=en-US&gl=US&ceid=US:en",
    },
    {
        "name": "Golden Dome·SBI 감시",
        "url": "https://news.google.com/rss/search?q=%22Golden+Dome%22+%22space-based+interceptor%22+OR+SBI&hl=en-US&gl=US&ceid=US:en",
    },
    {
        "name": "Space-Based AMTI 감시",
        "url": "https://news.google.com/rss/search?q=%22Space-Based+AMTI%22+OR+%22airborne+moving+target%22+Space+Force&hl=en-US&gl=US&ceid=US:en",
    },
]

SPACE_TERMS = [
    "space control", "space-control", "counterspace", "counter-space", "on-orbit",
    "orbital weapon", "space weapon", "electromagnetic warfare", "meadowlands",
    "counter communications system", "golden dome", "space-based interceptor",
    "space based interceptor", "sbi", "airborne moving target", "space-based amti",
    "space based amti", "amti", "space superiority", "satellite jammer", "jamming",
    "rendezvous", "proximity operations", "space domain awareness", "darc",
]

MATERIAL_TERMS = [
    "award", "contract", "task order", "prototype", "flight-ready", "flight ready",
    "launch", "deployed", "deployment", "operational", "initial capability",
    "gate two", "gate three", "gate four", "selected", "supplier", "vendor",
    "billion", "million", "$", "budget", "appropriation", "funding",
    "weapon", "interceptor", "jammer", "laser", "electronic warfare",
]

COMPANY_ALIASES = {
    "SpaceX": ["spacex"],
    "Lockheed Martin": ["lockheed martin", "lockheed"],
    "Northrop Grumman": ["northrop grumman", "northrop"],
    "RTX/Raytheon": ["raytheon", "rtx"],
    "General Dynamics": ["general dynamics"],
    "Anduril": ["anduril"],
    "Booz Allen Hamilton": ["booz allen"],
    "L3Harris": ["l3harris", "l3 harris"],
    "True Anomaly": ["true anomaly"],
    "Turion Space": ["turion space"],
    "SciTec": ["scitec", "sci-tec"],
    "Quindar": ["quindar"],
    "GITAI": ["gitai"],
    "Johns Hopkins APL": ["johns hopkins", "applied physics laboratory"],
    "Firefly Aerospace": ["firefly aerospace"],
    "Boeing": ["boeing"],
    "Blue Origin": ["blue origin"],
    "Umbra": ["umbra"],
}


def clean(text: str) -> str:
    return " ".join(BeautifulSoup(text or "", "html.parser").get_text(" ", strip=True).split())


def normalize_url(url: str) -> str:
    p = urlparse((url or "").strip())
    if not p.scheme or not p.netloc:
        return (url or "").strip()
    return f"{p.scheme}://{p.netloc}{p.path.rstrip('/')}"


def relevant(text: str) -> bool:
    low = clean(text).lower()
    return any(x in low for x in SPACE_TERMS) and any(x in low for x in MATERIAL_TERMS)


def get_text(url: str):
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        return r.text
    except Exception:
        r = requests.get(f"https://r.jina.ai/{url}", headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        return r.text


def extract_date(text: str) -> str:
    text = clean(text)
    patterns = [
        r"\b(September|August|July|June|May|April|March|February|January|October|November|December)\s+\d{1,2},\s+202\d\b",
        r"\b202\d[-/.]\d{1,2}[-/.]\d{1,2}\b",
    ]
    for pat in patterns:
        m = re.search(pat, text, flags=re.I)
        if not m:
            continue
        raw = m.group(0)
        for fmt in ("%B %d, %Y", "%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
            try:
                return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
            except Exception:
                pass
    return ""


def fetch_html_source(source, stage):
    raw = get_text(source["url"])
    soup = BeautifulSoup(raw, "html.parser")
    base_host = urlparse(source["url"]).netloc.lower().removeprefix("www.")
    items = {}
    for a in soup.find_all("a", href=True):
        title = clean(a.get_text(" ", strip=True))
        if len(title) < 12:
            continue
        url = normalize_url(urljoin(source["url"], a["href"]))
        host = urlparse(url).netloc.lower().removeprefix("www.")
        if not host.endswith(base_host):
            continue
        if not relevant(f"{title} {url}"):
            continue
        items[url] = {
            "source": source["name"], "title": title[:350], "url": url,
            "summary": "", "stage": stage, "published_date": "",
        }
    return items


def node_text(node, tag):
    for child in list(node):
        if child.tag.split("}")[-1].lower() == tag:
            return clean("".join(child.itertext()))
    return ""


def fetch_rss(source):
    r = requests.get(source["url"], headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    root = ET.fromstring(r.text)
    items = {}
    for entry in [x for x in root.iter() if x.tag.split("}")[-1].lower() == "item"]:
        title = node_text(entry, "title")
        link = node_text(entry, "link")
        desc = node_text(entry, "description")
        src = node_text(entry, "source")
        pub = node_text(entry, "pubdate")
        if not title or not link or not relevant(f"{title} {desc}"):
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
        items[normalize_url(link)] = {
            "source": src or source["name"], "title": title, "url": link,
            "summary": desc, "stage": "보도", "published_date": published,
        }
    return items


def enrich(item):
    item = dict(item)
    try:
        text = clean(get_text(item["url"]))
        item["article_text"] = text[:15000]
        if not item.get("published_date"):
            item["published_date"] = extract_date(text)
    except Exception:
        item["article_text"] = ""
    return item


def usd_krw():
    r = requests.get("https://api.frankfurter.app/latest", params={"from": "USD", "to": "KRW"}, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    return float(data["rates"]["KRW"]), str(data.get("date") or "")


def krw_text(usd: float, rate: float) -> str:
    won = usd * rate
    if won >= 1_000_000_000_000:
        jo = int(won // 1_000_000_000_000)
        eok = round((won - jo * 1_000_000_000_000) / 100_000_000)
        return f"약 {jo}조{eok:,.0f}억원" if eok else f"약 {jo}조원"
    if won >= 100_000_000:
        return f"약 {won / 100_000_000:,.0f}억원"
    if won >= 10_000:
        return f"약 {won / 10_000:,.0f}만원"
    return f"약 {won:,.0f}원"


def money_mentions(text: str):
    mentions = []
    patterns = [
        re.compile(r"\$\s*(\d[\d,.]*)\s*(billion|million|bn|mn|B|M)\b", re.I),
        re.compile(r"(\d[\d,.]*)\s*(billion|million|bn|mn|B|M)\s*(?:U\.S\.\s*)?dollars?\b", re.I),
    ]
    for pat in patterns:
        for m in pat.finditer(text):
            num = float(m.group(1).replace(",", ""))
            scale = m.group(2).lower()
            if scale in ("billion", "bn", "b"):
                usd = num * 1_000_000_000
            else:
                usd = num * 1_000_000
            key = (m.group(0), usd)
            if key not in mentions:
                mentions.append(key)
    return mentions[:4]


def companies(text: str):
    low = text.lower()
    found = []
    for name, keys in COMPANY_ALIASES.items():
        if any(k in low for k in keys):
            found.append(name)
    return found[:8]


def category(text: str):
    low = text.lower()
    if "on-orbit" in low or "space control weapon" in low or "counterspace" in low or "space weapon" in low:
        return "space_control"
    if "space-based interceptor" in low or "space based interceptor" in low or "golden dome" in low or re.search(r"\bsbi\b", low):
        return "sbi"
    if "airborne moving target" in low or "space-based amti" in low or "space based amti" in low or re.search(r"\bamti\b", low):
        return "amti"
    if "meadowlands" in low or "counter communications" in low or "electromagnetic warfare" in low or "jammer" in low:
        return "ew"
    return "space_defense"


def korean_title(item, cat):
    if cat == "space_control":
        return "미 우주군 궤도상 우주통제 무기 관련 신규 공개"
    if cat == "sbi":
        return "Golden Dome 우주기반 요격체 개발 단계 변화"
    if cat == "amti":
        return "미 우주군 우주기반 공중이동표적지시 사업 변화"
    if cat == "ew":
        return "미 우주군 대우주 전자전 능력 변화"
    raw = monitor.translate_piece(item.get("title", ""))
    return monitor.compact_korean(raw or item.get("title", ""), 105)


def build_message(item):
    item = enrich(item)
    text = f"{item.get('title','')} {item.get('summary','')} {item.get('article_text','')}"
    cat = category(text)
    title = korean_title(item, cat)
    rate, fx_date = usd_krw()
    money = money_mentions(text)
    cos = companies(text)

    bullets = [f"단계: {item.get('stage','보도')}"]
    if cat == "space_control":
        bullets.append("달라진 점: 궤도상 우주통제 무기의 존재·유형·수량·제작사·운용단계 가운데 새로 공개된 내용을 기존 기준선과 비교")
        bullets.append("투자 관점: 실제 제작사·탑재체·수량이 공개될 때 위성버스·추진·전자전·센서·지상통제 공급망의 직접성이 결정")
        bullets.append("다음 확인: 무기 종류(운동성/비운동성)·대수·발사시점·제작사·운용개념·추가 배치")
    elif cat == "sbi":
        bullets.append("달라진 점: Golden Dome 우주기반 요격체의 Gate 진행·시제품·비행시험·궤도실증·공급사 축소 여부 확인")
        bullets.append("투자 관점: 현재 경쟁개발보다 Gate 2→3 통과와 궤도 실증 성공이 실제 양산계약 가능성을 크게 높임")
        bullets.append("다음 확인: Gate 2/3 선정사·시험일정·요격 성공률·대당 단가·초기운용능력·양산계약")
    elif cat == "amti":
        bullets.append("달라진 점: 우주기반 공중이동표적지시 위성의 발사·위성 수·임무성능·추가 발주 여부 확인")
        bullets.append("투자 관점: 첫 발사 성공 후 위성 수량과 후속 임무주문이 공개돼야 SpaceX 및 센서·발사 공급망의 매출 민감도를 계산 가능")
        bullets.append("다음 확인: 첫 발사일·위성 수·초도운용·추가 태스크오더·센서 공급사·후속 발사")
    else:
        bullets.append("달라진 점: 미국의 우주통제·전자전·우주미사일방어 체계에서 새로운 배치·계약·시험·운용 변화 확인")
        bullets.append("다음 확인: 공급사·계약금액·수량·시험·운용승인·반복 발주")

    if money:
        money_parts = []
        for raw, usd in money:
            money_parts.append(f"{raw}({krw_text(usd, rate)})")
        bullets.insert(2, "금액: " + " / ".join(money_parts))
    if cos:
        bullets.insert(min(4, len(bullets)), "관련 기업: " + "·".join(cos))
    bullets = bullets[:6]

    url = html.escape(item["url"], quote=True)
    source = html.escape(item.get("source") or "공식자료")
    published = item.get("published_date") or ""
    date_line = f"공개일: <b>{html.escape(published)}</b>\n" if published else ""
    checked = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    bullet_text = "\n".join(f"• {html.escape(b)}" for b in bullets)
    fx_line = f"\n환산 기준: 1달러={rate:,.2f}원 · ECB {fx_date}" if money else ""
    return (
        "🚨 <b>미국 우주통제·골든돔 중요 변화</b>\n\n"
        f"<b>{html.escape(title)}</b>\n"
        f"출처: <a href=\"{url}\">{source}</a>\n"
        f"{date_line}확인시각: {checked}\n\n"
        f"{bullet_text}{html.escape(fx_line)}\n\n"
        f"<a href=\"{url}\"><b>원문</b></a>"
    )


def bootstrap_message():
    rate, fx_date = usd_krw()
    official_url = "https://www.usafe.af.mil/News/Article-Display/Article/4600887/meink-offers-blueprint-for-modernizing-air-space-forces-affordably/"
    checked = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    amti_krw = krw_text(4.16e9, rate)
    sbi_krw = krw_text(3.2e9, rate)
    return (
        "🚨 <b>미국 우주통제·골든돔 중요 변화</b>\n\n"
        "<b>미 공군장관, 미 우주군의 궤도상 우주통제 무기 실전배치 첫 공개 인정</b>\n"
        f"출처: <a href=\"{official_url}\">미 공군 공식 발표</a>\n"
        "공식 발언일: <b>2026-09-14</b>\n"
        f"확인시각: {checked}\n\n"
        "• 단계: 미 공군장관 공식 발언\n"
        "• 달라진 점: 미국 정부가 우주통제 무기의 궤도상 실전배치를 처음으로 공개 인정\n"
        "• 미공개: 무기 유형(운동성/비운동성)·대수·발사시점·제작사\n"
        f"• 동시 진행: Golden Dome 우주기반 요격체는 초기계약→비행준비 하드웨어 단계, SBI 경쟁개발은 최대 32억달러({sbi_krw})\n"
        f"• Space-Based AMTI: SpaceX 41억6천만달러({amti_krw}) 계약, 첫 위성 발사는 2026년 9월 예정\n"
        "• 다음 확인: 궤도상 무기 제작사·수량·무기유형, SBI Gate 2/3, AMTI 첫 발사 성공·초도 위성 수\n"
        f"환산 기준: 1달러={rate:,.2f}원 · ECB {fx_date}\n\n"
        f"<a href=\"{official_url}\"><b>원문</b></a>"
    )


def send_telegram(text: str):
    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    if not token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN 또는 TELEGRAM_CHAT_ID가 없습니다.")
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={
            "chat_id": chat_id, "text": text[:4096], "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
        },
        timeout=TIMEOUT,
    )
    r.raise_for_status()


def load_state():
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def semantic_key(item):
    title = re.sub(r"[^a-z0-9]+", " ", item.get("title", "").lower()).strip()
    cat = category(f"{item.get('title','')} {item.get('summary','')}")
    cos = ",".join(companies(f"{item.get('title','')} {item.get('summary','')}"))
    day = (item.get("published_date") or "")[:10]
    return f"{day}|{cat}|{cos}|{' '.join(title.split()[:14])}"


def main():
    old = load_state()
    new = dict(old)
    new.setdefault("sources", {})
    alerts = []

    source_groups = [(OFFICIAL_SOURCES, "공식"), (REPORT_SOURCES, "전문매체 보도")]
    for group, stage in source_groups:
        for source in group:
            name = source["name"]
            try:
                current = fetch_html_source(source, stage)
                print(f"[SPACE OK] {name}: {len(current)}개")
            except Exception as e:
                print(f"[SPACE WARN] {name}: {e}")
                continue
            prev = set((old.get("sources") or {}).get(name, []))
            cur = set(current.keys())
            if old.get("initialized") and name in (old.get("sources") or {}):
                for url in sorted(cur - prev):
                    alerts.append(current[url])
            else:
                print(f"[SPACE BASELINE] {name}")
            new["sources"][name] = sorted(prev | cur)[-800:]

    for source in NEWS_RSS:
        name = source["name"] + "::" + hashlib.sha1(source["url"].encode()).hexdigest()[:8]
        try:
            current = fetch_rss(source)
            print(f"[SPACE OK] {name}: {len(current)}개")
        except Exception as e:
            print(f"[SPACE WARN] {name}: {e}")
            continue
        prev = set((old.get("sources") or {}).get(name, []))
        cur = set(current.keys())
        if old.get("initialized") and name in (old.get("sources") or {}):
            for url in sorted(cur - prev):
                alerts.append(current[url])
        else:
            print(f"[SPACE BASELINE] {name}")
        new["sources"][name] = sorted(prev | cur)[-1200:]

    if not old.get("initialized"):
        send_telegram(bootstrap_message())
        new["initialized"] = True
        new["bootstrap_sent"] = True
        save_state(new)
        print("[SPACE BOOTSTRAP SENT] 2026-09-14 on-orbit space control weapons")
        return

    sent_keys = set(old.get("semantic_keys", []))
    candidates = []
    for item in alerts:
        key = semantic_key(item)
        if key in sent_keys:
            continue
        sent_keys.add(key)
        candidates.append(item)

    sent = 0
    for item in candidates[:8]:
        send_telegram(build_message(item))
        sent += 1
        print(f"[SPACE SENT] {item.get('source')} - {item.get('title')}")

    new["semantic_keys"] = sorted(sent_keys)[-1500:]
    new["initialized"] = True
    new["bootstrap_sent"] = bool(old.get("bootstrap_sent", True))
    save_state(new)
    print(f"[SPACE DONE] 신규 알림 {sent}건")


if __name__ == "__main__":
    main()
