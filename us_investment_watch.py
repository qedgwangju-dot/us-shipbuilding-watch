#!/usr/bin/env python3
from __future__ import annotations

import datetime as dt
import email.utils
import hashlib
import html
import json
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "us_investment_state.json"
ALERT = ROOT / "us_investment_alert.html"
KST = ZoneInfo("Asia/Seoul")
UTC = dt.timezone.utc
USER_AGENT = "Mozilla/5.0 KHS-US-Investment-Watch/1.1"
MAX_AGE_HOURS = 96

NAVER_USDKRW = "https://api.stock.naver.com/marketindex/exchange/FX_USDKRW/prices?page=1&pageSize=1"
FRANKFURTER_USDKRW = "https://api.frankfurter.dev/v2/rate/USD/KRW"

QUERIES = [
    '"대미투자" 엔시날 when:4d',
    '"한미전략투자" I-SPV OR SPV when:4d',
    '"대미투자" PPA OR EPC OR 가스터빈 when:4d',
    '"대미투자" 반도체 OR 삼성전자 OR SK하이닉스 when:4d',
    '"대미투자" 원전 OR AP1000 OR APR1400 when:4d',
    '"대미투자" "알래스카 LNG" when:4d',
    '"대미투자" 관세 OR 301조 OR 232조 when:4d',
]

TRUSTED = [
    "산업통상", "대한민국 정책브리핑", "대통령실", "연합뉴스", "뉴시스", "뉴스1",
    "이데일리", "헤럴드경제", "한국경제", "중앙일보", "매일경제", "Reuters", "Bloomberg",
]

MATERIAL = [
    "확정", "의결", "합의", "계약", "체결", "승인", "증액", "감액", "사업비", "투자액",
    "I-SPV", "SPV", "PPA", "EPC", "가스터빈", "발전기", "수주", "반도체", "원전", "LNG",
    "관세", "301조", "232조", "제외", "포함", "소유권", "의결권", "손실분담", "FID", "금융종결",
]

MARKET_ONLY = ["특징주", "급등", "강세", "관련주", "테마주"]
HARD_FACT = ["확정", "의결", "합의", "계약", "체결", "승인", "증액", "감액", "수주", "PPA", "I-SPV"]


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "ko-KR,ko;q=0.9"})
    with urllib.request.urlopen(req, timeout=20) as response:
        return response.read()


def load_state() -> dict:
    if not STATE.exists():
        return {"bootstrap_sent": False, "seen": {}, "recent_titles": []}
    try:
        state = json.loads(STATE.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            raise ValueError
    except Exception:
        return {"bootstrap_sent": False, "seen": {}, "recent_titles": []}
    state.setdefault("bootstrap_sent", False)
    state.setdefault("seen", {})
    state.setdefault("recent_titles", [])
    return state


def clean(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value or "")
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def get_usdkrw() -> tuple[float, str]:
    """Return USD/KRW. Prefer Naver market-index API, then a public daily-rate fallback."""
    try:
        data = json.loads(fetch(NAVER_USDKRW).decode("utf-8"))
        row = data[0] if isinstance(data, list) else data
        raw = row.get("closePrice") or row.get("close") or row.get("value")
        if raw is not None:
            rate = float(str(raw).replace(",", ""))
            if 500 <= rate <= 3000:
                return rate, "네이버 시장정보 API"
    except Exception:
        pass

    try:
        data = json.loads(fetch(FRANKFURTER_USDKRW).decode("utf-8"))
        rate = float(data.get("rate"))
        if 500 <= rate <= 3000:
            return rate, "Frankfurter 공개 환율 API"
    except Exception:
        pass

    raise RuntimeError("USD/KRW 환율을 확인하지 못해 원화 환산 알림을 만들 수 없습니다.")


def format_krw(won: float) -> str:
    """Format won in 조/억원. Round to the nearest 억원 for investment-sized values."""
    eok = int(round(won / 100_000_000))
    jo, rem_eok = divmod(eok, 10_000)
    if jo and rem_eok:
        return f"약 {jo:,}조 {rem_eok:,}억원"
    if jo:
        return f"약 {jo:,}조원"
    return f"약 {rem_eok:,}억원"


def usd_eok_krw(usd_eok: float, rate: float) -> str:
    return format_krw(usd_eok * 100_000_000 * rate)


def usd_billion_krw(usd_billion: float, rate: float) -> str:
    return format_krw(usd_billion * 1_000_000_000 * rate)


def extract_usd_conversions(text: str, rate: float) -> list[str]:
    """Extract common USD amount formats and return readable KRW conversions."""
    out: list[str] = []
    seen: set[str] = set()

    # Korean: 223억달러 / 2,000억 달러
    for match in re.finditer(r"(?<![\d.])([0-9][0-9,]*(?:\.[0-9]+)?)\s*억\s*달러", text, flags=re.I):
        raw = match.group(1)
        usd_eok = float(raw.replace(",", ""))
        label = f"{raw}억달러"
        item = f"{label} ≈ {usd_eok_krw(usd_eok, rate)}"
        if item not in seen:
            seen.add(item)
            out.append(item)

    # English: $22.3 billion / US$22.3bn / USD 500 million
    patterns = [
        r"(?:US\$|\$|USD\s*)([0-9][0-9,]*(?:\.[0-9]+)?)\s*(trillion|billion|million|tn|bn|mn)\b",
        r"([0-9][0-9,]*(?:\.[0-9]+)?)\s*(trillion|billion|million|tn|bn|mn)\s*(?:US\s*)?dollars?\b",
    ]
    multipliers = {"trillion": 1000.0, "tn": 1000.0, "billion": 1.0, "bn": 1.0, "million": 0.001, "mn": 0.001}
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.I):
            raw = match.group(1)
            unit = match.group(2).lower()
            billion = float(raw.replace(",", "")) * multipliers[unit]
            label = match.group(0).strip()
            item = f"{label} ≈ {usd_billion_krw(billion, rate)}"
            if item not in seen:
                seen.add(item)
                out.append(item)
    return out


