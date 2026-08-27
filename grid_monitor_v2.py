import html
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

import monitor

STATE_FILE = Path("grid_state.json")
TIMEOUT = 30
HEADERS = monitor.HEADERS
ECB_FX_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
_FX_CACHE = None

SOURCES = [
    {"name": "백악관 대통령 조치", "url": "https://www.whitehouse.gov/presidential-actions/"},
    {"name": "백악관 팩트시트", "url": "https://www.whitehouse.gov/fact-sheets/"},
    {"name": "미 에너지부 뉴스", "url": "https://www.energy.gov/articles"},
    {"name": "미 에너지부 전력국 뉴스", "url": "https://www.energy.gov/oe/listings/news-highlights"},
    {"name": "미 연방관보 DOE", "url": "https://www.federalregister.gov/agencies/energy-department"},
    {"name": "미 연방관보 FAR 검색", "url": "https://www.federalregister.gov/documents/search?conditions%5Bterm%5D=energy+infrastructure+federal+acquisition+regulation"},
]

GRID_TERMS = [
    "bulk-power system", "bulk power system", "electric grid", "power grid",
    "grid infrastructure", "grid equipment", "critical electric infrastructure",
    "substation transformer", "large power transformer", "transformer",
    "high-voltage circuit breaker", "high voltage circuit breaker", "circuit breaker",
    "protective relay", "protective relaying", "substation", "voltage regulator",
    "transmission equipment", "transmission line", "conductor",
    "grid-connected inverter", "grid connected inverter", "inverter",
    "battery energy storage system", "bess", "uninterruptible power supply", "ups",
    "industrial control system", "programmable logic controller", "plc",
    "intelligent electronic device", "ied", "distributed control system", "dcs",
    "safety instrumented system", "sis", "remote terminal unit", "rtu",
    "remote access", "software", "firmware", "digital service", "maintenance service",
    "electrical core steel", "capacitor bank", "automatic circuit recloser",
]

POLICY_TERMS = [
    "covered foreign entity", "foreign-produced", "foreign produced", "foreign adversary",
    "national emergency", "executive order 14420", "eo 14420",
    "pre-qualified", "prequalified", "licensing", "license transactions",
    "federal acquisition regulation", "far council", "united states-manufactured",
    "buy american", "domestic content", "domestic manufacturing",
    "defense production act", "section 303", "purchase commitment", "financial support",
    "replace", "remove", "isolate", "inventory", "disconnect", "mitigation",
    "120 days", "180 days", "90 days", "procurement", "supply chain", "cybersecurity",
]

EXCLUDE_TERMS = [
    "hot weather", "heat wave", "emergency order keeps", "dispatch specified units",
    "eddystone generating station", "resource adequacy", "combined notice of filings",
]


def normalize_url(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}{p.path.rstrip('/')}"


def relevant(title: str, url: str) -> bool:
    hay = f"{title} {urlparse(url).path}".lower()
    if any(x in hay for x in EXCLUDE_TERMS):
        return False
    has_grid = any(x in hay for x in GRID_TERMS)
    has_policy = any(x in hay for x in POLICY_TERMS)
    strong = any(x in hay for x in [
        "bulk-power system", "covered foreign entity", "executive order 14420",
        "pre-qualified", "federal acquisition regulation", "defense production act",
        "large power transformer", "united states-manufactured",
    ])
    return strong or (has_grid and has_policy)


