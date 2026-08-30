import hashlib
import html
import json
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

import monitor

STATE_FILE = Path("ai_remote_state.json")
TIMEOUT = 30
HEADERS = monitor.HEADERS

HTML_SOURCES = [
    {"name": "BIS 공식 뉴스", "url": "https://www.bis.gov/news-updates"},
    {"name": "BIS 집행·가이던스", "url": "https://www.bis.gov/enforcement/enforcement-policy-memos"},
    {"name": "미 연방관보 BIS", "url": "https://www.federalregister.gov/agencies/industry-and-security-bureau"},
]

NEWS_RSS = [
    {
        "name": "해외 보도 감시",
        "url": "https://news.google.com/rss/search?q=%22AI+Diffusion+Rule%22+remote+access+China+data+center&hl=en-US&gl=US&ceid=US:en",
    },
    {
        "name": "해외 보도 감시",
        "url": "https://news.google.com/rss/search?q=BIS+remote+access+AI+chips+Thailand+Singapore+China&hl=en-US&gl=US&ceid=US:en",
    },
    {
        "name": "의회·법안 보도 감시",
        "url": "https://news.google.com/rss/search?q=%22Remote+Access+Security+Act%22+AI+chips&hl=en-US&gl=US&ceid=US:en",
    },
]

TRACKED_PAGES = [
    {
        "name": "Remote Access Security Act H.R.2683",
        "url": "https://www.govinfo.gov/app/details/BILLS-119hr2683rfs",
        "kind": "bill",
    },
    {
        "name": "Remote Access Security Act S.3519",
        "url": "https://www.govinfo.gov/app/details/BILLS-119s3519is",
        "kind": "bill",
    },
    {
        "name": "BIS AI Action Plan 규제계획",
        "url": "https://www.reginfo.gov/public/do/eAgendaViewRule?RIN=0694-AJ90&operation=OPERATION_PRINT_RULE&pubId=202510",
        "kind": "reginfo",
    },
]

CORE_TERMS = [
    "ai diffusion", "artificial intelligence diffusion", "advanced ai chips",
    "advanced computing", "ai chip", "gpu", "nvidia", "h200", "blackwell",
    "gb200", "gb300", "mi325", "remote access", "cloud computing",
    "data center", "data centre", "remote end user",
]

POLICY_TERMS = [
    "export control", "license", "licensing", "bureau of industry and security",
    " bis ", "china", "prc", "country group d:5", "macau", "know your customer",
    "kyc", "end user", "remote access security act", "export administration regulations",
    "ear", "diversion", "secure export", "third country", "overseas subsidiary",
]

STRONG_TERMS = [
    "remote access security act", "ai diffusion rule", "remote access to chips",
    "remote access of items", "secure export of advanced ai chips",
]

TITLE_OVERRIDES = {
    "Trump Administration Working on AI Rule to Curb China’s Remote Access to Chips": "트럼프 행정부, 중국의 해외 데이터센터 원격 AI 칩 접근 차단 규칙 검토",
    "Department of Commerce Announces Rescission of Biden-Era Artificial Intelligence Diffusion Rule, Strengthens Chip-Related Export Controls": "미 상무부, 바이든 AI Diffusion Rule 철회·첨단 AI 칩 수출통제 재설계",
    "Remote Access Security Act": "미국 Remote Access Security Act 진행 상황 변화",
}


def normalize_url(url: str) -> str:
    p = urlparse((url or "").strip())
    return f"{p.scheme}://{p.netloc}{p.path.rstrip('/')}" if p.scheme and p.netloc else (url or "").strip()


def clean(text: str) -> str:
    text = BeautifulSoup(text or "", "html.parser").get_text(" ", strip=True)
    return " ".join(text.split())


def relevant(text: str) -> bool:
    low = f" {text.lower()} "
    if any(term in low for term in STRONG_TERMS):
        return True
    return any(term in low for term in CORE_TERMS) and any(term in low for term in POLICY_TERMS)


