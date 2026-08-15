import html
import json
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

STATE_FILE = Path("state.json")
TIMEOUT = 30
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; US-Shipbuilding-Watch/2.1; +https://github.com/qedgwangju-dot/us-shipbuilding-watch)"
}

SOURCES = [
    {
        "name": "백악관 팩트시트",
        "url": "https://www.whitehouse.gov/fact-sheets/",
        "kind": "html",
    },
    {
        "name": "백악관 대통령 조치",
        "url": "https://www.whitehouse.gov/presidential-actions/",
        "kind": "html",
    },
    {
        "name": "미 해군 보도자료",
        "url": "https://www.navy.mil/DesktopModules/ArticleCS/RSS.ashx?ContentType=2&Site=1067&max=40",
        "kind": "rss",
    },
    {
        "name": "NAVSEA 뉴스",
        "url": "https://www.navsea.navy.mil/Media/News/",
        "kind": "html_proxy",
    },
    {
        "name": "미 국방부 공식 보도자료",
        "url": "https://www.war.gov/DesktopModules/ArticleCS/RSS.ashx?ContentType=9&Site=945&max=40",
        "kind": "rss",
    },
    {
        "name": "MARAD 보도자료",
        "url": "https://www.maritime.dot.gov/newsroom/press-releases",
        "kind": "html_proxy",
    },
    {
        "name": "미 해안경비대 보도자료",
        "url": "https://www.news.uscg.mil/Press-Releases/",
        "kind": "html_proxy",
    },
]

KEYWORDS = [
    "shipbuild", "shipyard", "ship construction", "vessel construction",
    "naval ship", "warship", "submarine", "aircraft carrier",
    "destroyer", "frigate", "icebreaker", "arctic security cutter",
    "polar security cutter", "waterways commerce cutter",
    "dry dock", "overhaul", "ship repair", "fleet maintenance",
    "maritime industrial base", "industrial base", "foreign shipbuilder",
    "foreign-built", "overseas construction", "parent shipyard",
    "vessel construction manager", "new shipyard", "naval shipyard",
    "hanwha", "philly shipyard", "hd hyundai", "hyundai heavy",
    "samsung heavy", "korea shipbuilding", "korean shipyard",
    "cvn-81", "emals", "advanced weapons elevator",
]

HIGH_VALUE_TERMS = [
    "foreign shipbuilder", "parent shipyard", "two ships", "2 ships",
    "shipyard", "shipbuilding", "navy", "submarine", "aircraft carrier",
    "destroyer", "frigate", "icebreaker", "contract", "award", "grant",
    "investment", "build", "repair", "overhaul", "workforce",
]


def normalize_url(url: str) -> str:
    p = urlparse(url.strip())
    return urlunparse((p.scheme, p.netloc, p.path.rstrip("/"), "", "", ""))


def relevant(text: str, url: str) -> bool:
    # 도메인명 자체(NAVSEA 등) 때문에 모든 메뉴가 관련 자료로 잡히지 않도록
    # 제목/요약 + URL 경로만 판정한다.
    path = urlparse(url).path.lower()
    hay = f"{text} {path}".lower()
    return any(k in hay for k in KEYWORDS)


