import hashlib
import html
import json
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

STATE_FILE = Path("quantum_commercial_tech_state.json")
TIMEOUT = 30
KST = ZoneInfo("Asia/Seoul")
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; Quantum-Commercial-Tech-Watch/1.0)"}

OFFICIAL_SOURCES = [
    ("IonQ 공식 뉴스", "https://www.ionq.com/news"),
    ("D-Wave 공식 뉴스", "https://www.dwavequantum.com/company/newsroom/"),
    ("Rigetti 공식 IR", "https://investors.rigetti.com/news-events/news-releases"),
    ("Quantinuum 공식 IR", "https://ir.quantinuum.com/news-events/press-releases"),
    ("IBM 연구·혁신 공식 뉴스", "https://newsroom.ibm.com/press-releases-research-and-innovation"),
    ("PsiQuantum 공식 뉴스", "https://www.psiquantum.com/news"),
    ("NVIDIA 공식 뉴스", "https://nvidianews.nvidia.com/"),
]

NEWS_RSS = [
    ("양자 상용화·기술검증 보도 감시",
     "https://news.google.com/rss/search?q=%28IonQ+OR+%22D-Wave%22+OR+Rigetti+OR+Quantinuum+OR+PsiQuantum+OR+QuEra+OR+Infleqtion+OR+Xanadu+OR+%22Atom+Computing%22%29+%28%22quantum+error+correction%22+OR+%22logical+qubit%22+OR+deployment+OR+selected+OR+order+OR+customer+OR+delivery+OR+NVIDIA+OR+supercomputer+OR+foundry%29&hl=en-US&gl=US&ceid=US:en"),
]

COMPANY_ALIASES = {
    "IonQ": ["ionq"],
    "D-Wave": ["d-wave", "dwave", "qbts"],
    "Rigetti": ["rigetti", "rgti"],
    "Quantinuum": ["quantinuum"],
    "PsiQuantum": ["psiquantum"],
    "IBM": ["ibm"],
    "QuEra": ["quera"],
    "Infleqtion": ["infleqtion"],
    "Xanadu": ["xanadu"],
    "Atom Computing": ["atom computing"],
}

MATERIAL_TERMS = [
    "quantum error correction", "error decoder", "real-time decoder", "real time decoder",
    "logical qubit", "logical qubits", "fault-tolerant", "fault tolerant", "fidelity",
    "selected", "deployment", "deploy", "installed", "install", "customer delivery",
    "customer deliveries", "order", "purchase", "supply", "contract", "first qpu",
    "on-premise", "on premise", "nvaqc", "nvqlink", "cuda-q", "supercomputer",
    "quantum processor", "qpu", "manufacturing", "fabricated", "foundry",
    "production", "first ions", "commercial order", "customer", "partner",
]

STRONG_TECH_TERMS = [
    "quantum error correction", "error decoder", "logical qubit", "logical qubits",
    "fault-tolerant", "fault tolerant", "fidelity", "megaquop", "million quantum operations",
]

COMMERCIAL_TERMS = [
    "selected", "deployment", "deploy", "installed", "install", "order", "purchase",
    "supply", "contract", "customer delivery", "customer deliveries", "first qpu",
    "on-premise", "on premise", "nvaqc", "nvqlink", "supercomputer",
]

EXCLUDE_TERMS = [
    "to present", "will present", "conference", "webinar", "fireside chat",
    "stock jumps", "stock rises", "shares soar", "why ionq stock", "best to buy",
    "price target", "analyst", "earnings call", "event participation",
]

