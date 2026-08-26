import html
import json
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

import monitor

STATE_FILE = Path("grid_state.json")
TIMEOUT = 30
HEADERS = monitor.HEADERS

SOURCES = [
    {
        "name": "백악관 대통령 조치",
        "url": "https://www.whitehouse.gov/presidential-actions/",
    },
    {
        "name": "백악관 팩트시트",
        "url": "https://www.whitehouse.gov/fact-sheets/",
    },
    {
        "name": "미 에너지부 뉴스",
        "url": "https://www.energy.gov/articles",
    },
    {
        "name": "미 에너지부 전력국 뉴스",
        "url": "https://www.energy.gov/oe/listings/news-highlights",
    },
]

GRID_TERMS = [
    "bulk-power system", "bulk power system", "electric grid", "power grid",
    "grid infrastructure", "grid equipment", "critical electric infrastructure",
    "substation transformer", "large power transformer", "transformer",
    "high-voltage circuit breaker", "high voltage circuit breaker", "circuit breaker",
    "protective relay", "protective relaying", "substation",
    "transmission equipment", "transmission line", "conductor",
    "grid-connected inverter", "grid connected inverter", "inverter",
    "battery energy storage system", "bess", "uninterruptible power supply", "ups",
    "industrial control system", "programmable logic controller", "plc",
    "intelligent electronic device", "ied", "distributed control system",
    "safety instrumented system", "remote terminal unit", "remote access",
    "software", "firmware", "digital service", "maintenance service",
    "grid supply chain", "electrical core steel", "capacitor bank",
]

POLICY_TERMS = [
    "foreign-produced", "foreign produced", "covered foreign entity", "foreign adversary",
    "national emergency", "executive order", "presidential determination",
    "defense production act", "section 303", "prohibit", "prohibition",
    "procurement", "pre-qualified", "prequalified", "federal acquisition regulation",
    "far council", "license transactions", "licensing", "supply chain",
    "cybersecurity", "cyber security", "national security",
    "domestic manufacturing", "united states-manufactured", "onshore production",
    "import", "sanction", "arms embargo", "replace", "remove", "isolate",
    "inventory", "monitor", "secure", "digital backdoor", "120 days", "180 days",
]

EXCLUDE_TERMS = [
    "hot weather", "heat wave", "emergency order keeps", "dispatch specified units",
    "wagner unit", "eddystone generating station", "resource adequacy",
]


def normalize_url(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}{p.path.rstrip('/')}"


def relevant(title: str, url: str) -> bool:
    hay = f"{title} {urlparse(url).path}".lower()
    if any(term in hay for term in EXCLUDE_TERMS):
        return False
    has_grid = any(term in hay for term in GRID_TERMS)
    has_policy = any(term in hay for term in POLICY_TERMS)
    strong = any(term in hay for term in [
        "bulk-power system", "bulk power system", "grid supply chain",
        "critical electric infrastructure", "foreign-produced", "covered foreign entity",
        "large power transformer", "defense production act", "section 303",
        "federal acquisition regulation", "united states-manufactured",
    ])
    return strong or (has_grid and has_policy)


def get_page(url: str) -> str:
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        return r.text
    except Exception:
        proxy = requests.get(f"https://r.jina.ai/{url}", headers=HEADERS, timeout=TIMEOUT)
        proxy.raise_for_status()
        return proxy.text


def fetch_links(source):
    raw = get_page(source["url"])
    host = urlparse(source["url"]).netloc.lower().removeprefix("www.")
    items = {}

    if raw.lstrip().startswith("Title:") or "Markdown Content:" in raw[:1200]:
        for title, href in re.findall(r"\[([^\]\n]{8,350})\]\((https?://[^)\s]+)\)", raw):
            title = " ".join(title.split())
            url = normalize_url(href)
            cand_host = urlparse(url).netloc.lower().removeprefix("www.")
            if not cand_host.endswith(host):
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
        cand_host = urlparse(url).netloc.lower().removeprefix("www.")
        if not cand_host.endswith(host):
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
        if any(x in low for x in ["http://", "https://", "site search", "posted time", "published time"]):
            continue
        out.append(block)
    return out


