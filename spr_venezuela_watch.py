#!/usr/bin/env python3
import datetime as dt
import hashlib
import html
import json
import os
import pathlib
import re
import urllib.parse
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from refinery_watch import (
    HEADERS,
    enrich_korean,
    send_telegram,
    validate_telegram,
)

KST = ZoneInfo("Asia/Seoul")
STATE_PATH = pathlib.Path("spr_venezuela_watch_state.json")
SPR_CAPACITY_KBBL = 714_000

SPR_URL = "https://www.eia.gov/dnav/pet/PET_STOC_WSTK_A_EPC0_SAS_MBBL_W.htm"
VENEZUELA_WEEKLY_URL = (
    "https://www.eia.gov/dnav/pet/hist/LeafHandler.ashx?"
    "f=W&n=PET&s=W_EPC0_IM0_NUS-NVE_MBBLD"
)

NEWS_QUERIES = [
    'Trump Venezuela Strategic Petroleum Reserve SPR oil replenish',
    'Venezuela oil Strategic Petroleum Reserve DOE Trump',
    'site:energy.gov Venezuela "Strategic Petroleum Reserve"',
    'site:whitehouse.gov Venezuela "Strategic Petroleum Reserve"',
    'Venezuela crude SPR exchange return barrels Trump',
]

NEWS_TERMS = (
    "strategic petroleum reserve", "spr", "venezuela", "venezuelan",
    "replenish", "refill", "reserve", "department of energy", "doe",
    "exchange", "return barrels", "gift from venezuela",
)


def now_kst():
    return dt.datetime.now(KST)


def load_state():
    if not STATE_PATH.exists():
        return {
            "initialized": False,
            "seen_news": [],
            "spr": {},
            "venezuela_imports": {},
        }
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    data.setdefault("initialized", False)
    data.setdefault("seen_news", [])
    data.setdefault("spr", {})
    data.setdefault("venezuela_imports", {})
    return data


def save_state(state):
    state["last_run_kst"] = now_kst().isoformat(timespec="seconds")
    STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def parse_int(text):
    cleaned = re.sub(r"[^0-9-]", "", str(text or ""))
    if not cleaned or cleaned == "-":
        raise ValueError(f"숫자 변환 실패: {text!r}")
    return int(cleaned)


