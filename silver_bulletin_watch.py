from __future__ import annotations

import csv
import datetime as dt
import html
import io
import json
import os
import pathlib
import re
from typing import Dict, Optional

import requests
from bs4 import BeautifulSoup

PAGE_URL = "https://www.natesilver.net/p/trump-approval-ratings-nate-silver-bulletin"
STATE = pathlib.Path("silver_bulletin_state.json")
OUT = pathlib.Path("out")
ALERT = OUT / "silver_bulletin_alert.txt"
PENDING = OUT / "silver_bulletin_state_pending.json"
STATUS = OUT / "silver_bulletin_status.txt"
DEBUG = OUT / "silver_bulletin_debug.json"

FALLBACK_CHARTS = [
    "https://datawrapper.dwcdn.net/AdipN/76/",
    "https://datawrapper.dwcdn.net/RFXsV/73/",
    "https://datawrapper.dwcdn.net/wWI2Y/70/",
]

ISSUES = {
    "cost_of_living": ("물가·생활비", ["cost of living", "inflation/prices", "inflation", "prices", "living costs"]),
    "economy": ("경제", ["economy", "economic"]),
    "immigration": ("이민", ["immigration", "immigrant", "border"]),
    "trade": ("무역·관세", ["trade and tariffs", "trade & tariffs", "trade/tariffs", "tariff", "trade"]),
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; KHS-Silver-Bulletin-Watch/1.0)",
    "Cache-Control": "no-cache",
}


def norm(v) -> str:
    s = html.unescape(str(v or "")).lower().replace("−", "-").replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", s).strip()


def num(v) -> Optional[float]:
    s = norm(v).replace(",", "")
    if not s or re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}", s):
        return None
    m = re.search(r"(?<!\d)([-+]?\d+(?:\.\d+)?)(?:\s*%)?", s)
    if not m:
        return None
    x = float(m.group(1))
    return x if abs(x) <= 100 else None


def issue_for(v) -> Optional[str]:
    s = norm(v)
    for key, (_, aliases) in ISSUES.items():
        for alias in aliases:
            a = norm(alias)
            if s == a or re.search(rf"(?<![a-z]){re.escape(a)}(?![a-z])", s):
                return key
    return None


def date_key(row: dict, headers: list[str], idx: int) -> float:
    for h in headers:
        if any(t in norm(h) for t in ("date", "day", "time", "end")):
            s = str(row.get(h, "")).strip().replace("Z", "+00:00")
            try:
                d = dt.datetime.fromisoformat(s)
                if d.tzinfo is None:
                    d = d.replace(tzinfo=dt.timezone.utc)
                return d.timestamp()
            except ValueError:
                pass
    return float(idx)


def discover_charts(page: str) -> list[str]:
    soup = BeautifulSoup(page, "html.parser")
    found = []
    for iframe in soup.find_all("iframe"):
        src = str(iframe.get("src") or "")
        if "datawrapper.dwcdn.net" not in src:
            continue
        if src.startswith("//"):
            src = "https:" + src
        if not src.startswith("http"):
            continue
        m = re.search(r"https?://datawrapper\.dwcdn\.net/[A-Za-z0-9]+/\d+/?", src)
        if m:
            url = m.group(0)
            if not url.endswith("/"):
                url += "/"
            if url not in found:
                found.append(url)
    return found


def parse_csv(text: str) -> tuple[list[str], list[dict]]:
    try:
        dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    headers = [h for h in (reader.fieldnames or []) if h]
    rows = [{str(k): str(v or "") for k, v in r.items() if k} for r in reader]
    return headers, rows


def extract(headers: list[str], rows: list[dict]) -> Dict[str, dict]:
    found: Dict[str, dict] = {}

    # Wide datasets: issue names are columns and each row is a date.
    for h in headers:
        key = issue_for(h)
        if not key:
            continue
        for i, row in enumerate(rows):
            x = num(row.get(h))
            if x is None:
                continue
            k = date_key(row, headers, i)
            if key not in found or k >= found[key]["sort"]:
                found[key] = {"value": x, "sort": k, "mode": "wide", "field": h}

    # Long datasets: one column contains the issue label.
    for label_h in headers:
        for i, row in enumerate(rows):
            key = issue_for(row.get(label_h))
            if not key:
                continue
            candidates = []
            approve = disapprove = None
            for h in headers:
                if h == label_h:
                    continue
                hn = norm(h)
                x = num(row.get(h))
                if x is None:
                    continue
                score = 0
                if "net" in hn:
                    score += 50
                if any(t in hn for t in ("average", "avg", "rating", "margin", "value")):
                    score += 15
                if any(t in hn for t in ("date", "day", "time", "sample", "n=")):
                    score -= 30
                if "approve" in hn and "dis" not in hn:
                    approve = x
                if "disapprove" in hn:
                    disapprove = x
                candidates.append((score, x, h))
            candidates.sort(reverse=True)
            x = candidates[0][1] if candidates and candidates[0][0] > 0 else None
            if x is None and approve is not None and disapprove is not None:
                x = approve - disapprove
            if x is None:
                continue
            k = date_key(row, headers, i)
            if key not in found or k >= found[key]["sort"]:
                found[key] = {"value": x, "sort": k, "mode": "long", "field": label_h}
    return found