def is_bulk_power_eo(text: str) -> bool:
    low = text.lower()
    return (
        ("bulk-power system" in low or "bulk power system" in low)
        and "covered foreign entity" in low
        and "120 days" in low
    )


def is_dpa_grid_policy(text: str) -> bool:
    low = text.lower()
    return (
        "defense production act" in low
        and any(t in low for t in ["transformer", "grid infrastructure", "transmission", "electrical core steel"])
    )


def structured_grid_bullets(blocks):
    full = " ".join(blocks)
    low = full.lower()
    bullets = []

    if is_bulk_power_eo(full):
        bullets.append(
            "정책 핵심: Covered Foreign Entity가 설계·개발·제조·공급한 외국산 대규모 전력망 장비 중 안보 위험이 있다고 판정된 거래의 신규 구매·수입·이전·설치를 제한하며, 핵심 부품·소프트웨어·펌웨어·디지털 서비스·유지보수·원격접속 기능까지 포함"
        )
        bullets.append(
            "달라진 점: 2020년 EO 13920의 장비 정의 자체도 넓었지만 실제 2020년 12월 중국산 금지명령은 핵심 방위시설 공급 유틸리티와 69kV 이상 변압기·GSU·차단기·무효전력설비·관련 소프트웨어/펌웨어로 좁았다. 2026년 EO는 계통연계 인버터·BESS·UPS·소형 발전기와 디지털·유지보수·원격접속 위험까지 명시하고, 미국 관할 거래 전반에 적용 가능한 체계로 재구축"
        )
        bullets.append(
            "시간표: 미 에너지부가 120일 안에 Covered Foreign Entity·고위험 장비·허가 절차 등 시행규정을 마련하고, 180일 안에 미국산 에너지 인프라 조달 우선의 연방조달규정(FAR) 개정 권고안을 제출. 기존 설치 장비도 식별·재고화·격리·감시·교체 대상이 될 수 있음"
        )
        bullets.append(
            "투자 관점: 1차 민감 품목은 대형 변압기 → 고압 차단기 → 보호계전기 → 계통연계 인버터 → BESS → UPS → 산업제어시스템. 2026년 4월 DPA가 이미 변압기·송전선/도체·변전소·고압차단기·전력제어전자·보호계전·커패시터뱅크·전기강판의 미국 내 생산 확대를 지정해 이번 보안규제와 현지 생산 확대가 연결됨"
        )
        bullets.append(
            "한국 기업 해석: 한국산 자체가 자동 금지되는 것은 아니며 Covered Foreign Entity·위험 판정이 핵심. 다만 연방조달의 미국산 우선이 강화되면 미국 현지 생산기반이 있는 효성중공업(멤피스 변압기·미국 개폐기), HD현대일렉트릭(앨라배마 변압기), LS ELECTRIC(텍사스 변압기·개폐기)이 상대적으로 유리"
        )
        return bullets

    if is_dpa_grid_policy(full):
        bullets.append(
            "정책 핵심: 미국이 Defense Production Act 제303조를 활용해 변압기·송전선/도체·변전소·고압차단기·전력제어전자·보호계전·커패시터뱅크·전기강판과 관련 원재료·제조장비의 미국 내 생산능력 확대를 국가안보 자원으로 지정"
        )
        bullets.append(
            "투자 관점: 단순 전력수요 증가보다 장기 납기·수입 의존도가 높은 대형 변압기와 고압 차단기가 가장 직접적이며, 보호계전·전력제어전자·전기강판·도체로 수혜 범위가 확장"
        )
        bullets.append(
            "한국 기업 해석: 미국 현지 생산이 있는 효성중공업·HD현대일렉트릭·LS ELECTRIC은 조달 현지화와 공급망 안보 강화가 동시에 진행될수록 상대적 우위가 커질 수 있으나, 실제 수혜는 후속 DPA 자금·구매약정·FAR 조달기준과 수주 공시로 확인해야 함"
        )
        return bullets

    # 후속 규정에서 실제 투자판단을 바꾸는 변화만 별도 강조
    if any(term in low for term in ["covered foreign entity", "pre-qualified", "prequalified", "license transactions", "licensing"]):
        bullets.append("달라진 점: 특정 국가·기업의 Covered Foreign Entity 지정, 사전적격 공급업체, 금지거래 허가 절차 등 실제 조달 가능 업체를 가르는 세부기준 변화가 확인됨")
    if any(term in low for term in ["replace", "remove", "isolate", "inventory"]):
        bullets.append("투자 관점: 기존 설치 장비의 교체·격리·재고조사가 구체화되면 신규 건설 수요뿐 아니라 교체수요가 추가되어 변압기·차단기·보호계전·제어시스템의 실물 발주로 연결될 가능성이 커짐")
    if any(term in low for term in ["federal acquisition regulation", "united states-manufactured", "domestic manufacturing"]):
        bullets.append("투자 관점: 연방조달의 미국산 우선 기준이 구체화될수록 미국 현지 생산기반 보유 업체와 단순 수입업체 간 수주 경쟁력이 갈릴 수 있음")
    return bullets[:5]


