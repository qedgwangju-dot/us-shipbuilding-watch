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
    "high-voltage circuit breaker", "circuit breaker", "protective relay",
    "substation", "transmission equipment", "transmission line", "conductor",
    "grid-connected inverter", "inverter", "battery energy storage system",
    "uninterruptible power supply", "industrial control system", "programmable logic controller",
    "intelligent electronic device", "remote access", "firmware", "grid supply chain",
]

POLICY_TERMS = [
    "foreign-produced", "foreign produced", "covered foreign entity", "foreign adversary",
    "national emergency", "executive order", "presidential determination",
    "defense production act", "section 303", "prohibit", "prohibition",
    "procurement", "pre-qualified", "prequalified", "federal acquisition regulation",
    "supply chain", "cybersecurity", "cyber security", "national security",
    "domestic manufacturing", "onshore production", "import", "sanction", "arms embargo",
    "replace", "remove", "isolate", "monitor", "secure", "digital backdoor",
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


def structured_grid_bullets(blocks):
    full = " ".join(blocks)
    low = full.lower()
    bullets = []

    if is_bulk_power_eo(full):
        bullets.append(
            "안보 위험이 있다고 판정된 Covered Foreign Entity 관련 외국산 대규모 전력망 장비의 신규 구매·수입·이전·설치를 제한하며, 핵심 부품·소프트웨어·펌웨어·원격접속 기능까지 포함"
        )
        bullets.append(
            "미 에너지부는 기존 설치 장비도 필요하면 식별·격리·감시·보안강화·연결해제·교체·철거를 명령할 수 있으나, 전력 신뢰도·안전·대체품 확보를 고려해 단계적 이행 가능"
        )
        bullets.append(
            "미 에너지부는 120일 안에 시행규정을 마련하고, 180일 안에 미국산 에너지 인프라 조달을 우선하는 연방조달규정(FAR) 개정 권고안을 제출"
        )
        bullets.append(
            "적용 범위는 69kV 이상 대규모 전력망이며 지역 배전망은 제외; 변전소 변압기·계통연계 인버터·BESS·UPS·고압 차단기·발전터빈·산업제어시스템 등이 대상"
        )
        return bullets

    if "defense production act" in low and any(t in low for t in ["transformer", "grid infrastructure", "transmission"]):
        bullets.append("미국이 Defense Production Act를 활용해 변압기·송전·변전 등 핵심 전력망 장비의 미국 내 생산능력 확대를 지원")
        bullets.append("정책의 핵심은 단순 전력수요 증가가 아니라 미국 내 제조·조달망 확보와 수입 의존도 축소")
        return bullets

    return []


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
    bullet_text = "\n".join(f"• {html.escape(x)}" for x in bullets[:4])

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
