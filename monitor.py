import json
import os
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

STATE_FILE = Path("state.json")
TIMEOUT = 25
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; US-Shipbuilding-Watch/1.0; +https://github.com/qedgwangju-dot/us-shipbuilding-watch)"
}

SOURCES = [
    ("백악관 팩트시트", "https://www.whitehouse.gov/fact-sheets/"),
    ("백악관 대통령 조치", "https://www.whitehouse.gov/presidential-actions/"),
    ("미 해군 보도자료", "https://www.navy.mil/Press-Office/Press-Releases/"),
    ("NAVSEA 뉴스", "https://www.navsea.navy.mil/Media/News/"),
    ("미 국방부 보도자료", "https://www.defense.gov/News/Releases/"),
    ("MARAD 뉴스룸", "https://www.maritime.dot.gov/newsroom"),
    ("미 해안경비대 보도자료", "https://www.news.uscg.mil/Press-Releases/"),
]

KEYWORDS = [
    "shipbuild", "shipyard", "ship construction", "vessel construction",
    "naval ship", "warship", "submarine", "aircraft carrier", "carrier",
    "destroyer", "frigate", "icebreaker", "arctic security cutter",
    "dry dock", "overhaul", "ship repair", "fleet maintenance",
    "maritime industrial base", "industrial base", "foreign shipbuilder",
    "foreign-built", "overseas construction", "parent shipyard",
    "hanwha", "philly shipyard", "hd hyundai", "hyundai heavy",
    "samsung heavy", "korea shipbuilding", "korean shipyard",
    "cvn-81", "emals", "advanced weapons elevator", "navsea",
]


def normalize_url(url: str) -> str:
    p = urlparse(url)
    return urlunparse((p.scheme, p.netloc, p.path.rstrip("/"), "", "", ""))


def relevant(text: str, url: str) -> bool:
    hay = f"{text} {url}".lower()
    return any(k in hay for k in KEYWORDS)


def fetch_links(name: str, page_url: str):
    r = requests.get(page_url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    items = {}

    for a in soup.find_all("a", href=True):
        title = " ".join(a.get_text(" ", strip=True).split())
        if len(title) < 8:
            continue

        url = normalize_url(urljoin(page_url, a["href"]))
        if not url.startswith("http"):
            continue

        # 같은 공식 도메인 링크 위주로 본다.
        if urlparse(url).netloc != urlparse(page_url).netloc:
            continue

        if relevant(title, url):
            items[url] = {
                "source": name,
                "title": title[:350],
                "url": url,
            }

    return items


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
        f"새 공식자료가 감지되었습니다.\n"
        f"출처: {item['source']}\n"
        f"제목: {item['title']}\n\n"
        "확인 포인트: 함종·척수·계약금액·해외건조 허용·미국 조선소 투자·"
        "한화오션/HD현대 등 한국 기업 연결 여부\n\n"
        f"원문: {item['url']}"
    )


def main():
    old_state = load_state()
    first_run = not bool(old_state)
    new_state = dict(old_state)
    new_items = []

    for name, page_url in SOURCES:
        try:
            current = fetch_links(name, page_url)
        except Exception as e:
            print(f"[WARN] {name} 조회 실패: {e}")
            continue

        previous_urls = set(old_state.get(name, []))
        current_urls = set(current.keys())

        if not first_run:
            for url in sorted(current_urls - previous_urls):
                new_items.append(current[url])

        # 최신 페이지에서 사라진 과거 링크도 중복 알림 방지를 위해 일정 기간 기억한다.
        merged = list(dict.fromkeys(list(current_urls) + list(previous_urls)))
        new_state[name] = merged[-500:]
        print(f"[OK] {name}: 관련 링크 {len(current_urls)}개")

    save_state(new_state)

    if first_run:
        print("첫 실행: 현재 상태만 기준선으로 저장하고 알림은 보내지 않습니다.")
        return

    if not new_items:
        print("새로운 관련 공식자료 없음")
        return

    # 한 번에 너무 많은 메시지가 쏟아지는 것을 방지한다.
    for item in new_items[:10]:
        send_telegram(build_message(item))
        print(f"[SENT] {item['source']} - {item['title']}")


if __name__ == "__main__":
    main()