def fallback_bullets(blocks, limit=3):
    candidates = []
    for idx, block in enumerate(blocks):
        low = block.lower()
        if not any(term in low for term in GRID_TERMS):
            continue
        score = sum(2 for term in POLICY_TERMS if term in low)
        score += sum(2 for term in GRID_TERMS if term in low)
        if re.search(r"\b\d+(?:\.\d+)?\s*(?:days|kV|GW|MW|%)\b", block, flags=re.I):
            score += 3
        if idx < 20:
            score += 1
        candidates.append((score, idx, block))

    candidates.sort(key=lambda x: (-x[0], x[1]))
    result = []
    for _, _, block in candidates:
        translated = monitor.translate_piece(block)
        translated = monitor.compact_korean(translated, 140)
        if translated and monitor.has_korean(translated) and translated not in result:
            result.append(translated)
        if len(result) >= limit:
            break
    return result


def title_korean(item, blocks):
    full = " ".join(blocks)
    if is_bulk_power_eo(full):
        return "트럼프, 외국산 핵심 전력망 장비·소프트웨어 보안 규제 강화"
    if is_dpa_grid_policy(full):
        return "트럼프, DPA로 미국 전력망 핵심 장비·공급망 생산능력 확대"
    title = monitor.translate_piece(item["title"])
    title = monitor.compact_korean(title, 95)
    if not monitor.has_korean(title):
        return "미국 전력망·전력기기 정책 새 공식 발표"
    return title


def build_message(item):
    blocks = clean_blocks(item["url"])
    title = title_korean(item, blocks)
    bullets = structured_grid_bullets(blocks)
    if not bullets:
        bullets = fallback_bullets(blocks, 3)
    if not bullets:
        bullets = ["미국 전력망·전력기기 조달·보안 정책의 새 공식자료가 확인되었습니다."]

    safe_url = html.escape(item["url"], quote=True)
    safe_source = html.escape(item["source"])
    bullet_text = "\n".join(f"• {html.escape(x)}" for x in bullets[:5])

    return (
        "🚨 <b>미국 전력망·전력기기 정책 중요 변화</b>\n\n"
        f"<b>{html.escape(title)}</b>\n"
        f"출처: <a href=\"{safe_url}\">{safe_source}</a>\n\n"
        f"{bullet_text}\n\n"
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
            print(f"[WARN] {name} 조회 실패: {e}")
            continue

        previous = set(old.get(name, []))
        urls = set(current.keys())
        if name in old:
            for url in sorted(urls - previous):
                alerts.append(current[url])
        else:
            print(f"[GRID BASELINE] {name}: 첫 수집이라 과거 자료 알림 생략")

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