def get(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        return r.text
    except Exception:
        r = requests.get("https://r.jina.ai/" + url, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        return r.text

def clean(text):
    return " ".join(BeautifulSoup(text or "", "html.parser").get_text(" ", strip=True).split())

def normalize_url(url):
    p = urlparse((url or "").strip())
    if not p.scheme or not p.netloc:
        return (url or "").strip()
    return f"{p.scheme}://{p.netloc}{p.path.rstrip('/')}"

def load_state():
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}

def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

def company_keys(text):
    low = (text or "").lower()
    return sorted(name for name, keys in COMPANY_ALIASES.items() if any(k in low for k in keys))

def material(text):
    low = (text or "").lower()
    if any(x in low for x in EXCLUDE_TERMS):
        return False
    if not company_keys(low):
        return False
    has_strong = any(x in low for x in STRONG_TECH_TERMS)
    has_commercial = any(x in low for x in COMMERCIAL_TERMS)
    # 논문/연구는 수치 성과나 오류정정·논리큐비트처럼 제품 로드맵을 바꾸는 경우만 허용.
    has_quant = bool(re.search(r"\b\d+(?:\.\d+)?\s*(?:%|qubits?|logical|million|operations?|x\b)", low))
    return has_commercial or (has_strong and has_quant)

def fetch_official(name, url):
    raw = get(url)
    soup = BeautifulSoup(raw, "html.parser")
    host = urlparse(url).netloc.lower().removeprefix("www.")
    items = {}
    for a in soup.find_all("a", href=True):
        title = clean(a.get_text(" ", strip=True))
        if len(title) < 12:
            continue
        href = normalize_url(urljoin(url, a["href"]))
        h = urlparse(href).netloc.lower().removeprefix("www.")
        if not h.endswith(host):
            continue
        text = f"{title} {href}"
        if material(text):
            items[href] = {"source": name, "title": title, "url": href, "published": "", "summary": ""}
    return items

def node_text(node, tag):
    for child in list(node):
        if child.tag.split("}")[-1].lower() == tag:
            return clean("".join(child.itertext()))
    return ""

def fetch_rss(name, url):
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    root = ET.fromstring(r.text)
    out = {}
    for entry in [x for x in root.iter() if x.tag.split("}")[-1].lower() == "item"]:
        title = node_text(entry, "title")
        link = node_text(entry, "link")
        desc = node_text(entry, "description")
        pub = node_text(entry, "pubdate")
        src = node_text(entry, "source")
        text = f"{title} {desc}"
        if not material(text):
            continue
        published = ""
        if pub:
            try:
                dt = parsedate_to_datetime(pub)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                published = dt.astimezone(KST).strftime("%Y-%m-%d %H:%M")
            except Exception:
                published = pub
        key = normalize_url(link)
        out[key] = {"source": src or name, "title": title, "url": link, "published": published, "summary": desc}
    return out

def article_text(item):
    try:
        raw = get(item["url"])
        txt = clean(raw)
        return txt[:14000]
    except Exception:
        return clean(item.get("summary", ""))

def event_type(text):
    low = text.lower()
    if any(x in low for x in ["quantum error correction", "error decoder", "logical qubit", "fault-tolerant", "fault tolerant"]):
        return "오류정정·내결함성"
    if any(x in low for x in ["selected", "deployment", "on-premise", "on premise", "customer delivery", "supply", "order", "contract"]):
        return "상용화·고객배치"
    if any(x in low for x in ["fabricated", "foundry", "manufacturing", "production", "first ions"]):
        return "제조·양산"
    return "전략협력·시스템통합"

def partner_key(text):
    low = text.lower()
    for name in ["nvidia", "fiu", "florida international university", "sdt", "aws", "amazon", "azure", "microsoft", "google", "darpa", "doe", "ornl", "skywater"]:
        if name in low:
            return name.replace("florida international university", "fiu")
    return "none"

def event_key(item, full_text):
    comps = company_keys(full_text) or ["sector"]
    kind = event_type(full_text)
    partner = partner_key(full_text)
    # 날짜보다 사건 구성요소를 우선해 재기사 날짜가 달라도 같은 사건으로 묶음.
    model = "superion256" if "superion 256" in full_text.lower() else "generic"
    return [f"{c}|{kind}|{partner}|{model}" for c in comps]

def ko_date(value):
    m = re.search(r"(20\d{2})-(\d{2})-(\d{2})", value or "")
    if not m:
        return value or ""
    return f"{int(m.group(1))}년 {int(m.group(2))}월 {int(m.group(3))}일"

def extract_official_date(text):
    patterns = [
        r"(September|October|November|December|August|July|June|May|April|March|February|January)\s+(\d{1,2}),\s+(2026)",
        r"(2026)-(\d{1,2})-(\d{1,2})",
    ]
    m = re.search(patterns[0], text, re.I)
    if m:
        try:
            dt = datetime.strptime(m.group(0), "%B %d, %Y")
            return dt.strftime("%Y-%m-%d")
        except Exception:
            pass
    m = re.search(patterns[1], text)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return ""

def summarize_ionq_nvidia_qec(full_text):
    low = full_text.lower()
    if "superion 256" in low and "nvidia" in low and ("nvaqc" in low or "accelerated quantum research center" in low):
        return [
            "단계: 고객·연구센터 현장 배치 확정 — 단순 업무협약이 아니라 IonQ QPU가 NVIDIA 연구센터에 실제 설치되는 단계",
            "배치: Superion 256이 NVIDIA Accelerated Quantum Research Center(NVAQC)의 첫 현장형 양자 프로세서로 선정",
            "시스템: NVIDIA GB200 NVL72와 NVQLink로 직접 연결하고 CUDA-Q가 양자·GPU 워크로드를 통합 제어",
            "일정: Superion 256은 현재 주문 가능, 첫 고객 인도와 NVAQC 설치는 2027년 예정",
            "금액: 계약금액·NVIDIA의 구매대금 여부는 공식 발표에서 공개되지 않음 — '판매 확정 금액'으로 계산하지 않음",
            "다음 확인: 실제 설치일·계약금액·후속 Superion 세대·NVAQC 추가 QPU·상용 고객 전환",
        ]
    if "real-time quantum error correction decoder" in low or "real-time quantum error decoder" in low:
        return [
            "단계: 오류정정 핵심 병목의 실시간 검증",
            "검증 숫자: 최대 408개 논리 큐비트, 88개 메모리 블록·매직팩토리, 3,150만회 이상 양자 연산 규모를 모사",
            "처리 구조: 범용 CPU 1개로 실시간 디코딩을 수행해 고전 제어 하드웨어의 확장 부담을 낮춤",
            "지연: 표준 잡음 조건에서 디코딩이 전체 연산시간에 더한 지연은 최소 0.02%",
            "의미: 물리 큐비트 확대만이 아니라 내결함성 시스템의 실시간 오류처리 병목을 실제 실행 경로에서 줄였다는 점이 핵심",
            "다음 확인: 실제 하드웨어 논리 큐비트 수·논리 오류율·반복 성공률·2027 Superion 배치에서의 실시간 QEC 통합",
        ]
    return []

def generic_bullets(item, full_text):
    kind = event_type(full_text)
    comps = "·".join(company_keys(full_text)) or "양자기업"
    bullets = [f"단계: {kind}", f"당사자: {comps}"]
    low = full_text.lower()
    if "nvidia" in low:
        bullets.append("협력관계: NVIDIA의 GPU·CUDA-Q·NVQLink 생태계와 QPU 직접 통합 여부를 확인")
    if re.search(r"\b\d+(?:\.\d+)?\s*%", low):
        nums = re.findall(r"\b\d+(?:\.\d+)?\s*%", full_text)[:3]
        if nums:
            bullets.append("핵심 수치: " + ", ".join(nums))
    bullets.append("투자 관점: 발표가 실제 고객 배치·계약·논리큐비트·오류율·제조물량으로 이어지는지 확인")
    bullets.append("다음 확인: 계약금액·설치일·수량·고객 실명·논리 오류율·양산/인도 일정")
    return bullets[:6]

def title_ko(item, full_text):
    low = full_text.lower()
    if "superion 256" in low and "nvidia" in low and ("nvaqc" in low or "accelerated quantum research center" in low):
        return "IonQ Superion 256, NVIDIA NVAQC 첫 현장형 QPU로 선정"
    if "real-time quantum error correction decoder" in low:
        return "IonQ, 실시간 양자오류정정 디코더 검증…최대 408 논리큐비트·3,150만회 연산 모사"
    t = item.get("title", "")
    # 자동 번역 실패 시 영어 제목을 그대로 보내지 않고 식별 가능한 고정 한국어 설명으로 대체.
    if re.search(r"[A-Za-z]{4,}", t) and not re.search(r"[가-힣]", t):
        return f"{'·'.join(company_keys(full_text)) or '양자기업'} {event_type(full_text)} 중요 변화"
    return t

def build_message(item):
    full = article_text(item)
    combined = f"{item.get('title','')} {item.get('summary','')} {full}"
    date = extract_official_date(full) or (item.get("published","").split(" ")[0] if item.get("published") else "")
    title = title_ko(item, combined)
    bullets = summarize_ionq_nvidia_qec(combined) or generic_bullets(item, combined)
    safe_url = html.escape(item["url"], quote=True)
    source = html.escape(item.get("source") or "공식자료")
    date_line = f"공식 발표일: <b>{html.escape(ko_date(date))}</b>\n" if date else ""
    return (
        "🚨 <b>양자컴퓨팅 상용화·기술검증 중요 변화</b>\n\n"
        f"<b>{html.escape(title)}</b>\n"
        f"출처: <a href=\"{safe_url}\">{source}</a>\n"
        f"{date_line}"
        f"확인시각: {datetime.now(KST).strftime('%Y-%m-%d %H:%M KST')}\n\n"
        + "\n".join(f"• {html.escape(x)}" for x in bullets)
        + f"\n\n<a href=\"{safe_url}\"><b>원문</b></a>"
    )

def send(text):
    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    if not token or not chat_id:
        raise RuntimeError("Telegram Secret 없음")
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text[:4096], "parse_mode": "HTML", "link_preview_options": {"is_disabled": True}},
        timeout=TIMEOUT,
    )
    r.raise_for_status()

