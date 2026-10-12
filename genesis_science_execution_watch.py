#!/usr/bin/env python3
"""Official-only Genesis Mission execution alerts, independent of quantum sales alerts.

Principles: primary document date, stage-change evidence, no cash/credit conflation,
one semantic event per documented step, delivery receipt before seen-state mutation.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import html
import json
import os
import re
import tempfile
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parent
STATE=ROOT/"genesis_science_execution_state.json"
DELIVERY=ROOT/"genesis_science_execution_delivery.json"
KST=ZoneInfo("Asia/Seoul")
UA={"User-Agent":"Mozilla/5.0 (compatible; GenesisScienceExecutionWatch/1.0)"}
MAX_AGE_DAYS=14
OFFICIAL_HOSTS=("nvidianews.nvidia.com","investor.nvidia.com","energy.gov","whitehouse.gov")
BASELINE={
    "nvidia-science-commitment:2026-10-08": "NVIDIA 10억달러/5년 산업계 자원 약정",
    "genesis-industry-commitments:2026-10-08": "백악관 산업계 총 24억달러 자원·연산 사용권 약정, NVIDIA 10억달러 포함",
    "doe-genesis-phase2-awards:2026-10-08": "DOE 2단계 신규 12개 과제, 총 1억5,900만달러 선정",
}
DIRECT_BASELINE_SOURCES={
    "https://nvidianews.nvidia.com/news/nvidia-commits-1-billion-to-advance-us-science-over-the-next-five-years":"nvidia-science-commitment:2026-10-08",
    "https://investor.nvidia.com/news/press-release-details/2026/NVIDIA-Commits-1-Billion-to-Advance-US-Science-Over-the-Next-Five-Years/default.aspx":"nvidia-science-commitment:2026-10-08",
    "https://www.whitehouse.gov/fact-sheets/2026/10/fact-sheet-trump-administration-announces-the-most-ambitious-set-of-science-initiatives-this-century/":"genesis-industry-commitments:2026-10-08",
    "https://www.energy.gov/articles/energy-department-announces-new-genesis-mission-awards-advance-super-intelligence-science":"doe-genesis-phase2-awards:2026-10-08",
}
SEARCHES=(
    'site:nvidianews.nvidia.com/news "Genesis Mission" (award OR contract OR recipient OR delivery OR deployment)',
    'site:investor.nvidia.com/news "Genesis Mission" (agreement OR contract OR delivered OR award)',
    'site:energy.gov/articles "Genesis Mission" (award OR selected OR grant OR funding OR procurement)',
    'site:whitehouse.gov/fact-sheets "Genesis Mission" (award OR funding OR procurement OR agreement)',
)
ACTION_GROUPS=(
    ("실제 지급·사용",("disbursed","disbursement","funds distributed","credits redeemed","compute credits used","delivered computing resources")),
    ("계약·발주",("signed contract","signs contract","contract awarded","awards contract","purchase order","procurement contract","purchase agreement","executed agreement")),
    ("신규 선정·자금 배정",("selected recipients","announces new awards","new project awards","awards funding","funding awarded","selected projects","new grants","grant awards")),
    ("시스템 납품·설치",("deployed systems","system installed","equipment delivered","system delivered","installed qpu","installed gpu","system accepted")),
    ("조건 변경·증액",("amends award","revised award","additional funding","increased award","expanded commitment","new allocation","new appropriations")),
)
# Mark the actual technical bottleneck but do not infer a supplier contract.
TECH_AREAS=(
    ("양자컴퓨팅",("quantum","qubit","error correction","qpu","cuda-q")),
    ("핵융합",("fusion","plasma","tokamak","tritium")),
    ("반도체·가속기",("microelectronics","accelerator design","semiconductor","microchip")),
    ("연구용 연산 인프라",("supercomputer","cloud","compute credits","gpu","argonne","los alamos")),
)
KNOWN_PROJECTS=("axess","asqc","ai4hpc","moat-core","gridfm","prometheus")
NAMED_ENTITIES=("argonne","los alamos","fermilab","harvard","oak ridge","lawrence berkeley","northeastern","mit","princeton","brookhaven","university","laboratory","nvidia","amd","micron","google","aws","crusoe","openai")

def clean(value):
    raw=str(value or "")
    if "<" not in raw and ">" not in raw:
        return re.sub(r"\s+"," ",html.unescape(raw)).strip()
    return re.sub(r"\s+"," ",BeautifulSoup(raw,"html.parser").get_text(" ",strip=True)).strip()

def canonical(url):
    u=urllib.parse.urlsplit(str(url or ""))
    return urllib.parse.urlunsplit((u.scheme.lower(),u.netloc.lower(),u.path.rstrip("/"),"",""))

def official_host(url):
    host=(urllib.parse.urlsplit(url).hostname or "").lower()
    return any(host==h or host.endswith("."+h) for h in OFFICIAL_HOSTS)

def now_kst(): return dt.datetime.now(KST)

def get(url,timeout=18):
    response=requests.get(url,headers=UA,timeout=timeout)
    response.raise_for_status()
    return response.text

def primary_date(soup):
    for prop in ("article:published_time","datePublished","datepublished","pubdate","publish-date"):
        tag=soup.find("meta",attrs={"property":prop}) or soup.find("meta",attrs={"name":prop})
        if tag and tag.get("content"):
            parsed=date_parse(tag["content"])
            if parsed:return parsed
    for tag in soup.find_all("time",limit=5):
        parsed=date_parse(tag.get("datetime") or tag.get_text(" ",strip=True))
        if parsed:return parsed
    # Structured data may carry datePublished even if a site omits a <time>.
    for script in soup.find_all("script",attrs={"type":"application/ld+json"},limit=12):
        txt=script.string or script.get_text()
        match=re.search(r'"datePublished"\s*:\s*"([^"]+)"',txt,re.I)
        if match:
            parsed=date_parse(match.group(1))
            if parsed:return parsed
    h=soup.find("h1")
    if h:
        near=clean(str(h.parent))[:900]
        match=re.search(r'\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2},\s+20\d{2}\b',near,re.I)
        if match:return date_parse(match.group())
    # Release pages sometimes keep the publication day near the article lead,
    # rather than in meta tags or the h1 parent.
    for area in (soup.find("article"),soup.find("main")):
        if area:
            lead=clean(str(area))[:1800]
            match=re.search(r'\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2},\s+20\d{2}\b',lead,re.I)
            if match:return date_parse(match.group())
    return None

def date_parse(value):
    s=str(value or "").strip()
    if not s:return None
    m=re.search(r"\b(20\d{2})-(\d{2})-(\d{2})",s)
    if m:
        try:return dt.date(int(m.group(1)),int(m.group(2)),int(m.group(3)))
        except ValueError:return None
    from email.utils import parsedate_to_datetime
    try:return parsedate_to_datetime(s).date()
    except Exception:pass
    m=re.search(r"([A-Za-z]+)\s+(\d{1,2}),\s+(20\d{2})",s)
    if m:
        for fmt in ("%B %d, %Y","%b %d, %Y"):
            try:return dt.datetime.strptime(m.group(0),fmt).date()
            except ValueError:pass
    return None

def article(url):
    if not official_host(url):return None
    soup=BeautifulSoup(get(url),"html.parser")
    h=soup.find("h1")
    title=clean(str(h)) if h else ""
    published=primary_date(soup)
    area=soup.find("article") or soup.find("main") or soup
    clone=BeautifulSoup(str(area),"html.parser")
    for elt in clone.select("script,style,nav,footer,aside,header,form"):
        elt.decompose()
    body=clean(str(clone))[:15000]
    # An exact, independently verified 8 Oct NVIDIA release may lack parseable
    # publication metadata in one of its official site templates. Never apply
    # this exception to new or unknown articles.
    is_known_nvidia_release=(
        canonical(url) in {canonical(u) for u in DIRECT_BASELINE_SOURCES if "nvidia.com" in u}
        and "nvidia commits $1 billion to advance us science over the next five years" in title.lower()
    )
    if published is None and is_known_nvidia_release and len(body)>=150:
        published=dt.date(2026,10,8)
    if not title or not published or len(body)<150:
        raise ValueError(f"원문검증 실패: title={title[:70]!r} published={published!r} body_chars={len(body)}")
    return {"url":canonical(url),"title":title,"published":published.isoformat(),"body":body}

def is_baseline(item):
    url=canonical(item["url"])
    if url in {canonical(source_url) for source_url in DIRECT_BASELINE_SOURCES}:return True
    t=item["title"].lower()
    return (
        ("nvidia" in t and ("$1 billion" in t or "1 billion" in t) and ("science" in t or "five years" in t) and any(x in t for x in ("commit","invest","pledge")))
        or ("most ambitious set of science initiatives" in t and "trump" in t)
        or ("genesis mission awards" in t and "energy department announces" in t and "2026-10-08"==item["published"])
    )

def classification(item):
    """Only new primary-source executions, not October 8 press release rewrites."""
    if not official_host(item["url"]) or is_baseline(item):return None
    day=date_parse(item["published"])
    if day is None or day<=dt.date(2026,10,8):return None
    if (now_kst().date()-day).days>MAX_AGE_DAYS or day>now_kst().date():return None
    title=clean(item["title"]).lower()
    body=clean(item["body"]).lower()
    if not ("genesis mission" in title or ("genesis mission" in body and ("nvidia" in title or "quantum genesis" in title))):return None
    # Broad summit recaps or pledge repetitions are never execution milestones.
    if any(x in title for x in ("summit","pledge","commitment to advance us science","over the next five years","science: a new golden age")):
        return None
    selected=None
    for stage,terms in ACTION_GROUPS:
        if any(term in title for term in terms):
            selected=stage
            break
    if selected is None:return None
    # Every alert must identify a real recipient/project and a concrete execution action.
    specific=any(x in body for x in NAMED_ENTITIES) and any(x in body for x in ("project","research","facility","contract","award","resource","computing"))
    if not specific:return None
    subject="NVIDIA" if "nvidia" in title else "미 에너지부" if "energy.gov" in item["url"] else "백악관" if "whitehouse.gov" in item["url"] else "미국 연구기관"
    areas=[name for name,terms in TECH_AREAS if any(x in title+" "+body[:3500] for x in terms)]
    # Repeated media headlines from a new URL use the same event if project,
    # stage and original publication title have not changed.
    project=next((p for p in KNOWN_PROJECTS if re.search(r"\b"+re.escape(p)+r"\b",title)),None)
    if not project:
        norm=re.sub(r"[^a-z0-9 ]"," ",title)
        norm=re.sub(r"\b(nvidia|energy department|doe|white house|genesis mission|announces|new|to|the|and)\b"," ",norm)
        project=hashlib.sha256(" ".join(norm.split()).encode()).hexdigest()[:14]
    key=f"genesis-execution:v1:{subject}:{selected}:{project}"
    amount_match=re.search(r"\$([\d,.]+)\s*(billion|million|trillion)\b",title,re.I)
    amount=f"${amount_match.group(1)} {amount_match.group(2)}" if amount_match else "계약·과제별 금액 미공개"
    return {"key":key,"stage":selected,"subject":subject,"area":"·".join(areas[:3]) if areas else "과학 연구 인프라","amount":amount,"source":item}

def initial_state():
    return {"schema":1,"seen":{key:{"kind":"기준 사건","published":"2026-10-08"} for key in BASELINE},"uncertain":{},"last_checked_kst":"","source_health":{}}

def load_state():
    try:
        state=json.loads(STATE.read_text(encoding="utf-8"))
        if not isinstance(state,dict):raise ValueError
        for k,default in initial_state().items():
            state.setdefault(k,default)
        return state
    except Exception:return initial_state()

def atomic_write(path,obj):
    fd,tmp=tempfile.mkstemp(prefix=".genesis_",dir=str(path.parent))
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as f:
            json.dump(obj,f,ensure_ascii=False,sort_keys=True,indent=2)
            f.write("\n")
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)

def google_url(query):
    return "https://news.google.com/rss/search?"+urllib.parse.urlencode({"q":query+" when:7d","hl":"en-US","gl":"US","ceid":"US:en"})

def fetch_nvidia_press_rss():
    """Parse an NVIDIA-hosted RSS feed; never promote a third-party feed as primary."""
    feed_url="https://nvidianews.nvidia.com/cats/press_release.xml"
    root=ET.fromstring(get(feed_url,timeout=18))
    rows={}
    for node in root.findall("./channel/item")[:40]:
        title=clean(node.findtext("title"))
        link=canonical(clean(node.findtext("link")))
        raw_date=clean(node.findtext("pubDate"))
        from email.utils import parsedate_to_datetime
        try:published=parsedate_to_datetime(raw_date).date()
        except Exception:published=date_parse(raw_date)
        if not title or not link or not published:
            continue
        if (urllib.parse.urlsplit(link).hostname or "").lower() not in ("nvidianews.nvidia.com","www.nvidianews.nvidia.com"):
            continue
        summary=clean(node.findtext("description"))
        for child in node:
            if child.tag.split("}")[-1].lower() in ("encoded","content"):
                summary=clean(" ".join((summary,child.text or "")))
        rows[link]={"url":link,"title":title,"published":published.isoformat(),"body":summary,"source_mode":"NVIDIA 공식 RSS"}
    return rows

def discover():
    sources=set(DIRECT_BASELINE_SOURCES)
    diagnostics={"baseline_verified":0,"nvidia_verified":0,"doe_verified":0,"whitehouse_verified":0,"feed_items":0,"decoded_official":0,"nvidia_rss_count":0,"source_errors":[]}
    rss_documents={}
    try:
        rss_documents=fetch_nvidia_press_rss()
        diagnostics["nvidia_rss_count"]=len(rss_documents)
        for item in rss_documents.values():
            if "nvidia commits $1 billion to advance us science over the next five years" in item["title"].lower() and item["published"]=="2026-10-08":
                diagnostics["nvidia_verified"]+=1
                diagnostics["baseline_verified"]+=1
            if "genesis mission" in item["title"].lower() or "quantum" in item["title"].lower():
                sources.add(item["url"])
    except Exception as exc:
        diagnostics["source_errors"].append(f"NVIDIA 공식 RSS 실패: {type(exc).__name__}: {exc}")

    for url in list(DIRECT_BASELINE_SOURCES):
        try:
            item=article(url)
            if item and is_baseline(item):
                diagnostics["baseline_verified"]+=1
                host=(urllib.parse.urlsplit(url).hostname or "").lower()
                family="nvidia" if "nvidia.com" in host else "doe" if "energy.gov" in host else "whitehouse" if "whitehouse.gov" in host else ""
                if family:diagnostics[family+"_verified"]+=1
        except Exception as exc:
            diagnostics["source_errors"].append(f"공식 기준원문 실패 {url}: {type(exc).__name__}")
    try:
        from googlenewsdecoder import gnewsdecoder
    except ImportError:
        gnewsdecoder=None
    for query in SEARCHES:
        try:
            root=ET.fromstring(get(google_url(query),timeout=18))
        except Exception as exc:
            diagnostics["source_errors"].append(f"공식 뉴스 검색 실패 {query[:55]}: {type(exc).__name__}")
            continue
        for row in root.findall("./channel/item")[:12]:
            url=clean(row.findtext("link"))
            title=clean(row.findtext("title"))
            if not url or not title:continue
            diagnostics["feed_items"]+=1
            if not gnewsdecoder:
                diagnostics["source_errors"].append("뉴스 원문 주소 복원 라이브러리 누락")
                continue
            # Never promote a Google News wrapper or its RSS date to official evidence.
            try:
                decoded=gnewsdecoder(url,interval=0)
                direct=(decoded.get("decoded_url") or "") if decoded.get("status") else ""
            except Exception:
                direct=""
            if direct and official_host(direct):
                diagnostics["decoded_official"]+=1
                sources.add(canonical(direct))
    return sorted(sources),diagnostics,rss_documents

def quote_fx():
    try:
        res=requests.get("https://api.frankfurter.app/latest",params={"from":"USD","to":"KRW"},timeout=12)
        res.raise_for_status()
        v=res.json()
        return float(v["rates"]["KRW"]),str(v["date"])
    except Exception:return None,None

def won(amount,rate):
    if rate is None:return "원화 환산 확인 불가"
    m=re.fullmatch(r"\$([\d,.]+)\s*(billion|million|trillion)",amount,re.I)
    if not m:return "원화 환산 대상 금액 미공개"
    num=float(m.group(1).replace(",",""))
    dollars=num*{"million":1e6,"billion":1e9,"trillion":1e12}[m.group(2).lower()]
    eok=dollars*rate/1e8
    jo=int(eok//10000)
    return f"약 {jo}조{round(eok-jo*10000):,}억원" if jo else f"약 {round(eok):,}억원"

def render(event,fx=None,fx_day=None):
    src=event["source"]
    esc=lambda x: html.escape(str(x),quote=True)
    v=event["amount"]
    money=f"{v} ({won(v,fx)})" if v.startswith("$") else v
    label=event["subject"]+" Genesis Mission "+event["stage"]
    return ("<b>[Genesis Mission 과학·양자 투자 집행]</b>\n"+
       f"<b>{esc(label)}</b>\n"+
       f"• 공식 문서 발표일: {esc(src['published'])}\n"+
       f"• 사업 단계: {esc(event['stage'])}\n"+
       f"• 해당 기술: {esc(event['area'])}\n"+
       f"• 계약·과제 금액: {esc(money)}\n"+
       "• 구분: NVIDIA 10억달러(5년) 기업 약정은 Genesis 산업계 총 24억달러에 포함. DOE 기존 2단계 과제 1억5,900만달러는 별도 사업. 확정 수혜기업 매출과 혼동 금지.\n"+
       "• 다음 확인: 수혜기관·계약서·납품량·사용권/연산 크레딧 실제 사용·검수·현금집행·유지보수\n"+
       "• 실패 경로: 지원자원 제공 지연, 데이터센터 전력·냉각 병목, QPU/가속기 신뢰성·계약 미체결\n"+
       (f"• 환율 근거: ECB 기준 {esc(fx_day)} · 1달러={fx:,.2f}원\n" if fx else "")+
       f"• <a href=\"{esc(src['url'])}\">공식 원문</a>")

def send(message):
    token=os.getenv("TELEGRAM_BOT_TOKEN","").strip()
    chat=os.getenv("TELEGRAM_CHAT_ID","").strip()
    if not token or not chat:raise RuntimeError("Telegram 봇·채팅 설정 누락")
    if len(message.encode("utf-16-le"))//2>4096:
        raise RuntimeError("Telegram 메시지 한도 초과")
    resp=requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id":chat,"text":message,"parse_mode":"HTML","link_preview_options":{"is_disabled":True}},
        timeout=25)
    resp.raise_for_status()
    payload=resp.json()
    mid=(payload.get("result") or {}).get("message_id")
    if not payload.get("ok") or not isinstance(mid,int):
        raise RuntimeError("Telegram message_id 응답 누락")
    return mid

def main():
    state=load_state()
    sources,health,rss_documents=discover()
    candidates=[]
    baseline_urls={canonical(u) for u in DIRECT_BASELINE_SOURCES}
    for url in sources:
        if canonical(url) in baseline_urls:
            continue  # already checked by discover() using original page and date
        try:
            item=article(url)
        except Exception as exc:
            item=rss_documents.get(canonical(url))
            if not item or len(item.get("body",""))<150:
                health["source_errors"].append(f"원문검증 실패 {url}: {type(exc).__name__}")
                continue
            print(f"genesis_primary_rss_fallback={url}")
        if item and not is_baseline(item):
            event=classification(item)
            if event and event["key"] not in state["seen"] and event["key"] not in state["uncertain"]:
                candidates.append(event)
    state["last_checked_kst"]=now_kst().isoformat(timespec="seconds")
    health["official_pages_checked"]=len(sources)
    health["new_candidates"]=len(candidates)
    state["source_health"]=health
    print("genesis_watch="+json.dumps(health,ensure_ascii=False))
    if health["baseline_verified"]==0:
        atomic_write(STATE,state)
        raise RuntimeError("핵심 공식 기준원문 3곳 모두 실패: 정상 감시로 처리 불가")
    fx,fx_day=quote_fx() if candidates else (None,None)
    receipts=[]
    for event in candidates[:3]:
        try:
            mid=send(render(event,fx,fx_day))
        except Exception as exc:
            # Only transport uncertainty (not a definite HTTP rejection or
            # missing token) risks an accepted-but-unacknowledged message.
            if isinstance(exc,(requests.exceptions.Timeout,requests.exceptions.ConnectionError)):
                state["uncertain"][event["key"]]={"reason":type(exc).__name__,"at_kst":now_kst().isoformat(timespec="seconds"),"official_url":event["source"]["url"]}
            atomic_write(STATE,state)
            raise
        receipt={"message_id":mid,"official_url":event["source"]["url"],"confirmed_at_kst":now_kst().isoformat(timespec="seconds")}
        state["seen"][event["key"]]=receipt
        receipts.append({"event_key":event["key"],**receipt})
        atomic_write(STATE,state)
        atomic_write(DELIVERY,{"status":"confirmed","receipts":receipts})
        print(f"genesis_telegram_confirmed event={event['key']} message_id={mid}")
    atomic_write(STATE,state)
    print(f"genesis_watch_done new={len(candidates)} sent={len(receipts)} uncertain={len(state['uncertain'])}")
    return 0

if __name__=="__main__":
    raise SystemExit(main())