def load_state() -> dict:
    if not STATE.exists():
        return {}
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def fmt(x: float) -> str:
    return f"{x:+.1f}%p"


def main() -> int:
    OUT.mkdir(exist_ok=True)
    for p in (ALERT, PENDING, STATUS, DEBUG):
        p.unlink(missing_ok=True)

    debug = {"page": PAGE_URL, "charts": [], "errors": []}
    try:
        page = requests.get(PAGE_URL, headers=HEADERS, timeout=30).text
        charts = discover_charts(page)
    except Exception as e:
        charts = []
        debug["errors"].append(f"page: {type(e).__name__}: {e}")
    if not charts:
        charts = FALLBACK_CHARTS[:]

    values: Dict[str, dict] = {}
    for base in charts:
        url = base.rstrip("/") + "/dataset.csv"
        item = {"chart": base, "dataset": url}
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            r.raise_for_status()
            headers, rows = parse_csv(r.text)
            got = extract(headers, rows)
            item.update({"status": r.status_code, "headers": headers, "rows": len(rows), "issues": list(got)})
            for key, payload in got.items():
                if key not in values or payload["sort"] >= values[key]["sort"]:
                    values[key] = {**payload, "chart": base}
        except Exception as e:
            item["error"] = f"{type(e).__name__}: {e}"
            debug["errors"].append(f"{url}: {item['error']}")
        debug["charts"].append(item)

    debug["values"] = {k: v for k, v in values.items()}
    DEBUG.write_text(json.dumps(debug, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if len(values) < 3:
        STATUS.write_text(
            f"Silver Bulletin 확인 불가: 이슈 평균 {len(values)}/4개만 파싱됨. 기존 기준값 보존, Telegram 미발송.\n",
            encoding="utf-8",
        )
        return 2

    previous = load_state()
    old = previous.get("values", {}) if isinstance(previous, dict) else {}
    changed = not old
    if old:
        for key, payload in values.items():
            old_value = old.get(key)
            if isinstance(old_value, dict):
                old_value = old_value.get("value")
            try:
                old_num = float(old_value)
            except (TypeError, ValueError):
                changed = True
                break
            if abs(float(payload["value"]) - old_num) >= 0.05:
                changed = True
                break

    compact = {k: {"value": round(float(v["value"]), 3), "chart": v["chart"]} for k, v in values.items()}
    current = {
        "source": PAGE_URL,
        "checked_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "values": compact,
        "charts": charts,
    }

    if changed:
        lines = ["[Silver Bulletin 트럼프 이슈 지지율 감시] " + ("Telegram 연결 완료" if not old else "변화 감지"), ""]
        deltas = []
        for key in ("cost_of_living", "economy", "immigration", "trade"):
            if key not in compact:
                continue
            label = ISSUES[key][0]
            now = float(compact[key]["value"])
            before = old.get(key)
            if isinstance(before, dict):
                before = before.get("value")
            try:
                before = float(before)
            except (TypeError, ValueError):
                before = None
            if before is None:
                lines.append(f"- {label}: {fmt(now)}")
            else:
                delta = now - before
                deltas.append((abs(delta), label, delta))
                lines.append(f"- {label}: {fmt(before)} → {fmt(now)} ({delta:+.1f}%p)")
        if deltas:
            _, label, delta = max(deltas)
            lines += ["", f"→ 가장 큰 변화: {label} {abs(delta):.1f}%p {'개선' if delta > 0 else '악화'}"]
        else:
            lines += ["", "→ 현재 수치를 기준값으로 저장합니다. 이후 값이 바뀔 때만 알립니다."]
        lines += ["", f"- 원문: {PAGE_URL}"]
        ALERT.write_text("\n".join(lines) + "\n", encoding="utf-8")
        PENDING.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    STATUS.write_text(
        "Silver Bulletin 이슈 평균 확인 완료 — " + ("기준값/변화 감지" if changed else "변화 없음") + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