def bootstrap_message():
    qec = "https://investors.ionq.com/news/news-details/2026/IonQ-Demonstrates-Industrys-First-End-to-End-Real-Time-Quantum-Error-Decoder/default.aspx"
    nv = "https://ionq.com/news/ionq-to-advance-quantum-supercomputing-by-bringing-first-qpu-to-nvidia-accelerated-quantum-research-center"
    return (
        "🚨 <b>양자컴퓨팅 상용화·기술검증 중요 변화</b>\n\n"
        "<b>IonQ, 실시간 오류정정 병목 개선 → NVIDIA NVAQC 첫 현장형 QPU 배치 확정</b>\n"
        f"출처: <a href=\"{qec}\">IonQ QEC 공식 발표</a> · <a href=\"{nv}\">IonQ·NVIDIA 배치 공식 발표</a>\n"
        "오류정정 발표일: <b>2026년 9월 22일</b>\n"
        "NVIDIA 배치 발표일: <b>2026년 9월 23일</b>\n"
        f"확인시각: {datetime.now(KST).strftime('%Y-%m-%d %H:%M KST')}\n\n"
        "• 오류정정: 범용 CPU 1개로 최대 408개 논리 큐비트·3,150만회 이상 연산 규모의 실시간 디코딩을 검증, 추가 지연 최소 0.02%\n"
        "• NVIDIA: Superion 256이 NVAQC의 첫 현장형 QPU로 선정돼 GB200 NVL72와 NVQLink로 직접 연결, CUDA-Q로 통합 제어\n"
        "• 일정: Superion 256은 현재 주문 가능하며 첫 고객 인도와 NVAQC 설치는 2027년 예정\n"
        "• 구분: 공식 발표는 'NVIDIA 연구센터 설치·배치'를 확인하지만 계약금액·NVIDIA 구매대금은 공개하지 않아 매출액은 미확정\n"
        "• 투자 의미: QEC 기술검증이 실제 대형 AI 인프라 현장 배치로 이어졌다는 점이 핵심이며, 연구논문 단독보다 상용화 검증 강도가 높아짐\n"
        "• 다음 확인: 실제 설치일·계약금액·논리 오류율·NVAQC 추가 QPU·Superion 후속 고객과 인도 물량\n\n"
        f"<a href=\"{nv}\"><b>원문</b></a>"
    )