def fetch_spr_latest():
    r = requests.get(SPR_URL, headers=HEADERS, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    date_row = None
    dates = []
    for tr in soup.find_all("tr"):
        cells = [" ".join(x.get_text(" ", strip=True).split()) for x in tr.find_all(["th", "td"])]
        found = [x for x in cells if re.fullmatch(r"\d{2}/\d{2}/\d{2}", x)]
        if len(found) >= 2:
            date_row = cells
            dates = found
            break
    if not dates:
        raise RuntimeError("EIA SPR 기준일을 찾지 못함")

    values = None
    for tr in soup.find_all("tr"):
        cells = [" ".join(x.get_text(" ", strip=True).split()) for x in tr.find_all(["th", "td"])]
        if not cells:
            continue
        if cells[0].strip().upper() == "U.S.":
            nums = []
            for cell in cells[1:]:
                if re.fullmatch(r"[0-9,]+", cell):
                    nums.append(parse_int(cell))
            if len(nums) >= len(dates):
                values = nums[: len(dates)]
                break
    if not values:
        raise RuntimeError("EIA SPR 재고 수치를 찾지 못함")

    pairs = []
    for d, v in zip(dates, values):
        when = dt.datetime.strptime(d, "%m/%d/%y").date()
        pairs.append((when, v))
    pairs.sort(key=lambda x: x[0])
    latest_date, latest_value = pairs[-1]
    prev_value = pairs[-2][1] if len(pairs) >= 2 else None
    return {
        "date": latest_date.isoformat(),
        "value_kbbl": latest_value,
        "prev_kbbl": prev_value,
        "source": SPR_URL,
    }


def fetch_venezuela_imports_latest():
    r = requests.get(VENEZUELA_WEEKLY_URL, headers=HEADERS, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    observations = []

    for tr in soup.find_all("tr"):
        cells = [" ".join(x.get_text(" ", strip=True).split()) for x in tr.find_all(["th", "td"])]
        if not cells:
            continue
        m = re.search(r"(20\d{2})-[A-Za-z]{3}", cells[0])
        if not m:
            continue
        year = int(m.group(1))
        i = 1
        while i + 1 < len(cells):
            d = cells[i].strip()
            v = cells[i + 1].strip()
            if re.fullmatch(r"\d{2}/\d{2}", d) and re.fullmatch(r"[0-9,]+", v):
                month, day = map(int, d.split("/"))
                observations.append((dt.date(year, month, day), parse_int(v)))
                i += 2
            else:
                i += 1

    if not observations:
        raise RuntimeError("EIA 베네수엘라산 주간 원유수입 수치를 찾지 못함")
    observations.sort(key=lambda x: x[0])
    latest_date, latest_value = observations[-1]
    prev_value = observations[-2][1] if len(observations) >= 2 else None
    return {
        "date": latest_date.isoformat(),
        "value_kbd": latest_value,
        "prev_kbd": prev_value,
        "source": VENEZUELA_WEEKLY_URL,
    }


def format_million_barrels(kbbl):
    return f"{kbbl / 1000:,.3f}백만 배럴"


def format_spr_alert(current, previous_state):
    value = current["value_kbbl"]
    prev = current.get("prev_kbbl")
    delta = value - prev if prev is not None else None
    ratio = value / SPR_CAPACITY_KBBL * 100
    shortage = SPR_CAPACITY_KBBL - value

    if delta is None:
        direction = "전주 대비 비교 불가"
        verdict = "실제 보충 시작 여부 확인 필요"
    elif delta > 0:
        direction = f"전주 대비 +{delta:,}천 배럴(+{delta / 1000:,.3f}백만 배럴)"
        verdict = "SPR 주간 재고 순증 확인 → 실제 보충 또는 반환 유입 신호"
    elif delta < 0:
        direction = f"전주 대비 {delta:,}천 배럴({delta / 1000:,.3f}백만 배럴)"
        verdict = "SPR 재고가 계속 감소 → 베네수엘라산 보충 효과는 아직 주간 재고에서 확인되지 않음"
    else:
        direction = "전주 대비 변화 없음"
        verdict = "보충 시작 신호 미확인"

    return (
        "<b>[SPR·베네수엘라 원유 공식 수치]</b>\n"
        f"기준일: {html.escape(current['date'])}\n\n"
        f"• 미국 전략비축유(SPR): <b>{value:,}천 배럴</b> ({format_million_barrels(value)})\n"
        f"• 주간 변화: {direction}\n"
        f"• 저장능력 7억1,400만 배럴 대비: <b>{ratio:.1f}%</b>\n"
        f"• 완전 충전까지 부족분: 약 <b>{shortage / 1000:,.3f}백만 배럴</b>\n\n"
        f"판정: <b>{verdict}</b>\n"
        "주의: SPR 증가분이 확인돼도 그것이 베네수엘라산 신규 반입인지, 기존 교환계약 반환물량인지 DOE 자료로 별도 확인해야 합니다.\n\n"
        f'<a href="{html.escape(current["source"], quote=True)}">EIA 원문</a>'
    )


def format_import_alert(current):
    value = current["value_kbd"]
    prev = current.get("prev_kbd")
    delta = value - prev if prev is not None else None
    if delta is None:
        change = "전주 대비 비교 불가"
    else:
        sign = "+" if delta > 0 else ""
        change = f"전주 대비 {sign}{delta:,}천 배럴/일"

    weekly_volume_kbbl = value * 7
    return (
        "<b>[베네수엘라산 원유 미국 유입 공식 수치]</b>\n"
        f"기준일: {html.escape(current['date'])}\n\n"
        f"• 미국의 베네수엘라산 원유 수입: <b>{value:,}천 배럴/일</b>\n"
        f"• 주간 변화: {change}\n"
        f"• 단순 7일 환산 물량: 약 <b>{weekly_volume_kbbl / 1000:,.3f}백만 배럴</b>\n\n"
        "판정: 이 수치는 미국 전체 베네수엘라산 원유 수입량이며 SPR 직접 반입량과 동일하지 않습니다. "
        "정유사 투입분과 SPR 저장분을 DOE 조달·반입 자료로 분리 확인해야 합니다.\n\n"
        f'<a href="{html.escape(current["source"], quote=True)}">EIA 원문</a>'
    )


def google_news_rss(query):
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode({
        "q": query,
        "hl": "en-US",
        "gl": "US",
        "ceid": "US:en",
    })
    r = requests.get(url, headers=HEADERS, timeout=25)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    items = []
    for node in root.findall("./channel/item"):
        title = (node.findtext("title") or "").strip()
        link = (node.findtext("link") or "").strip()
        source_el = node.find("source")
        source = (source_el.text or "").strip() if source_el is not None else ""
        source_url = (source_el.attrib.get("url") or "").strip() if source_el is not None else ""
        pub_raw = (node.findtext("pubDate") or "").strip()
        try:
            pub = parsedate_to_datetime(pub_raw)
            if pub.tzinfo is None:
                pub = pub.replace(tzinfo=dt.timezone.utc)
            pub_kst = pub.astimezone(KST)
        except Exception:
            pub_kst = now_kst()
        ident = hashlib.sha256((title + "|" + link).encode("utf-8")).hexdigest()[:24]
        items.append({
            "id": ident,
            "title": title,
            "link": link,
            "source": source,
            "source_url": source_url,
            "pub_kst": pub_kst.isoformat(timespec="minutes"),
        })
    return items


def relevant_news(item):
    text = f"{item['title']} {item['source']}".lower()
    if "venezuela" not in text and "venezuelan" not in text:
        return False
    hits = sum(1 for term in NEWS_TERMS if term in text)
    official_or_reliable = any(x in text for x in (
        "department of energy", "white house", "reuters", "bloomberg",
        "eia", "energy.gov",
    ))
    return hits >= 2 or (hits >= 1 and official_or_reliable)


def collect_news():
    merged = {}
    errors = []
    for query in NEWS_QUERIES:
        try:
            for item in google_news_rss(query):
                if relevant_news(item):
                    merged[item["id"]] = item
        except Exception as exc:
            errors.append(f"{query}: {exc}")
    items = list(merged.values())
    items.sort(key=lambda x: x["pub_kst"], reverse=True)
    return items, errors


def format_news_alert(item):
    link = html.escape(item["original_url"], quote=True)
    lines = [
        "<b>[SPR·베네수엘라 원유 웹감시]</b>",
        f"<b>{html.escape(item['title_ko'])}</b>",
        f"출처: {html.escape(item['source'] or '확인 필요')} | 발표: {html.escape(item['pub_kst'].replace('T', ' '))}",
        "",
        "<b>본문 한국어 번역</b>",
    ]
    for paragraph in item["body_ko"]:
        lines.append(html.escape(paragraph))
        lines.append("")
    lines.extend([
        f'<a href="{link}">원문</a>',
        "",
        "감시 포인트: DOE의 베네수엘라산 SPR 조달·반입 공고, 실제 배럴 수·가격·반입일·저장기지, "
        "기존 SPR 교환계약 반환물량과의 중복 여부, EIA SPR 주간 재고 순증, 미국의 베네수엘라산 원유 수입 변화, "
        "원유 품질·저장 규격, 생산량 150만 배럴/일 확대 일정과 실제 선적 여부.",
    ])
    return "\n".join(lines).strip()


def main():
    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()
    if not token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID secret is missing")

    bot_username = validate_telegram(token)
    state = load_state()
    messages = []

    # 1) EIA SPR weekly official data
    try:
        spr = fetch_spr_latest()
        old = state.get("spr") or {}
        if old.get("date") and spr["date"] != old.get("date"):
            messages.append(format_spr_alert(spr, old))
        state["spr"] = spr
    except Exception as exc:
        print(f"spr_fetch_error={exc}")

    # 2) Weekly U.S. imports of Venezuelan crude
    try:
        ven = fetch_venezuela_imports_latest()
        old = state.get("venezuela_imports") or {}
        if old.get("date") and ven["date"] != old.get("date"):
            messages.append(format_import_alert(ven))
        state["venezuela_imports"] = ven
    except Exception as exc:
        print(f"venezuela_import_fetch_error={exc}")

    # 3) Policy/news changes; English body is never sent unless Korean translation succeeds.
    news, source_errors = collect_news()
    seen = set(state.get("seen_news") or [])
    cutoff = now_kst() - dt.timedelta(days=3)
    delivered = []
    for item in news:
        if item["id"] in seen:
            continue
        try:
            pub = dt.datetime.fromisoformat(item["pub_kst"])
            if pub < cutoff:
                continue
        except Exception:
            pass
        try:
            enriched = enrich_korean(item)
            messages.append(format_news_alert(enriched))
            delivered.append(item["id"])
        except Exception as exc:
            # Leave unseen to retry later; never fall back to English/error pages.
            print(f"spr_news_translation_skipped={item['id']} error={exc}")
        if len(delivered) >= 4:
            break

    if not state.get("initialized"):
        # Establish baseline silently so old articles/data are not replayed.
        state["initialized"] = True
        state["seen_news"] = [x["id"] for x in news[:300]]
        messages = []
    else:
        state["seen_news"] = list(dict.fromkeys(delivered + list(seen)))[:600]

    sent_ids = []
    for message in messages:
        results = send_telegram(token, chat_id, message)
        sent_ids.extend((x.get("result") or {}).get("message_id") for x in results)

    save_state(state)
    print(
        f"spr_venezuela_watch_ok=true bot=@{bot_username} messages={len(messages)} "
        f"message_ids={sent_ids} source_errors={len(source_errors)}"
    )
    if source_errors:
        print("source_errors=" + " | ".join(source_errors))


if __name__ == "__main__":
    main()