def google_news_url(query: str) -> str:
    return "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": query, "hl": "ko", "gl": "KR", "ceid": "KR:ko"}
    )


def normalize_title(value: str) -> str:
    value = value.lower()
    value = re.sub(r"\[[^\]]+\]|\([^)]*\)", " ", value)
    value = re.sub(r"[^0-9a-z가-힣]+", " ", value)
    stop = {"단독", "속보", "대미투자", "정부", "미국", "한국", "한미", "관련", "보도"}
    return " ".join(w for w in value.split() if len(w) >= 2 and w not in stop)


def similarity(a: str, b: str) -> float:
    sa = set(normalize_title(a).split())
    sb = set(normalize_title(b).split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def tags_for(title: str) -> list[str]:
    low = title.lower()
    rules = [
        ("1호·엔시날", ["엔시날", "encinal", "1호"]),
        ("투자구조", ["i-spv", "spv", "소유권", "의결권", "지분", "손실분담"]),
        ("전력판매", ["ppa", "ercot", "전력판매", "오프그리드"]),
        ("한국기업 수주", ["epc", "가스터빈", "발전기", "수주"]),
        ("반도체", ["반도체", "메모리", "삼성전자", "sk하이닉스"]),
        ("원전", ["원전", "ap1000", "apr1400", "웨스팅하우스"]),
        ("알래스카 LNG", ["알래스카", "alaska lng"]),
        ("관세", ["301조", "232조", "section 301", "section 232", "관세"]),
        ("사업비", ["사업비", "증액", "감액", "투자액"]),
    ]
    out = []
    for tag, terms in rules:
        if any(term in low for term in terms):
            out.append(tag)
    return out or ["대미투자"]


def meaning(tags: list[str]) -> str:
    if "투자구조" in tags:
        return "지분·의결권·손실분담이 한국의 실제 투자 회수액과 위험부담을 바꿉니다."
    if "전력판매" in tags:
        return "PPA 고객·계약 GW·기간·가격이 잠기면 발전소 현금흐름을 계산할 수 있습니다."
    if "한국기업 수주" in tags:
        return "EPC·가스터빈 본계약부터 국내 기업의 수주잔고와 실제 매출로 연결됩니다."
    if "반도체" in tags:
        return "미국 팹이 구체화되면 관세 우대와 국내 설비투자·현금흐름 분산을 함께 봐야 합니다."
    if "원전" in tags:
        return "AP1000·APR1400 선택과 주도권에 따라 한국의 시공·기자재·운영 몫이 달라집니다."
    if "알래스카 LNG" in tags:
        return "장기 구매계약·세제·FID·금융종결이 실제 착공 여부를 결정합니다."
    if "관세" in tags:
        return "관세 변화는 한국 수출기업의 마진·할인율과 대미 협상력을 바꿀 수 있습니다."
    if "사업비" in tags:
        return "총사업비가 늘면 투자수익률이 낮아질 수 있어 증액 사유와 초과비용 부담주체가 핵심입니다."
    return "프로젝트의 확정도와 집행 시간표가 한 단계 바뀐 신호입니다."


def next_check(tags: list[str], usdkrw: float) -> str:
    semi_package = f"2,000억달러({usd_eok_krw(2000, usdkrw)})"
    mapping = {
        "1호·엔시날": "정부 최종 발표·국회 절차",
        "투자구조": "I-SPV 원문·지분·의결권·초과비용/손실분담",
        "전력판매": "PPA 고객·계약 GW·기간·MWh당 가격",
        "한국기업 수주": "EPC·가스터빈·발전기 본계약과 금액·대수",
        "반도체": f"{semi_package} 포함 여부·회사/공정/지역·관세 우대",
        "원전": "AP1000/APR1400·한수원 주도권·한국 기자재 물량",
        "알래스카 LNG": "구속력 있는 구매계약·세제·FID·금융종결",
        "관세": "USTR 공식 문서·기존 15% 합의와 중첩 여부",
        "사업비": "증액 세부내역·고정가격 EPC·예비비·추가 증액 상한",
    }
    items = []
    for tag in tags:
        value = mapping.get(tag)
        if value and value not in items:
            items.append(value)
    return " · ".join(items[:4]) or "공식 발표·계약금액·매출 인식 시점"


def rss_items(now: dt.datetime) -> list[dict]:
    rows: list[dict] = []
    for query in QUERIES:
        try:
            root = ET.fromstring(fetch(google_news_url(query)))
        except Exception:
            continue
        for item in root.findall(".//item"):
            raw_title = clean(item.findtext("title") or "")
            source = clean(item.findtext("source") or "")
            if not source and " - " in raw_title:
                raw_title, source = raw_title.rsplit(" - ", 1)
            link = clean(item.findtext("link") or "")
            pub = item.findtext("pubDate") or ""
            try:
                published = email.utils.parsedate_to_datetime(pub)
                if published.tzinfo is None:
                    published = published.replace(tzinfo=UTC)
            except Exception:
                published = now
            if (now - published.astimezone(UTC)).total_seconds() > MAX_AGE_HOURS * 3600:
                continue
            blob = f"{raw_title} {source}"
            if not any(x.lower() in blob.lower() for x in TRUSTED):
                continue
            if not any(x.lower() in blob.lower() for x in MATERIAL):
                continue
            if any(x.lower() in raw_title.lower() for x in MARKET_ONLY) and not any(x.lower() in raw_title.lower() for x in HARD_FACT):
                continue
            key = hashlib.sha256(f"{raw_title}|{link}".encode()).hexdigest()[:24]
            rows.append({
                "id": key,
                "title": raw_title.strip(),
                "source": source.strip() or "신뢰자료",
                "link": link,
                "published": published.astimezone(UTC).isoformat(),
                "tags": tags_for(raw_title),
            })
    rows.sort(key=lambda x: x["published"], reverse=True)
    unique = []
    for row in rows:
        if any(row["id"] == old["id"] for old in unique):
            continue
        if any(similarity(row["title"], old["title"]) >= 0.68 for old in unique):
            continue
        unique.append(row)
    return unique


def footer(now: dt.datetime, rate: float, fx_source: str, tail: str) -> str:
    return (
        f"조회 {now.astimezone(KST).strftime('%Y-%m-%d %H:%M KST')} · "
        f"환율 1달러={rate:,.2f}원({fx_source}) · {tail}"
    )


def bootstrap_message(now: dt.datetime, usdkrw: float, fx_source: str) -> str:
    krw168 = usd_eok_krw(168, usdkrw)
    krw200 = usd_eok_krw(200, usdkrw)
    krw223 = usd_eok_krw(223, usdkrw)
    return "\n".join([
        "<b>🇺🇸 대미투자 | 텍사스 엔시날 1호 확정 보도</b>",
        "",
        "<b>한눈에 보기</b>",
        "• 1호 사업: <b>텍사스 엔시날 가스복합발전</b>",
        "• 발전용량: <b>6.3GW</b>",
        f"• 총사업비: <b>223억달러({krw223})</b> — 168억달러({krw168}) → 200억달러({krw200}) → 223억달러({krw223})로 확대 보도",
        "• 첫 <b>I-SPV 운영계약안</b>도 함께 의결 대상으로 보도",
        "• 미국 대형원전·Alaska LNG는 이번 1호에서 제외하고 별도 검토",
        "",
        "<b>왜 이 사업이 먼저인가</b>",
        "• 가스를 발전소에서 전기로 바꿔 판매하는 구조라 원전·CCUS보다 현금창출 경로가 단순합니다.",
        "• 1단계를 먼저 가동하고 후속 복합화력을 순차 증설해 초기 선투자 위험을 줄이는 구조입니다.",
        "",
        "<b>지금 가장 중요한 3가지</b>",
        "① <b>I-SPV</b> — 지분·의결권·수익배분·추가 공사비·손실분담",
        "② <b>PPA</b> — 고객 실명·계약 GW·기간·전력가격",
        "③ <b>한국 기업 본계약</b> — EPC·가스터빈·발전기 계약금액과 물량",
        "",
        "<b>아직 확정 매출로 보면 안 되는 부분</b>",
        "• 삼성물산·현대건설·DL이앤씨 EPC, 두산에너빌리티 가스터빈 등은 엔시날 본계약 확인 전입니다.",
        f"• 223억달러({krw223})는 <b>전체 프로젝트 총사업비</b>이지 한국이 한 번에 내는 직접 투자액이 아닙니다.",
        "",
        "<b>다음 확인</b>",
        "1) 정부 최종 공식 발표  2) I-SPV 원문  3) PPA  4) EPC·가스터빈 본계약  5) 추가 사업비 증액 여부",
        "",
        '<b>원문</b> · <a href="https://biz.heraldcorp.com/article/10864870">헤럴드경제</a> · <a href="https://www2.edaily.co.kr/News/Read?mediaCodeNo=257&newsId=02499366645577824">이데일리</a>',
        footer(now, usdkrw, fx_source, "공식 문서가 나오면 보도 단계에서 공식 확정으로 갱신"),
    ])


def update_message(now: dt.datetime, rows: list[dict], usdkrw: float, fx_source: str) -> str:
    parts = ["<b>🇺🇸 대미투자 | 중요 업데이트</b>", ""]
    for index, row in enumerate(rows[:5], 1):
        tags = row["tags"]
        parts.append(f"<b>{index}. {html.escape(row['title'])}</b>")
        conversions = extract_usd_conversions(row["title"], usdkrw)
        if conversions:
            parts.append("• 원화 환산: " + " · ".join(html.escape(x) for x in conversions))
        parts.extend([
            f"• 구분: <b>{html.escape(' / '.join(tags[:3]))}</b>",
            f"• 의미: {html.escape(meaning(tags))}",
            f"• 다음 확인: {html.escape(next_check(tags, usdkrw))}",
            f'• 원문: <a href="{html.escape(row["link"], quote=True)}">{html.escape(row["source"])}</a>',
            "",
        ])
    parts.append(footer(now, usdkrw, fx_source, "같은 사건 반복 기사와 단순 주가 반응은 제외"))
    return "\n".join(parts)


def main() -> int:
    if ALERT.exists():
        ALERT.unlink()
    state = load_state()
    now = dt.datetime.now(UTC)
    usdkrw, fx_source = get_usdkrw()

    if not state.get("bootstrap_sent"):
        ALERT.write_text(bootstrap_message(now, usdkrw, fx_source) + "\n", encoding="utf-8")
        state["bootstrap_sent"] = True
        state["last_alert_at"] = now.astimezone(KST).isoformat(timespec="seconds")
        state["recent_titles"] = ["텍사스 엔시날 대미투자 1호 6.3GW 223억달러"]
        state["last_usdkrw"] = usdkrw
        state["last_fx_source"] = fx_source
        STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("bootstrap_alert=true")
        return 0

    rows = rss_items(now)
    seen = state.setdefault("seen", {})
    recent = list(state.get("recent_titles") or [])[-80:]
    fresh = []
    for row in rows:
        if row["id"] in seen:
            continue
        seen[row["id"]] = now.isoformat()
        if any(similarity(row["title"], old) >= 0.68 for old in recent):
            continue
        fresh.append(row)
        recent.append(row["title"])
        if len(fresh) >= 5:
            break

    state["recent_titles"] = recent[-80:]
    state["last_checked_at"] = now.astimezone(KST).isoformat(timespec="seconds")
    state["last_usdkrw"] = usdkrw
    state["last_fx_source"] = fx_source
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if fresh:
        ALERT.write_text(update_message(now, fresh, usdkrw, fx_source) + "\n", encoding="utf-8")
        state["last_alert_at"] = now.astimezone(KST).isoformat(timespec="seconds")
        STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"new_alerts={len(fresh)}")
    else:
        print("new_alerts=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
