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
            "정책 핵심: 69kV 이상 Bulk-Power System에서 Covered Foreign Entity 관련 외국산 장비·핵심부품·소프트웨어·펌웨어·원격접속 중 안보 위험 거래의 신규 구매·수입·이전·설치를 제한"
        )
        bullets.append(
            "달라진 점: 2020년 실제 중국산 금지명령은 핵심 방위시설과 변압기·GSU·차단기·무효전력설비 중심이었지만, 2026년 EO는 계통연계 인버터·BESS·UPS·소형 발전기와 디지털·유지보수·원격접속 위험까지 명시하고 기존 설치장비의 격리·교체도 가능"
        )
        bullets.append(
            "시장 해석: 유안타 제시 기준 미국 10MVA 초과 액체식 대형변압기 수입에서 중국 약 3%, 한국 약 20% 내외 → 중국 물량 직접 대체보다 미국 현지생산·조달자격·진입장벽 프리미엄이 더 중요. 해당 점유율은 증권사 제시치로 공식 통관자료 재검증 대상"
        )
        bullets.append(
            "한국 기업: 효성중공업은 Memphis의 미국 유일 765kV 변압기 생산기지와 2026년 미국 7,870억원 수주, HD현대일렉트릭은 Alabama 제2공장으로 생산능력 +50%·연간 매출능력 약 +2,000억원, LS ELECTRIC은 Utah 개폐기·Texas 전력시스템 현지화를 확대 중. 단 LS의 배전제품은 69kV EO 직접범위와 분리"
        )
        bullets.append(
            "다음 알림: DOE 120일 시행규정의 Covered Foreign Entity·고위험 장비·허가절차, 사전적격 공급업체, 기존 장비 교체명령, 180일 FAR 미국산 우선조달, DPA 실제 자금·구매약정, 한국·중국 대형변압기 수입점유율 변화"
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
            "한국 기업 해석: 효성중공업 Memphis·HD현대일렉트릭 Alabama·LS ELECTRIC Utah/Texas 등 미국 현지 생산기반의 전략적 가치가 상승. 실제 수혜는 DPA 자금·구매약정·FAR 조달기준·신규수주로 확인"
        )
        bullets.append(
            "다음 확인: 현지 생산능력 증설 완료 → 가동률 → 신규수주 → 평균판매단가 → 매출·영업이익률로 이어지는지 확인하고, 미국산 인정기준이 현지 조립뿐 아니라 핵심부품 원산지까지 요구하는지 점검"
        )
        return bullets

    # 후속 규정에서 실제 투자판단을 바꾸는 변화만 별도 강조
    if any(term in low for term in ["covered foreign entity", "pre-qualified", "prequalified", "license transactions", "licensing"]):
        bullets.append("달라진 점: 특정 국가·기업의 Covered Foreign Entity 지정, 사전적격 공급업체, 금지거래 허가 절차 등 실제 조달 가능 업체를 가르는 세부기준 변화가 확인됨")
    if any(term in low for term in ["replace", "remove", "isolate", "inventory"]):
        bullets.append("투자 관점: 기존 설치 장비의 교체·격리·재고조사가 구체화되면 신규 건설뿐 아니라 강제 교체수요가 추가되어 변압기·차단기·보호계전·제어시스템의 실물 발주로 연결될 가능성이 커짐")
    if any(term in low for term in ["federal acquisition regulation", "united states-manufactured", "domestic manufacturing"]):
        bullets.append("투자 관점: 연방조달의 미국산 우선 기준이 구체화될수록 미국 현지 생산기반 보유 업체와 단순 수입업체 간 수주 경쟁력이 갈릴 수 있음")
    if any(term in low for term in ["purchase commitment", "financial support", "section 303"]):
        bullets.append("실적 연결: DPA 구매약정·금융지원이 실제 금액으로 확정되면 정책 기대에서 설비투자·수주 가시성 단계로 한 단계 올라간 것으로 판단")
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