def get_text(url: str, allow_proxy: bool = False):
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        return r.text, "직접"
    except Exception as direct_error:
        if not allow_proxy:
            raise direct_error
        proxy_url = f"https://r.jina.ai/{url}"
        r = requests.get(proxy_url, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        return r.text, "공식 페이지 읽기 보조"


def official_host_for(url: str) -> str:
    return urlparse(url).netloc.lower()


def host_matches(candidate: str, official_host: str) -> bool:
    host = urlparse(candidate).netloc.lower()
    base = official_host.removeprefix("www.")
    if base == "navy.mil":
        return host.endswith("navy.mil")
    if base in {"war.gov", "defense.gov"}:
        return host.endswith("war.gov") or host.endswith("defense.gov")
    return host == official_host or host == base or host.endswith("." + base)


def clean_title(text: str) -> str:
    text = BeautifulSoup(text or "", "html.parser").get_text(" ", strip=True)
    return " ".join(text.split())


def fetch_html_links(name: str, page_url: str, allow_proxy: bool = False):
    text, route = get_text(page_url, allow_proxy=allow_proxy)
    official_host = official_host_for(page_url)
    items = {}

    if route != "직접":
        for title, href in re.findall(r"\[([^\]\n]{8,350})\]\((https?://[^)\s]+)\)", text):
            title = clean_title(title)
            url = normalize_url(href)
            if host_matches(url, official_host) and relevant(title, url):
                items[url] = {
                    "source": name,
                    "title": title[:350],
                    "url": url,
                    "summary": "",
                }
        print(f"[INFO] {name}: 직접 접속 차단으로 읽기 보조 사용")
        return items

    soup = BeautifulSoup(text, "html.parser")
    for a in soup.find_all("a", href=True):
        title = clean_title(a.get_text(" ", strip=True))
        if len(title) < 8:
            continue
        url = normalize_url(urljoin(page_url, a["href"]))
        if not url.startswith("http") or not host_matches(url, official_host):
            continue
        if relevant(title, url):
            items[url] = {
                "source": name,
                "title": title[:350],
                "url": url,
                "summary": "",
            }
    return items


def node_text(node, tag_name):
    for child in list(node):
        if child.tag.split("}")[-1].lower() == tag_name.lower():
            return clean_title("".join(child.itertext()))
    return ""


def fetch_rss_links(name: str, feed_url: str):
    text, _ = get_text(feed_url, allow_proxy=False)
    root = ET.fromstring(text)
    items = {}
    entries = [n for n in root.iter() if n.tag.split("}")[-1].lower() in {"item", "entry"}]

    for entry in entries:
        title = node_text(entry, "title")
        description = node_text(entry, "description") or node_text(entry, "summary")
        link = node_text(entry, "link")
        if not link:
            for child in list(entry):
                if child.tag.split("}")[-1].lower() == "link":
                    link = child.attrib.get("href", "")
                    if link:
                        break
        if not title or not link:
            continue
        url = normalize_url(link)
        if relevant(f"{title} {description}", url):
            items[url] = {
                "source": name,
                "title": title[:350],
                "url": url,
                "summary": description[:3000],
            }
    return items


def fetch_source(source):
    if source["kind"] == "rss":
        return fetch_rss_links(source["name"], source["url"])
    return fetch_html_links(
        source["name"],
        source["url"],
        allow_proxy=(source["kind"] == "html_proxy"),
    )


def load_state():
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state):
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def translate_piece(text: str) -> str:
    """별도 API 키 없이 짧은 원문 조각을 한국어로 번역한다."""
    text = clean_title(text)
    if not text:
        return ""

    pieces = []
    remaining = text
    while len(remaining) > 430:
        cut = max(
            remaining.rfind(". ", 0, 430),
            remaining.rfind("; ", 0, 430),
            remaining.rfind(", ", 0, 430),
            remaining.rfind(" ", 0, 430),
        )
        if cut < 180:
            cut = 430
        pieces.append(remaining[:cut + 1].strip())
        remaining = remaining[cut + 1:].strip()
    if remaining:
        pieces.append(remaining)

    translated = []
    for piece in pieces:
        try:
            r = requests.get(
                "https://translate.googleapis.com/translate_a/single",
                params={
                    "client": "gtx",
                    "sl": "en",
                    "tl": "ko",
                    "dt": "t",
                    "q": piece,
                },
                headers=HEADERS,
                timeout=TIMEOUT,
            )
            r.raise_for_status()
            data = r.json()
            translated.append("".join(part[0] for part in data[0] if part and part[0]))
        except Exception as e:
            print(f"[WARN] 한국어 번역 실패: {e}")
            translated.append(piece)
    return " ".join(translated).strip()


def compact_korean(text: str, max_chars: int = 180) -> str:
    """텔레그램에서 한눈에 읽히도록 한 항목을 짧게 줄인다."""
    text = " ".join((text or "").split())
    if len(text) <= max_chars:
        return text

    # 문장 끝이나 쉼표에서 자연스럽게 자른다.
    candidates = [
        text.rfind("다. ", 0, max_chars),
        text.rfind("요. ", 0, max_chars),
        text.rfind("며, ", 0, max_chars),
        text.rfind(", ", 0, max_chars),
        text.rfind(" ", 0, max_chars),
    ]
    cut = max(candidates)
    if cut < 90:
        cut = max_chars
    return text[:cut + 1].rstrip(" ,") + "…"


def extract_article_blocks(url: str):
    try:
        raw, route = get_text(url, allow_proxy=True)
    except Exception as e:
        print(f"[WARN] 원문 본문 읽기 실패 {url}: {e}")
        return []

    blocks = []
    if route == "직접":
        soup = BeautifulSoup(raw, "html.parser")
        for tag in soup(["script", "style", "noscript", "svg", "nav", "footer", "header", "form"]):
            tag.decompose()
        root = soup.find("article") or soup.find("main") or soup.body or soup
        for node in root.find_all(["h1", "h2", "h3", "p", "li"]):
            text = clean_title(node.get_text(" ", strip=True))
            if 20 <= len(text) <= 1200:
                blocks.append(text)
    else:
        for line in raw.splitlines():
            line = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", line)
            line = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", line)
            line = re.sub(r"^[#>*\-\s]+", "", line).strip()
            if 20 <= len(line) <= 1200:
                blocks.append(clean_title(line))

    deduped = []
    seen = set()
    for block in blocks:
        key = re.sub(r"\W+", " ", block.lower()).strip()
        if key and key not in seen:
            seen.add(key)
            deduped.append(block)
    return deduped[:120]