def get_text(url: str) -> str:
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        return r.text
    except Exception:
        r = requests.get(f"https://r.jina.ai/{url}", headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        return r.text


def fetch_html_source(source):
    raw = get_text(source["url"])
    items = {}
    source_host = urlparse(source["url"]).netloc.lower().removeprefix("www.")

    if raw.lstrip().startswith("Title:") or "Markdown Content:" in raw[:1600]:
        pairs = re.findall(r"\[([^\]\n]{8,350})\]\((https?://[^)\s]+)\)", raw)
        for title, href in pairs:
            title = clean(title)
            url = normalize_url(href)
            host = urlparse(url).netloc.lower().removeprefix("www.")
            if source_host not in {"www.federalregister.gov", "federalregister.gov"} and not host.endswith(source_host):
                continue
            if relevant(f"{title} {url}"):
                items[url] = {"source": source["name"], "title": title, "url": url, "summary": "", "stage": "공식"}
        return items

    soup = BeautifulSoup(raw, "html.parser")
    for a in soup.find_all("a", href=True):
        title = clean(a.get_text(" ", strip=True))
        if len(title) < 8:
            continue
        url = normalize_url(urljoin(source["url"], a["href"]))
        host = urlparse(url).netloc.lower().removeprefix("www.")
        if source_host not in {"www.federalregister.gov", "federalregister.gov"} and not host.endswith(source_host):
            continue
        if relevant(f"{title} {url}"):
            items[url] = {"source": source["name"], "title": title, "url": url, "summary": "", "stage": "공식"}
    return items


def node_text(node, tag):
    for child in list(node):
        if child.tag.split("}")[-1].lower() == tag:
            return clean("".join(child.itertext()))
    return ""


def fetch_news_rss(source):
    r = requests.get(source["url"], headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    root = ET.fromstring(r.text)
    items = {}
    for entry in [x for x in root.iter() if x.tag.split("}")[-1].lower() == "item"]:
        title = node_text(entry, "title")
        link = node_text(entry, "link")
        desc = node_text(entry, "description")
        src = node_text(entry, "source")
        if not title or not link or not relevant(f"{title} {desc}"):
            continue
        key = normalize_url(link)
        label = f"{source['name']} · {src}" if src else source["name"]
        items[key] = {"source": label, "title": title, "url": link, "summary": desc, "stage": "보도"}
    return items


def meaningful_snapshot(page):
    raw = get_text(page["url"])
    text = clean(raw)
    low = text.lower()

    if page["kind"] == "bill":
        chunks = []
        patterns = [
            r"Last Action Date Listed\s*([^A-Z]{0,120})",
            r"Action\s*(.{0,500}?)\s*Bill Number",
            r"Committee Assignment\s*(.{0,500}?)(?:Sponsor|Document Citations|$)",
        ]
        for pattern in patterns:
            m = re.search(pattern, text, flags=re.I | re.S)
            if m:
                chunks.append(clean(m.group(1)))
        snapshot = " | ".join(chunks) or text[:3500]
    else:
        chunks = []
        for key in ["Title:", "Abstract:", "Agenda Stage of Rulemaking:", "Timetable:", "Final Action"]:
            idx = text.find(key)
            if idx >= 0:
                chunks.append(text[idx:idx + 1000])
        snapshot = " | ".join(chunks) or text[:4000]

    digest = hashlib.sha256(snapshot.encode("utf-8")).hexdigest()
    title = page["name"]
    return digest, snapshot, {"source": page["name"], "title": title, "url": page["url"], "summary": snapshot, "stage": "공식"}


def extract_article_text(item):
    try:
        blocks = monitor.extract_article_blocks(item["url"])
        if blocks:
            return " ".join(blocks[:80])
    except Exception:
        pass
    return clean(item.get("summary", ""))


def korean_title(raw_title: str) -> str:
    for key, value in TITLE_OVERRIDES.items():
        if key.lower() in raw_title.lower():
            return value
    ko = monitor.translate_piece(raw_title)
    if ko and monitor.has_korean(ko):
        return monitor.compact_korean(ko, 105)
    return "미국 AI 칩·원격접근 규제 새 변화"


def countries_in(text: str):
    mapping = {
        "thailand": "태국", "singapore": "싱가포르", "malaysia": "말레이시아",
        "united arab emirates": "UAE", "uae": "UAE", "japan": "일본",
        "south korea": "한국", "korea": "한국", "china": "중국", "macau": "마카오",
    }
    low = text.lower()
    out = []
    for key, label in mapping.items():
        if key in low and label not in out:
            out.append(label)
    return out


def build_message(item):
    raw = f"{item.get('title', '')} {item.get('summary', '')} {extract_article_text(item)}"
    low = raw.lower()
    title = korean_title(item.get("title", ""))
    bullets = []

    if item.get("stage") == "보도":
        bullets.append("단계: 정책 검토·보도 단계 — BIS 공식 초안·최종규칙이 확인되기 전에는 확정 규제로 보지 않음")
    else:
        bullets.append("단계: 미국 정부·연방관보·의회 공식자료 변화")

    if "remote access security act" in low or "remote access of items" in low:
        bullets.append("달라진 점: 원격 클라우드 접근을 수출통제의 법적 규제축으로 명시하는 Remote Access Security Act 진행 상황이 변경됨")
    elif "ai diffusion" in low or "secure export of advanced ai chips" in low:
        bullets.append("달라진 점: 바이든식 광범위 AI Diffusion Rule 대신 첨단 AI 칩의 안전한 해외수출·중국 우회접근 차단을 겨냥한 후속 규칙의 범위·일정이 변경됨")
    elif "remote access" in low:
        bullets.append("달라진 점: 중국 기업의 제3국 데이터센터·클라우드 원격 GPU 접근을 통제하는 규제 범위 또는 집행방식에 새 변화가 감지됨")
    else:
        bullets.append("새 변화: 첨단 AI 칩 해외수출·중국 우회사용·데이터센터 최종사용자 통제와 관련된 새 자료가 확인됨")

    found_countries = countries_in(raw)
    if found_countries:
        bullets.append(f"대상 지역 확인: {', '.join(found_countries[:5])} — 최종 규칙에서 실제 라이선스 대상국인지, 단순 사례 언급인지 구분 필요")
    elif any(x in low for x in ["know your customer", "kyc", "remote end user", "end user"]):
        bullets.append("KYC·최종사용자: 데이터센터·클라우드가 고객 신원·최종모회사·원격사용자를 어디까지 확인·보고해야 하는지가 핵심")

    bullets.append("투자 관점: NVIDIA·AMD의 실제 악재는 제3국 GPU 주문이 취소될 때이며, 승인된 미국계 클라우드·다른 지역으로 재배치되면 총 GPU·HBM 수요 영향은 제한적. SK하이닉스·삼성전자 HBM은 총 가속기 출하 감소 여부를 우선 확인")
    bullets.append("다음 확인: BIS 초안·Federal Register, 대상 국가, ECCN·성능 기준, KYC·원격사용자 조건, 라이선스 승인/거절, H.R.2683·S.3519 상원 진행, 실제 GPU 주문 취소 여부")

    safe_url = html.escape(item["url"], quote=True)
    safe_source = html.escape(item["source"])
    bullet_text = "\n".join(f"• {html.escape(x)}" for x in bullets[:5])
    return (
        "🚨 <b>미국 AI 칩·원격접근 규제 중요 변화</b>\n\n"
        f"<b>{html.escape(title)}</b>\n"
        f"출처: <a href=\"{safe_url}\">{safe_source}</a>\n\n"
        f"{bullet_text}\n\n"
        f"<a href=\"{safe_url}\"><b>원문</b></a>"
    )


def send_telegram(message: str):
    token = os.environ.get("AI_REMOTE_TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("AI_REMOTE_TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        raise RuntimeError("AI_REMOTE_TELEGRAM_BOT_TOKEN 또는 AI_REMOTE_TELEGRAM_CHAT_ID가 없음")
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
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


def collect():
    listings = {}
    for source in HTML_SOURCES:
        try:
            listings[source["name"]] = fetch_html_source(source)
            print(f"[AI OK] {source['name']}: {len(listings[source['name']])}개")
        except Exception as e:
            print(f"[AI WARN] {source['name']} 조회 실패: {e}")
            listings[source["name"]] = {}
    for source in NEWS_RSS:
        key = source["name"] + " " + source["url"]
        try:
            listings[key] = fetch_news_rss(source)
            print(f"[AI OK] {source['name']}: {len(listings[key])}개")
        except Exception as e:
            print(f"[AI WARN] {source['name']} 조회 실패: {e}")
            listings[key] = {}

    tracked = {}
    for page in TRACKED_PAGES:
        try:
            digest, snapshot, item = meaningful_snapshot(page)
            tracked[page["name"]] = {"digest": digest, "snapshot": snapshot, "item": item}
            print(f"[AI OK] {page['name']} 상태 확인")
        except Exception as e:
            print(f"[AI WARN] {page['name']} 조회 실패: {e}")
    return listings, tracked


def main():
    old = load_state()
    listings, tracked = collect()
    new = {"listings": {}, "tracked": {}}
    alerts = []

    old_listings = old.get("listings", {})
    for name, items in listings.items():
        urls = set(items.keys())
        prev = set(old_listings.get(name, []))
        if old:
            for url in sorted(urls - prev):
                alerts.append(items[url])
        new["listings"][name] = sorted(prev | urls)[-600:]

    old_tracked = old.get("tracked", {})
    for name, data in tracked.items():
        old_digest = (old_tracked.get(name) or {}).get("digest")
        if old and old_digest and old_digest != data["digest"]:
            alerts.append(data["item"])
        new["tracked"][name] = {"digest": data["digest"], "snapshot": data["snapshot"][:2500]}

    if not old:
        save_state(new)
        print("[AI BASELINE] 첫 실행: 현재 자료를 기준선으로 저장하고 과거 알림은 보내지 않음")
        return

    token = os.environ.get("AI_REMOTE_TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("AI_REMOTE_TELEGRAM_CHAT_ID", "").strip()
    if alerts and (not token or not chat_id):
        print("[AI PENDING] 새 변화가 있으나 새 텔레그램 봇 Secret 미설정 — 상태를 갱신하지 않아 다음 실행에서 다시 알림 예정")
        return

    sent = 0
    for item in alerts[:8]:
        try:
            send_telegram(build_message(item))
            sent += 1
            print(f"[AI SENT] {item['source']} - {item['title']}")
        except Exception as e:
            print(f"[AI SEND FAIL] {e}")
            return

    save_state(new)
    print(f"[AI DONE] 신규 알림 {sent}건")


if __name__ == "__main__":
    main()
