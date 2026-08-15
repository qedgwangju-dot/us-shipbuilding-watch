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
    "User-Agent": "Mozilla/5.0 (compatible; US-Shipbuilding-Watch/1.1; +https://github.com/qedgwangju-dot/us-shipbuilding-watch)"
}

# RSS가 공식 제공되는 곳은 RSS를 우선 사용한다.
# GitHub Actions의 클라우드 IP를 막는 일부 WEB.mil 페이지는 직접 접속 실패 시
# Jina Reader를 '읽기 통로'로만 사용하고, 실제 링크/출처는 공식 사이트만 인정한다.
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
        "url": "https://www.navy.mil/DesktopModules/ArticleCS/RSS.ashx?ContentType=2&Site=1067&max=30",
        "kind": "rss",
    },
    {
        "name": "NAVSEA 뉴스",
        "url": "https://www.navsea.navy.mil/Media/News/",
        "kind": "html_proxy",
    },
    {
        "name": "미 국방부 공식 보도자료",
        "url": "https://www.war.gov/DesktopModules/ArticleCS/RSS.ashx?ContentType=9&Site=945&max=30",
        "kind": "rss",
    },
    {
        "name": "MARAD 뉴스룸",
        "url": "https://www.maritime.dot.gov/taxonomy/term/11/feed",
        "kind": "rss",
    },
    {
        "name": "미 해안경비대 보도자료",
        "url": "https://www.news.uscg.mil/Press-Releases/",
        "kind": "html_proxy",
    },
]

KEYWORDS = [
    "shipbuild", "shipyard", "ship construction", "vessel construction",
    "naval ship", "warship", "submarine", "aircraft carrier", "carrier",
    "destroyer", "frigate", "icebreaker", "arctic security cutter",
    "polar security cutter", "waterways commerce cutter",
    "dry dock", "overhaul", "ship repair", "fleet maintenance",
    "maritime industrial base", "industrial base", "foreign shipbuilder",
    "foreign-built", "overseas construction", "parent shipyard",
    "vessel construction manager", "new shipyard", "naval shipyard",
    "hanwha", "philly shipyard", "hd hyundai", "hyundai heavy",
    "samsung heavy", "korea shipbuilding", "korean shipyard",
    "cvn-81", "emals", "advanced weapons elevator", "navsea",
]


def normalize_url(url: str) -> str:
    p = urlparse(url.strip())
    return urlunparse((p.scheme, p.netloc, p.path.rstrip("/"), "", "", ""))


def relevant(text: str, url: str) -> bool:
    hay = f"{text} {url}".lower()
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
    if official_host == "www.navy.mil":
        return host.endswith("navy.mil")
    if official_host == "www.war.gov":
        return host.endswith("war.gov") or host.endswith("defense.gov")
    return host == official_host or host.endswith("." + official_host.removeprefix("www."))


def fetch_html_links(name: str, page_url: str, allow_proxy: bool = False):
    text, route = get_text(page_url, allow_proxy=allow_proxy)
    official_host = official_host_for(page_url)
    items = {}

    # Jina Reader 결과는 Markdown 링크 형식이다.
    if route != "직접":
        for title, href in re.findall(r"\[([^\]\n]{8,350})\]\((https?://[^)\s]+)\)", text):
            title = " ".join(title.split())
            url = normalize_url(href)
            if host_matches(url, official_host) and relevant(title, url):
                items[url] = {"source": name, "title": title[:350], "url": url}
        print(f"[INFO] {name}: 직접 접속 차단으로 읽기 보조 사용")
        return items

    soup = BeautifulSoup(text, "html.parser")
    for a in soup.find_all("a", href=True):
        title = " ".join(a.get_text(" ", strip=True).split())
        if len(title) < 8:
            continue

        url = normalize_url(urljoin(page_url, a["href"]))
        if not url.startswith("http") or not host_matches(url, official_host):
            continue

        if relevant(title, url):
            items[url] = {"source": name, "title": title[:350], "url": url}

    return items


def node_text(node, tag_name):
    for child in list(node):
        if child.tag.split("}")[-1].lower() == tag_name.lower():
            return " ".join("".join(child.itertext()).split())
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
            items[url] = {"source": name, "title": title[:350], "url": url}

    return items


def fetch_source(source):
    kind = source["kind"]
    if kind == "rss":
        return fetch_rss_links(source["name"], source["url"])
    return fetch_html_links(
        source["name"],
        source["url"],
        allow_proxy=(kind == "html_proxy"),
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
            "disable_web_page_preview": False,
        },
        timeout=TIMEOUT,
    )
    r.raise_for_status()


def build_message(item):
    return (
        "🚨 미국 조선·해군 정책 웹감시\n\n"
        "새 공식자료가 감지되었습니다.\n"
        f"출처: {item['source']}\n"
        f"제목: {item['title']}\n\n"
        "확인 포인트: 함종·척수·계약금액·해외 건조 허용·미국 조선소 투자·"
        "한화오션·HD현대 등 한국 기업 연결 여부\n\n"
        f"공식 원문: {item['url']}"
    )


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

        # 이 출처를 처음 정상 수집한 실행에서는 기준선만 만든다.
        if name in old_state:
            for url in sorted(current_urls - previous_urls):
                new_items.append(current[url])
        else:
            print(f"[BASELINE] {name}: 첫 정상 수집이라 기존 자료 알림 생략")

        merged = list(dict.fromkeys(list(current_urls) + list(previous_urls)))
        new_state[name] = merged[-500:]
        print(f"[OK] {name}: 관련 링크 {len(current_urls)}개")

    save_state(new_state)

    if new_items:
        for item in new_items[:10]:
            send_telegram(build_message(item))
            print(f"[SENT] {item['source']} - {item['title']}")
    else:
        print("새로운 관련 공식자료 없음")

    # 사용자가 Actions에서 Run workflow를 누른 경우에는 연결 확인 메시지를 1회 보낸다.
    if os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch":
        failed_text = ", ".join(failed_sources) if failed_sources else "없음"
        send_telegram(
            "✅ 텔레그램 연결 성공\n\n"
            "미국 조선·해군 정책 웹감시가 정상 실행되었습니다.\n"
            f"정상 확인 출처: {ok_sources}/{len(SOURCES)}\n"
            f"이번 실행 조회 실패: {failed_text}\n"
            "정기 실행에서는 새 관련 자료가 생겼을 때만 알림을 보냅니다."
        )
        print("[TEST SENT] 텔레그램 연결 확인 메시지 전송")


if __name__ == "__main__":
    main()