def main():
    old = load_state()
    sources_state = dict(old.get("sources") or {})
    old_events = set(old.get("events") or [])
    new = dict(old)
    candidates = []

    for name, url in OFFICIAL_SOURCES:
        try:
            current = fetch_official(name, url)
            print(f"[QTECH OK] {name}: {len(current)}개")
        except Exception as e:
            print(f"[QTECH WARN] {name}: {e}")
            continue
        prev = set(sources_state.get(name, []))
        cur = set(current.keys())
        if old.get("initialized") and name in sources_state:
            for u in sorted(cur - prev):
                candidates.append(current[u])
        sources_state[name] = sorted(prev | cur)[-800:]

    for name, url in NEWS_RSS:
        keyname = name + "::" + hashlib.sha1(url.encode()).hexdigest()[:8]
        try:
            current = fetch_rss(name, url)
            print(f"[QTECH OK] {keyname}: {len(current)}개")
        except Exception as e:
            print(f"[QTECH WARN] {keyname}: {e}")
            continue
        prev = set(sources_state.get(keyname, []))
        cur = set(current.keys())
        if old.get("initialized") and keyname in sources_state:
            for u in sorted(cur - prev):
                candidates.append(current[u])
        sources_state[keyname] = sorted(prev | cur)[-1000:]

    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()

    if not old.get("initialized"):
        if not token or not chat_id:
            print("[QTECH PENDING] Telegram Secret 없음")
            return
        send(bootstrap_message())
        print("[QTECH BOOTSTRAP SENT] IonQ QEC + NVIDIA NVAQC")
        new["initialized"] = True
        new["bootstrap_sent"] = True
        # 현재 페이지 전체를 기준선으로 저장해 과거 보도 폭탄을 막는다.
        new["sources"] = sources_state
        new["events"] = sorted(old_events | {
            "IonQ|오류정정·내결함성|none|generic",
            "IonQ|상용화·고객배치|nvidia|superion256",
        })
        save_state(new)
        return

    alerts = []
    run_events = set()
    for item in candidates:
        full = article_text(item)
        combined = f"{item.get('title','')} {item.get('summary','')} {full}"
        if not material(combined):
            continue
        date = extract_official_date(full) or (item.get("published","").split(" ")[0] if item.get("published") else "")
        if date:
            try:
                d = datetime.strptime(date, "%Y-%m-%d").date()
                if (datetime.now(KST).date() - d).days > 5:
                    print(f"[QTECH STALE] {date} {item.get('title','')}")
                    continue
            except Exception:
                pass
        keys = event_key(item, combined)
        if any(k in old_events or k in run_events for k in keys):
            print(f"[QTECH DEDUPE] {item.get('title','')}")
            continue
        item["_event_keys"] = keys
        alerts.append(item)
        run_events.update(keys)

    if alerts and (not token or not chat_id):
        print("[QTECH PENDING] 새 사건 있으나 Telegram Secret 없음")
        return

    sent_events = set(old_events)
    sent = 0
    for item in alerts[:8]:
        send(build_message(item))
        sent += 1
        sent_events.update(item.get("_event_keys") or [])
        print(f"[QTECH SENT] {item.get('source')} - {item.get('title')}")

    new["initialized"] = True
    new["bootstrap_sent"] = bool(old.get("bootstrap_sent", True))
    new["sources"] = sources_state
    new["events"] = sorted(sent_events)[-2000:]
    save_state(new)
    print(f"[QTECH DONE] 신규 알림 {sent}건")

if __name__ == "__main__":
    main()
