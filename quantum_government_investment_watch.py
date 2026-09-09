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

STATE_FILE = Path("quantum_government_investment_state.json")
TIMEOUT = 30
HEADERS = monitor.HEADERS
KST = ZoneInfo("Asia/Seoul")

OFFICIAL_SOURCES = [
    {"name": "NIST·CHIPS 공식 뉴스", "url": "https://www.nist.gov/news-events/news"},
    {"name": "D-Wave 공식 뉴스", "url": "https://www.dwavequantum.com/company/newsroom/"},
    {"name": "Rigetti 공식 IR", "url": "https://investors.rigetti.com/news-events/news-releases"},
    {"name": "Quantinuum 공식 IR", "url": "https://ir.quantinuum.com/news-events/press-releases"},
    {"name": "GlobalFoundries 공식 뉴스", "url": "https://gf.com/gf-press-release/"},
    {"name": "IBM 공식 뉴스", "url": "https://newsroom.ibm.com/"},
]

NEWS_RSS = [
    {
        "name": "주요 보도 감시",
        "url": "https://news.google.com/rss/search?q=US+government+quantum+computing+CHIPS+funding+equity+D-Wave+Rigetti+Quantinuum&hl=en-US&gl=US&ceid=US:en",
    },
    {
        "name": "양자 정부투자 보도 감시",
        "url": "https://news.google.com/rss/search?q=%22Department+of+Commerce%22+quantum+funding+equity+CHIPS&hl=en-US&gl=US&ceid=US:en",
    },
]

PORTFOLIO_COMPANIES = [
    "globalfoundries", "ibm", "atom computing", "diraq", "d-wave", "dwave",
    "infleqtion", "psiquantum", "quantinuum", "rigetti",
]

QUANTUM_TERMS = [
    "quantum", "qubit", "superconducting", "trapped ion", "trapped-ion",
    "neutral atom", "neutral-atom", "photonic", "silicon spin", "silicon-spin",
    "annealing", "cryostat", "quantum foundry", "quantum computing",
]

MONEY_POLICY_TERMS = [
    "chips act", "chips and science act", "department of commerce", "nist",
    "award", "funding", "grant", "incentive", "investment", "equity",
    "minority stake", "letter of intent", "definitive agreement", "final award",
    "milestone", "purchase", "procurement", "contract", "foundry",
]

HIGH_PRIORITY_TERMS = [
    "definitive agreement", "final award", "minority stake", "equity stake",
    "letter of intent", "$100 million", "$375 million", "$1 billion",
    "globalfoundries", "ibm", "manufacturing", "foundry", "milestone",
]

KNOWN_CURRENT_URLS = {
    "https://www.dwavequantum.com/company/newsroom/press-release/d-wave-finalizes-agreement-with-u-s-department-of-commerce-for-up-to-100-million/",
    "https://investors.rigetti.com/news-releases/news-release-details/rigetti-signs-definitive-agreement-100m-us-government-accelerate",
    "https://ir.quantinuum.com/news-releases/news-release-details/quantinuum-finalizes-100-million-chips-rd-award-us-department",
}


def clean(text: str) -> str:
    return " ".join(BeautifulSoup(text or "", "html.parser").get_text(" ", strip=True).split())


def normalize_url(url: str) -> str:
    p = urlparse((url or "").strip())
    if not p.scheme or not p.netloc:
        return (url or "").strip()
    return f"{p.scheme}://{p.netloc}{p.path.rstrip('/')}"


def relevant(text: str) -> bool:
    low = f" {clean(text).lower()} "
    has_quantum = any(x in low for x in QUANTUM_TERMS)
    has_policy = any(x in low for x in MONEY_POLICY_TERMS)
    has_portfolio = any(x in low for x in PORTFOLIO_COMPANIES)
    return has_quantum and (has_policy or has_portfolio)