def token_overlap(a: str, b: str) -> float:
    ta = set(re.findall(r"[a-z0-9]+", a.lower()))
    tb = set(re.findall(r"[a-z0-9]+", b.lower()))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / max(1, min(len(ta), len(tb)))


def select_key_blocks(item, blocks, limit=3):
    title_key = re.sub(r"\W+", " ", item.get("title", "").lower()).strip()
    scored = []
    for idx, block in enumerate(blocks):
        block_key = re.sub(r"\W+", " ", block.lower()).strip()
        if not block_key or block_key == title_key:
            continue
        low = block.lower()
        score = 0
        score += sum(3 for term in HIGH_VALUE_TERMS if term in low)
        if re.search(r"\$|\b\d+(?:\.\d+)?%?\b", block):
            score += 2
        if idx < 12:
            score += 2
        if any(k in low for k in KEYWORDS):
            score += 2
        if score > 0:
            scored.append((score, idx, block))

    ranked = sorted(scored, key=lambda x: (-x[0], x[1]))
    chosen = []
    for score, idx, block in ranked:
        if any(token_overlap(block, existing[2]) >= 0.72 for existing in chosen):
            continue
        chosen.append((score, idx, block))
        if len(chosen) >= limit:
            break

    chosen.sort(key=lambda x: x[1])
    return [block for _, _, block in chosen]


def build_message(item):
    translated_title = compact_korean(translate_piece(item["title"]), 90)
    blocks = extract_article_blocks(item["url"])
    selected = select_key_blocks(item, blocks, limit=3)

    if not selected and item.get("summary"):
        selected = [item["summary"]]

    translated_blocks = [compact_korean(translate_piece(block), 180) for block in selected]
    translated_blocks = [t for t in translated_blocks if t]

    if not translated_blocks:
        translated_blocks = ["새로운 공식자료가 감지되었습니다. 세부 내용은 원문에서 확인할 수 있습니다."]

    bullet_text = "\n".join(f"• {html.escape(t)}" for t in translated_blocks[:3])
    safe_title = html.escape(translated_title or item["title"])
    safe_source = html.escape(item["source"])
    safe_url = html.escape(item["url"], quote=True)

    return (
        "🚨 <b>미국 조선·해군 중요 변화</b>\n\n"
        f"<b>{safe_title}</b>\n"
        f"<i>{safe_source}</i>\n\n"
        f"{bullet_text}\n\n"
        f"🔎 <a href=\"{safe_url}\">원문</a>"
    )


def send_telegram(text: str):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN 또는 TELEGRAM_CHAT_ID가 없습니다.")

    api = f"https://api.telegram.org/bot{token}/sendMessage"
    r = requests.post(
        api,
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
    old_state = load_state()
    new_state = dict(old_state)
    new_items = []
    ok_sources = 0
    failed_sources = []

    for source in SOURCES:
        name = source["name"]
        try:
            current = fetch_source(source)
            ok_sources += 1
        except Exception as e:
            failed_sources.append(name)
            print(f"[WARN] {name} 조회 실패: {e}")
            continue

        previous_urls = set(old_state.get(name, []))
        current_urls = set(current.keys())

        if name in old_state:
            for url in sorted(current_urls - previous_urls):
                new_items.append(current[url])
        else:
            print(f"[BASELINE] {name}: 첫 정상 수집이라 기존 자료 알림 생략")

        new_state[name] = sorted(current_urls | previous_urls)[-500:]
        print(f"[OK] {name}: 관련 링크 {len(current_urls)}개")

    save_state(new_state)

    test_mode = os.environ.get("TEST_MODE") == "1"
    if not test_mode:
        if new_items:
            for item in new_items[:8]:
                send_telegram(build_message(item))
                print(f"[SENT] 한국어 알림 - {item['source']} - {item['title']}")
        else:
            print("새로운 관련 공식자료 없음")

    if test_mode:
        test_item = {
            "source": "백악관 팩트시트",
            "title": "President Donald J. Trump Rebuilds the U.S. Navy and America’s Shipbuilding Industrial Base",
            "url": "https://www.whitehouse.gov/fact-sheets/2026/08/fact-sheet-president-donald-j-trump-rebuilds-the-u-s-navy-and-americas-shipbuilding-industrial-base/",
            "summary": "",
        }
        send_telegram(build_message(test_item))
        print(f"[TEST SENT] 3줄 압축형 한국어 알림 테스트 / 정상 출처 {ok_sources}/{len(SOURCES)} / 실패 {failed_sources or '없음'}")


if __name__ == "__main__":
    main()
