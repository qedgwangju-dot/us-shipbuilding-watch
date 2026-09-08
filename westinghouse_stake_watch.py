#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import html
import json
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "westinghouse_stake_state.json"
PENDING = ROOT / "westinghouse_stake_pending_state.json"
ALERT = ROOT / "westinghouse_stake_alert.html"
STATUS = ROOT / "westinghouse_stake_status.md"

QUERIES = [
    ("한국 뉴스", "웨스팅하우스 지분 인수 한국전력 산업통상부 한수원 브룩필드 카메코 when:14d"),
    ("해외 뉴스", "Westinghouse stake Korea KEPCO KHNP Brookfield Cameco when:14d"),
    ("공식입장", "웨스팅하우스 산업통상부 한국전력 지분 공식 발표 when:30d"),
]

CORE = ["westinghouse", "웨스팅하우스", "wec"]
TRANSACTION = [
    "지분", "인수", "투자", "출자", "주주", "경영", "상장", "기업공개", "급물살", "검토", "협상", "제안",
    "stake", "equity", "acquisition", "investment", "invest", "shareholder", "buyout", "ipo", "talks", "proposal",
    "brookfield", "브룩필드", "cameco", "카메코", "kepco", "한국전력", "한전", "khnp", "한수원", "ap1000",
    "지식재산", "입찰 제한", "bidding restriction", "intellectual property",
]
BAD = re.compile(r"(?:error\s*\d+|server\s+error|access\s+denied|forbidden|captcha|service\s+unavailable|try\s+again\s+later)", re.I)
PROTECTED = ["Westinghouse", "Brookfield", "Cameco", "AP1000", "CFIUS", "NRC", "LOI", "MOU", "KEPCO", "KHNP"]


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def fetch(url: str, timeout: int = 25) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "KHS-Westinghouse-Watch/1.0", "Accept-Language": "ko,en;q=0.8"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def relevant(title: str) -> bool:
    low = title.lower()
    return any(x in low for x in CORE) and any(x in low for x in TRANSACTION)


def clean_title(title: str) -> str:
    return re.sub(r"\s+-\s+[^-]{2,80}$", "", norm(title)).strip()


def fp(title: str, url: str, source: str) -> str:
    return hashlib.sha256(f"{title.lower()}\n{url}\n{source}".encode()).hexdigest()


def load_state() -> dict:
    if not STATE.exists():
        return {"initialized": False, "seen": []}
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {"initialized": False, "seen": []}


def parse_feed(label: str, query: str) -> list[dict]:
    url = "https://news.google.com/rss/search?q=" + urllib.parse.quote_plus(query) + "&hl=ko&gl=KR&ceid=KR:ko"
    root = ET.fromstring(fetch(url))
    now = datetime.now(timezone.utc)
    rows = []
    for item in root.findall(".//item"):
        title = clean_title(item.findtext("title") or "")
        link = norm(item.findtext("link") or "")
        src = item.find("source")
        outlet = norm(src.text if src is not None and src.text else "") or label
        pub = norm(item.findtext("pubDate") or "")
        if not title or not link or BAD.search(title) or not relevant(title):
            continue
        try:
            dt = parsedate_to_datetime(pub)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if (now - dt.astimezone(timezone.utc)).total_seconds() > 30 * 86400:
                continue
        except Exception:
            pass
        rows.append({"title": title[:500], "url": link, "source": outlet, "published": pub})
        if len(rows) >= 8:
            break
    return rows


def needs_translation(text: str) -> bool:
    if re.search(r"[가-힣]", text):
        return False
    stripped = text
    for term in sorted(PROTECTED, key=len, reverse=True):
        stripped = re.sub(re.escape(term), " ", stripped, flags=re.I)
    return bool(re.search(r"\b[A-Za-z]{3,}\b", stripped))


def translate_ko(text: str) -> str:
    raw = norm(text)
    if not needs_translation(raw):
        return raw
    placeholders = {}
    protected = raw
    for i, term in enumerate(sorted(PROTECTED, key=len, reverse=True)):
        ph = f"ZXQ{i}QXZ"
        if re.search(re.escape(term), protected, flags=re.I):
            protected = re.sub(re.escape(term), ph, protected, flags=re.I)
            placeholders[ph] = term
    params = urllib.parse.urlencode({"client":"gtx","sl":"en","tl":"ko","dt":"t","q":protected})
    data = json.loads(fetch("https://translate.googleapis.com/translate_a/single?" + params, 20))
    out = "".join(str(row[0]) for row in (data[0] if data else []) if isinstance(row, list) and row and row[0])
    for ph, term in placeholders.items():
        out = out.replace(ph, term).replace(ph.lower(), term)
    out = norm(out)
    if not re.search(r"[가-힣]", out) or BAD.search(out):
        raise RuntimeError("한국어 번역 검증 실패")
    return out


def role(title: str) -> str:
    low = title.lower()
    if any(x in low for x in ["특징주", "상승세", "급등", "주가"]): return "시장 반응"
    if any(x in low for x in ["사실과 달라", "사실과 다름", "부인", "denies", "not true"]): return "부인·검증"
    if any(x in low for x in ["떠맡", "부담", "우려", "논란", "쟁점"]): return "쟁점·비판"
    if any(x in low for x in ["급물살", "검토", "협상", "논의", "제안", "인수", "확보", "상장"]): return "본안·협상"
    return "관련 보도"