def get_page(url: str) -> str:
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        return r.text
    except Exception:
        r = requests.get(f"https://r.jina.ai/{url}", headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        return r.text


def fetch_links(source):
    raw = get_page(source["url"])
    host = urlparse(source["url"]).netloc.lower().removeprefix("www.")
    items = {}

    if raw.lstrip().startswith("Title:") or "Markdown Content:" in raw[:1500]:
        pairs = re.findall(r"\[([^\]\n]{8,350})\]\((https?://[^)\s]+)\)", raw)
        for title, href in pairs:
            title = " ".join(title.split())
            url = normalize_url(href)
            cand = urlparse(url).netloc.lower().removeprefix("www.")
            if not cand.endswith(host):
                continue
            if relevant(title, url):
                items[url] = {"source": source["name"], "title": title, "url": url}
        return items

    soup = BeautifulSoup(raw, "html.parser")
    for a in soup.find_all("a", href=True):
        title = " ".join(a.get_text(" ", strip=True).split())
        if len(title) < 8:
            continue
        url = normalize_url(urljoin(source["url"], a["href"]))
        cand = urlparse(url).netloc.lower().removeprefix("www.")
        if not cand.endswith(host):
            continue
        if relevant(title, url):
            items[url] = {"source": source["name"], "title": title, "url": url}
    return items


def load_state():
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def clean_blocks(url: str):
    blocks = monitor.extract_article_blocks(url)
    out = []
    for block in blocks:
        low = block.lower()
        if any(x in low for x in ["http://", "https://", "site search", "posted time", "published time", "title:"]):
            continue
        out.append(block)
    return out


def is_bulk_power_eo(text: str) -> bool:
    low = text.lower()
    return ("bulk-power system" in low or "bulk power system" in low) and "covered foreign entity" in low and "120 days" in low


def is_dpa_grid_policy(text: str) -> bool:
    low = text.lower()
    return "defense production act" in low and any(x in low for x in ["transformer", "grid infrastructure", "transmission", "electrical core steel"])


def get_fx():
    global _FX_CACHE
    if _FX_CACHE is not None:
        return _FX_CACHE
    try:
        r = requests.get(ECB_FX_URL, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        root = ET.fromstring(r.content)
        date = ""
        rates = {"EUR": 1.0}
        for node in root.iter():
            if "time" in node.attrib:
                date = node.attrib["time"]
            if node.attrib.get("currency") and node.attrib.get("rate"):
                rates[node.attrib["currency"]] = float(node.attrib["rate"])
        krw = rates.get("KRW")
        usd = rates.get("USD")
        if not krw or not usd:
            raise ValueError("ECB KRW/USD rate unavailable")
        _FX_CACHE = {"date": date, "USD": krw / usd}
    except Exception as e:
        print(f"[GRID WARN] 환율 조회 실패: {e}")
        _FX_CACHE = {"date": "", "USD": None}
    return _FX_CACHE


def format_krw(value: float) -> str:
    eok = int(round(value / 100_000_000))
    if eok >= 10000:
        jo, rem = divmod(eok, 10000)
        return f"{jo}조{rem:,}억원" if rem else f"{jo}조원"
    return f"{eok:,}억원"


def add_usd_krw(text_ko: str, source_en: str) -> str:
    fx = get_fx()
    rate = fx.get("USD")
    if not rate:
        return text_ko
    m = re.search(r"\$\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*(billion|million|bn|mn|b|m)\b", source_en, flags=re.I)
    if not m:
        return text_ko
    n = float(m.group(1).replace(",", ""))
    unit = m.group(2).lower()
    mult = 1_000_000_000 if unit in {"billion", "bn", "b"} else 1_000_000
    return f"{text_ko} (원화 약 {format_krw(n * mult * rate)})"


def structured_bullets(blocks):
    full = " ".join(blocks)
    low = full.lower()
    bullets = []

    if is_bulk_power_eo(full):
        bullets.append("정확한 규제: 모든 외국산을 전면 금지하는 것이 아니라 Covered Foreign Entity가 관여한 외국산 BPS 장비 중 DOE가 사이버·원격조작·공급중단 등 안보 위험을 판정한 신규 거래를 제한")
        bullets.append("범위 변화: 69kV 이상 송전망을 포함한 BPS가 대상이고 지역 배전은 제외. 변압기·고압차단기·보호계전뿐 아니라 계통연계 인버터·BESS·UPS·RTU/PLC/IED·DCS/SIS와 소프트웨어·펌웨어·원격접속·유지보수까지 심사")
        bullets.append("투자 관점: 외국산은 미국에서 제조·생산·조립되지 않은 물품으로 정의. 사전적격 공급업체·거래 허가와 기설치 장비의 격리·교체 권한 때문에 단순 중국산 대체보다 미국 현지생산·조달자격 프리미엄과 강제 교체수요가 핵심")
        bullets.append("한국 기업: 효성중공업 Memphis·HD현대일렉트릭 Alabama는 초고압 변압기 현지생산 직접성이 높음. LS ELECTRIC Utah/Texas는 현지화 수혜지만 지역 배전용 저·중압 제품은 이번 EO 직접 수혜와 분리")
        bullets.append("다음 알림: 120일 DOE 시행규정·Covered Foreign Entity·사전적격 공급업체·허가/완화조건·기설치 교체명령, 180일 FAR 권고와 이후 FAR Council 절차, DPA 실제 구매약정·금융지원 금액을 최우선 추적")
        return bullets

    if is_dpa_grid_policy(full):
        bullets.append("정책 핵심: DPA 제303조로 변압기·송전선/도체·변전소·고압차단기·전력제어전자·보호계전·커패시터뱅크·전기강판 및 관련 원재료·제조장비의 미국 내 생산능력 확대를 지원")
        bullets.append("실적 연결: 실제 구매·구매약정·금융지원 금액이 발표되면 정책 기대에서 설비투자·수주 가시성 단계로 상향")
        bullets.append("한국 기업: 미국 현지 생산기반이 있는 효성중공업·HD현대일렉트릭·LS ELECTRIC의 전략적 가치 상승. 실제 수혜는 증설 완료 → 가동률 → 신규수주 → 평균판매단가 → 매출·영업이익률 순으로 확인")
        return bullets

    if any(x in low for x in ["pre-qualified", "prequalified", "covered foreign entity", "license transactions", "licensing", "mitigation"]):
        bullets.append("달라진 점: Covered Foreign Entity 지정·사전적격 장비/공급업체·거래 허가·완화조건이 구체화되어 실제 미국 전력망 조달 가능 업체를 가르는 기준이 바뀜")
    if any(x in low for x in ["replace", "remove", "isolate", "disconnect", "inventory"]):
        bullets.append("투자 관점: 기존 설치 장비의 재고조사·격리·교체·철거가 구체화되면 신규 건설 외에 강제 교체수요가 추가돼 변압기·차단기·보호계전·제어시스템 발주가 늘 수 있음")
    if any(x in low for x in ["federal acquisition regulation", "united states-manufactured", "buy american", "domestic content"]):
        bullets.append("미국산 기준: 현행 일반 Buy American 제조품 기준은 2024~2028년 국내부품 원가 65% 초과, 2029년부터 75%이며 EO 14420 전용 에너지 인프라 FAR 규정이 이보다 강화·변형되는지 비교")
    if any(x in low for x in ["purchase commitment", "financial support", "section 303"]):
        bullets.append("실적 연결: DPA의 구매약정·금융지원이 실제 금액으로 확정되면 현지 생산능력 확대와 수주잔고로 연결되는지 확인")
    return bullets[:4]


def fallback_bullets(blocks, limit=3):
    scored = []
    for idx, block in enumerate(blocks):
        low = block.lower()
        if not any(x in low for x in GRID_TERMS):
            continue
        score = sum(2 for x in POLICY_TERMS if x in low) + sum(1 for x in GRID_TERMS if x in low)
        if re.search(r"\b\d+(?:\.\d+)?\s*(?:days|kV|GW|MW|%)\b", block, flags=re.I):
            score += 3
        scored.append((score, idx, block))
    scored.sort(key=lambda x: (-x[0], x[1]))

    out = []
    for score, _, block in scored:
        if score <= 0:
            continue
        ko = monitor.translate_piece(block)
        ko = monitor.compact_korean(ko, 135)
        if ko and monitor.has_korean(ko):
            ko = add_usd_krw(ko, block)
            if ko not in out:
                out.append(ko)
        if len(out) >= limit:
            break
    return out


def title_korean(item, blocks):
    full = " ".join(blocks)
    if is_bulk_power_eo(full):
        return "트럼프 EO 14420, 고위험 외국산 전력망 장비 거래 제한·미국 현지생산 프리미엄 강화"
    if is_dpa_grid_policy(full):
        return "미국, DPA로 전력망 핵심 장비·공급망 미국 내 생산 확대"
    ko = monitor.translate_piece(item["title"])
    ko = monitor.compact_korean(ko, 100)
    return ko if monitor.has_korean(ko) else "미국 전력망·전력기기 정책 새 공식 발표"


def build_message(item):
    blocks = clean_blocks(item["url"])
    title = title_korean(item, blocks)
    bullets = structured_bullets(blocks)
    if not bullets:
        bullets = fallback_bullets(blocks, 3)
    if not bullets:
        bullets = ["미국 전력망·전력기기 조달·보안 정책의 새 공식자료가 확인되었습니다."]

    safe_url = html.escape(item["url"], quote=True)
    safe_source = html.escape(item["source"])
    bullet_text = "\n".join(f"• {html.escape(x)}" for x in bullets[:5])

    fx = get_fx()
    fx_line = ""
    if fx.get("USD") and re.search(r"\$\s*\d", " ".join(blocks)):
        fx_line = f"\n\n환산 기준: 1달러={fx['USD']:,.0f}원 · ECB {fx['date']}"

    return (
        "🚨 <b>미국 전력망·전력기기 정책 중요 변화</b>\n\n"
        f"<b>{html.escape(title)}</b>\n"
        f"출처: <a href=\"{safe_url}\">{safe_source}</a>\n\n"
        f"{bullet_text}"
        f"{html.escape(fx_line)}\n\n"
        f"🔎 <a href=\"{safe_url}\"><b>원문</b></a>"
    )


def main():
    old = load_state()
    new = dict(old)
    alerts = []

    for source in SOURCES:
        name = source["name"]
        try:
            current = fetch_links(source)
        except Exception as e:
            print(f"[GRID WARN] {name} 조회 실패: {e}")
            continue

        previous = set(old.get(name, []))
        urls = set(current.keys())
        if name in old:
            for url in sorted(urls - previous):
                alerts.append(current[url])
        else:
            print(f"[GRID BASELINE] {name}: 첫 수집이라 기존 자료 알림 생략")

        new[name] = sorted(previous | urls)[-500:]
        print(f"[GRID OK] {name}: 관련 링크 {len(urls)}개")

    save_state(new)

    if alerts:
        for item in alerts[:8]:
            monitor.send_telegram(build_message(item))
            print(f"[GRID SENT] {item['source']} - {item['title']}")
    else:
        print("새로운 전력망·전력기기 정책 자료 없음")


if __name__ == "__main__":
    main()