def get_text(url: str):
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        return r.text
    except Exception:
        r = requests.get(f"https://r.jina.ai/{url}", headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        return r.text


def extract_date_from_text(text: str) -> str:
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


def fetch_html_source(source):
    raw = get_text(source["url"])
    soup = BeautifulSoup(raw, "html.parser")
    items = {}
    base_host = urlparse(source["url"]).netloc.lower().removeprefix("www.")

    for a in soup.find_all("a", href=True):
        title = clean(a.get_text(" ", strip=True))
        if len(title) < 10:
            continue
        url = normalize_url(urljoin(source["url"], a["href"]))
        host = urlparse(url).netloc.lower().removeprefix("www.")
        if not host.endswith(base_host):
            continue
        if not relevant(f"{title} {url}"):
            continue
        items[url] = {
            "source": source["name"],
            "title": title[:350],
            "url": url,
            "summary": "",
            "stage": "공식",
            "published_date": "",
        }
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
        key = normalize_url(link)
        items[key] = {
            "source": src or source["name"],
            "title": title,
            "url": link,
            "summary": desc,
            "stage": "보도",
            "published_date": published,
        }
    return items


def fetch_article_meta(item):
    try:
        raw = get_text(item["url"])
    except Exception:
        return item
    text = clean(raw)
    if not item.get("published_date"):
        item["published_date"] = extract_date_from_text(text)
    # 메시지 해석용으로 앞부분을 보존하되 상태키에는 쓰지 않는다.
    item["article_text"] = text[:12000]
    return item


def fx_usd_krw():
    # ECB 기반 Frankfurter를 우선 사용. 실패하면 최근 검증값을 고정값으로 사용하지 않고 환산 줄을 생략한다.
    try:
        r = requests.get("https://api.frankfurter.app/latest", params={"from": "USD", "to": "KRW"}, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        rate = float((data.get("rates") or {}).get("KRW"))
        date = str(data.get("date") or "")
        if rate > 0:
            return rate, date
    except Exception as e:
        print(f"[WARN] USD/KRW 환율 조회 실패: {e}")
    return None, ""


def krw_text(usd_million: float, rate: float) -> str:
    won = usd_million * 1_000_000 * rate
    eok = won / 100_000_000
    if eok >= 10000:
        jo = int(eok // 10000)
        rem = eok - jo * 10000
        return f"약 {jo}조{rem:,.0f}억원" if rem >= 1 else f"약 {jo}조원"
    return f"약 {eok:,.0f}억원"


def strip_source_suffix(title: str) -> str:
    title = clean(title)
    return re.sub(r"\s[-–—]\s[^-–—]{2,80}$", "", title).strip()


def classify_item(item):
    raw = f"{item.get('title','')} {item.get('summary','')} {item.get('article_text','')}"
    low = raw.lower()
    companies = []
    display = [
        ("D-Wave", ["d-wave", "dwave"]),
        ("Rigetti", ["rigetti"]),
        ("Quantinuum", ["quantinuum"]),
        ("GlobalFoundries", ["globalfoundries"]),
        ("IBM", [" ibm ", "ibm quantum"]),
        ("Atom Computing", ["atom computing"]),
        ("Diraq", ["diraq"]),
        ("Infleqtion", ["infleqtion"]),
        ("PsiQuantum", ["psiquantum"]),
    ]
    for name, keys in display:
        if any(k in f" {low} " for k in keys):
            companies.append(name)

    if "definitive agreement" in low or "finalizes" in low or "finalized" in low or "final award" in low:
        stage = "최종 확정"
    elif "letter of intent" in low or "letters of intent" in low:
        stage = "의향서"
    elif item.get("stage") == "공식":
        stage = "공식자료"
    else:
        stage = "보도"
    return companies, stage, low


def korean_title(item, companies, stage):
    low = f"{item.get('title','')} {item.get('article_text','')}".lower()
    if all(x in low for x in ["d-wave", "rigetti", "quantinuum"]) and ("300 million" in low or "$300 million" in low):
        return "미 정부, D-Wave·Rigetti·Quantinuum 양자컴퓨팅 지원 최종 확정"
    if "d-wave" in low and ("100 million" in low or "$100m" in low):
        return "D-Wave, 미 상무부와 최대 1억달러 CHIPS 지원 최종 계약"
    if "rigetti" in low and ("100 million" in low or "$100m" in low):
        return "Rigetti, 미 상무부와 1억달러 양자컴퓨팅 연구개발 지원 최종 계약"
    if "quantinuum" in low and "100 million" in low:
        return "Quantinuum, 미 상무부와 1억달러 CHIPS 연구개발 지원 최종 계약"
    translated = monitor.translate_piece(strip_source_suffix(item.get("title", "")))
    return monitor.compact_korean(translated or strip_source_suffix(item.get("title", "")), 100)


def investment_bullets(item, companies, stage, rate, fx_date):
    raw = f"{item.get('title','')} {item.get('summary','')} {item.get('article_text','')}"
    low = raw.lower()
    bullets = [f"단계: {stage}"]

    # 2026-09-08 세 회사 최종계약은 2026-05-21 LOI에서 최종 계약으로 넘어간 것이 핵심이다.
    if any(c in companies for c in ["D-Wave", "Rigetti", "Quantinuum"]):
        if stage == "최종 확정":
            bullets.append("달라진 점: 2026-05-21 CHIPS R&D 의향서 단계에서 2026-09-08 최종 계약 단계로 진전")

    if all(c in companies for c in ["D-Wave", "Rigetti", "Quantinuum"]) or ("300 million" in low and "quant" in low):
        if rate:
            bullets.append(
                f"금액: D-Wave 최대 1억달러({krw_text(100, rate)}), Rigetti 1억달러({krw_text(100, rate)}), Quantinuum 1억달러({krw_text(100, rate)}) — 합산 최대 3억달러({krw_text(300, rate)})"
            )
        else:
            bullets.append("금액: D-Wave 최대 1억달러, Rigetti 1억달러, Quantinuum 1억달러 — 합산 최대 3억달러")
        bullets.append("지분 구조: CHIPS R&D 지원 조건으로 미 상무부가 각 회사의 소수·비지배 지분을 확보하는 구조")
    elif "d-wave" in low and ("100 million" in low or "$100m" in low):
        bullets.append(f"금액: 최대 1억달러({krw_text(100, rate)})" if rate else "금액: 최대 1억달러")
    elif ("rigetti" in low or "quantinuum" in low) and ("100 million" in low or "$100m" in low):
        bullets.append(f"금액: 1억달러({krw_text(100, rate)})" if rate else "금액: 1억달러")

    if "d-wave" in low:
        bullets.append("개발 병목: 초전도 어닐링·게이트형 시스템의 오류율·코히런스·유전체·계면제어·고밀도 패키징 개선")
    if "rigetti" in low:
        bullets.append("개발 병목: 초전도 양자컴퓨터 확장을 위한 판독전자 소형화·극저온 시스템·상호연결 구조 개선")
    if "quantinuum" in low:
        bullets.append("개발 병목: 트랩이온 확장을 위한 저손실 집적광학·레이저·광부품·300mm 이온트랩/제어전자 공급망 확대")
        if "globalfoundries" in low or "monarch" in low:
            bullets.append("협력관계: GlobalFoundries가 차세대 이온트랩·제어전자를 300mm 공정으로 지원하고 Monarch Quantum이 레이저·광부품을 개발")

    if "globalfoundries" in low and ("375 million" in low or "$375" in low):
        bullets.append(f"계획 금액: 3억7,500만달러({krw_text(375, rate)}) 규모 양자 파운드리 지원" if rate else "계획 금액: 3억7,500만달러 규모 양자 파운드리 지원")
    if re.search(r"\bibm\b", low) and ("1 billion" in low or "$1 billion" in low):
        bullets.append(f"계획 금액: 10억달러({krw_text(1000, rate)}) 규모 양자급 초전도 웨이퍼 파운드리 지원" if rate else "계획 금액: 10억달러 규모 양자급 초전도 웨이퍼 파운드리 지원")

    bullets.append("투자 관점: 정부 지분참여는 단순 연구보조금보다 정책 검증 강도가 높지만, 실제 재평가는 지급 마일스톤·지분율·기술 목표 달성·상용 고객 수주로 확인")
    bullets.append("다음 확인: 실제 지분율·주식 발행가격·지급 마일스톤·추가 6개 LOI 기업 최종계약·GlobalFoundries/IBM 파운드리 최종지원·상용 수주")

    # 6개로 압축
    return bullets[:6]


def build_message(item):
    item = fetch_article_meta(dict(item))
    companies, stage, low = classify_item(item)
    rate, fx_date = fx_usd_krw()
    title = korean_title(item, companies, stage)
    safe_url = html.escape(item["url"], quote=True)
    safe_source = html.escape(item.get("source") or "공식자료")
    bullets = investment_bullets(item, companies, stage, rate, fx_date)

    published = item.get("published_date") or ""
    date_line = f"공개일: <b>{html.escape(published)}</b>\n" if published else ""
    checked_line = f"확인시각: {datetime.now(KST).strftime('%Y-%m-%d %H:%M KST')}\n"
    bullet_text = "\n".join(f"• {html.escape(x)}" for x in bullets)
    fx_line = f"\n환산 기준: 1달러={rate:,.2f}원 · ECB {fx_date}" if rate and any("달러" in x for x in bullets) else ""

    return (
        "🚨 <b>미국 양자컴퓨팅 정부투자·지분 중요 변화</b>\n\n"
        f"<b>{html.escape(title)}</b>\n"
        f"출처: <a href=\"{safe_url}\">{safe_source}</a>\n"
        f"{date_line}{checked_line}\n"
        f"{bullet_text}"
        f"{html.escape(fx_line)}\n\n"
        f"<a href=\"{safe_url}\"><b>원문</b></a>"
    )


def send_telegram(text: str):
    token = (os.environ.get("QUANTUM_TELEGRAM_BOT_TOKEN") or os.environ.get("AI_REMOTE_TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (os.environ.get("QUANTUM_TELEGRAM_CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    if not token or not chat_id:
        raise RuntimeError("양자컴퓨팅 Telegram token/chat_id가 없습니다.")
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


def load_state():
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def bootstrap_item():
    # 현재 사용자가 요청한 2026-09-08 최종계약을 한 번만 기준 알림으로 보낸다.
    return {
        "source": "NIST·기업 공식자료 교차검증",
        "title": "U.S. Government Finalizes Quantum CHIPS Awards with D-Wave Rigetti Quantinuum for $300 Million",
        "url": "https://www.nist.gov/news-events/news/2026/05/department-commerce-announces-letters-intent-9-companies-2-billion",
        "summary": "D-Wave Rigetti Quantinuum finalized CHIPS R&D agreements on September 8 2026. D-Wave up to $100 million; Rigetti $100 million; Quantinuum $100 million. Minority non-controlling equity stakes are a condition of the CHIPS R&D portfolio.",
        "article_text": "D-Wave Rigetti Quantinuum $300 million definitive agreement finalizes September 8 2026 GlobalFoundries Monarch Quantum minority non-controlling equity stake",
        "stage": "공식",
        "published_date": "2026-09-08",
    }


def main():
    old = load_state()
    new = dict(old)
    alerts = []

    for source in OFFICIAL_SOURCES:
        name = source["name"]
        try:
            current = fetch_html_source(source)
            print(f"[QUANTUM OK] {name}: {len(current)}개")
        except Exception as e:
            print(f"[QUANTUM WARN] {name}: {e}")
            continue
        prev = set((old.get("sources") or {}).get(name, []))
        cur = set(current.keys())
        if old.get("initialized"):
            for url in sorted(cur - prev):
                alerts.append(current[url])
        new.setdefault("sources", {})[name] = sorted(prev | cur)[-600:]

    for source in NEWS_RSS:
        name = source["name"] + "::" + hashlib.sha1(source["url"].encode()).hexdigest()[:8]
        try:
            current = fetch_news_rss(source)
            print(f"[QUANTUM OK] {name}: {len(current)}개")
        except Exception as e:
            print(f"[QUANTUM WARN] {name}: {e}")
            continue
        prev = set((old.get("sources") or {}).get(name, []))
        cur = set(current.keys())
        if old.get("initialized"):
            for url in sorted(cur - prev):
                alerts.append(current[url])
        new.setdefault("sources", {})[name] = sorted(prev | cur)[-600:]

    token = (os.environ.get("QUANTUM_TELEGRAM_BOT_TOKEN") or os.environ.get("AI_REMOTE_TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (os.environ.get("QUANTUM_TELEGRAM_CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID") or "").strip()

    # 첫 실행은 현재 확정 사안을 딱 1회만 보내고 나머지는 기준선으로 저장한다.
    if not old.get("initialized"):
        if token and chat_id:
            send_telegram(build_message(bootstrap_item()))
            print("[QUANTUM BOOTSTRAP SENT] 2026-09-08 3사 최종계약 기준 알림")
            new["bootstrap_sent"] = True
        else:
            print("[QUANTUM PENDING] Telegram Secret이 없어 첫 알림 보류")
            return
        new["initialized"] = True
        save_state(new)
        return

    # URL이 다른 동일 기사 재게시를 줄이기 위한 간단한 의미 중복 제거.
    seen_semantic = set()
    filtered = []
    for item in alerts:
        key_text = re.sub(r"[^a-z0-9]+", " ", strip_source_suffix(item.get("title", "")).lower()).strip()
        key = " ".join(key_text.split()[:14])
        if key and key in seen_semantic:
            continue
        seen_semantic.add(key)
        filtered.append(item)

    if filtered and (not token or not chat_id):
        print("[QUANTUM PENDING] 새 변화가 있으나 Telegram Secret이 없어 상태를 갱신하지 않음")
        return

    sent = 0
    for item in filtered[:8]:
        try:
            send_telegram(build_message(item))
            sent += 1
            print(f"[QUANTUM SENT] {item['source']} - {item['title']}")
        except Exception as e:
            print(f"[QUANTUM SEND FAIL] {e}")
            return

    save_state(new)
    print(f"[QUANTUM DONE] 신규 알림 {sent}건")


if __name__ == "__main__":
    main()