def status(events: list[dict]) -> str:
    for e in events:
        src = e["source"].lower(); t = e["title"].lower()
        official = any(x in src for x in ["산업통상부", "정책브리핑", "한국전력", "한수원", "westinghouse", "cameco", "brookfield"])
        if official and any(x in t for x in ["계약", "합의", "취득", "투자 확정", "agreement", "acquisition"]):
            return "공식 확인 단계"
    if any(any(x in e["title"] for x in ["검토", "급물살", "협상", "논의", "확보", "인수"]) for e in events):
        return "새 보도 재등장 — 협의·검토 단계, 공식 확정 전"
    return "관련 보도 확대 — 공식 확정 여부 교차검증 필요"


def headline(events: list[dict]) -> str:
    titles = " ".join(e["title"] for e in events)
    if any(x in titles for x in ["급물살", "정부·한전", "지분 확보", "지분 인수"]):
        return "한국의 Westinghouse 지분 인수 논의 재부상"
    return "한국의 Westinghouse 지분 참여 관련 보도 확대"


def render(events: list[dict]) -> str:
    lines = [
        "🚨 <b>[원전·Westinghouse 웹감시]</b>", "",
        f"<b>{html.escape(headline(events))}</b>",
        f"<code>지분투자·원전동맹 | 신규 보도 {len(events)}건</code>", "",
        "<b>지금 무엇이 달라졌나</b>",
        "• 한국 정부·한국전력이 미국 원전 건설 참여와 함께 Westinghouse 지분 확보 방안을 검토한다는 보도가 다시 확대",
        "• 2026년 8월 25일 산업통상부는 ‘한·미 공동출자 방식의 Westinghouse 지분 인수’ 보도를 공식적으로 사실과 다르다고 설명한 바 있어 새 보도는 공식자료와 교차검증 필요",
        f"• 현재 판정: <b>{html.escape(status(events))}</b>", "",
        "<b>한눈에 보기</b>",
        "• 핵심 당사자: <b>한국 정부·한국전력 / Westinghouse / Brookfield / Cameco</b>",
        "• 거래조건: <b>지분율 · 인수가격 · 재원 · 경영참여권</b>",
        "• 사업 연결: <b>AP1000 설계 · 기자재 조달 · 시공 · 사업개발</b>",
        "• 별도 협상: <b>지식재산권 · 입찰 지역 제한 완화</b>", "",
        "<b>기사별 확인</b>",
    ]
    for e in events[:8]:
        try:
            t = translate_ko(e["title"])
        except Exception:
            continue
        if BAD.search(t):
            continue
        lines.append(f"• <b>{html.escape(role(t))}</b> | {html.escape(e['source'])} — <a href=\"{html.escape(e['url'], quote=True)}\">{html.escape(t)}</a>")
    lines += [
        "", "<b>핵심 병목</b>",
        "• <b>공식 확인:</b> 산업통상부의 기존 부인 이후 정부·한전·Westinghouse 측 새 공식 발표가 핵심",
        "• <b>가격·지분:</b> Brookfield·Cameco가 실제로 어느 지분을 어떤 가격에 매각할지 미확정",
        "• <b>권한:</b> 지분 취득만으로 AP1000 설계·조달·시공 권한이 자동 확보되지는 않음",
        "• <b>규제:</b> 미국 원전 프로젝트별 CFIUS·NRC 관련 심사 가능", "",
        "<b>왜 중요한가</b>",
        "• 실제 지분 참여와 사업권 확대가 함께 성사되면 한국 역할이 단순 기자재·시공에서 <b>설계·조달·사업개발</b>까지 확대 가능",
        "• 반대로 지분만 취득하고 사업권·지식재산권 조건이 그대로라면 투자금 대비 전략적 실익이 낮아질 수 있음", "",
        "<b>다음 확인</b>",
        "• <b>공식 발표 → LOI/MOU → 실사 착수 → 지분율·인수가격·재원 → 경영참여권 → CFIUS/NRC → AP1000 사업권</b>",
    ]
    return "\n".join(lines).strip()


def main() -> int:
    for p in [ALERT, PENDING, STATUS]:
        try: p.unlink()
        except FileNotFoundError: pass
    state = load_state(); initialized = bool(state.get("initialized")); seen = set(state.get("seen") or [])
    all_rows = []
    errors = []
    for label, query in QUERIES:
        try: all_rows.extend(parse_feed(label, query))
        except Exception as exc: errors.append(f"{label}: {type(exc).__name__}: {exc}")
    unique = {}
    for row in all_rows:
        key = fp(row["title"], row["url"], row["source"]); row["fp"] = key; unique[key] = row
    current = set(unique)
    new = [unique[k] for k in current - seen] if initialized else []
    # 최신 보도가 위로 오도록 제목/발행시각 기준 정렬. 동일 이슈는 한 메시지로 묶는다.
    new.sort(key=lambda x: x.get("published", ""), reverse=True)
    if new:
        text = render(new)
        if text and not BAD.search(text): ALERT.write_text(text, encoding="utf-8")
    merged = list(dict.fromkeys(list(state.get("seen") or []) + sorted(current)))
    if len(merged) > 2500: merged = merged[-2500:]
    pending = {"initialized": True, "initialized_at": state.get("initialized_at") or datetime.now(timezone.utc).isoformat(), "last_checked_at": datetime.now(timezone.utc).isoformat(), "seen": merged}
    PENDING.write_text(json.dumps(pending, ensure_ascii=False, indent=2), encoding="utf-8")
    STATUS.write_text("# Westinghouse 지분 웹감시\n\n" + f"- 기준선: {len(current)}건\n- 신규: {len(new)}건\n- 오류: {len(errors)}건\n- 초기화: {'예' if not initialized else '아니오'}\n" + ("- 부분오류: " + " | ".join(errors[:3]) + "\n" if errors else ""), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
