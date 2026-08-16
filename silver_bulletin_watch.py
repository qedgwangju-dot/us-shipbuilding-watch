from __future__ import annotations

import csv
import datetime as dt
import html
import io
import json
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
PARSER_VERSION = 2

# Known issue-average chart fallback. The watcher still rediscovers all live
# Datawrapper embeds from Silver Bulletin every run and identifies the correct
# dataset by its four issue columns, not by this chart ID alone.
FALLBACK_CHARTS = [
    "https://datawrapper.dwcdn.net/RFXsV/73/",
]

ISSUES = {
    "cost_of_living": (
        "물가·생활비",
        ["cost", "cost of living", "inflation/prices", "inflation", "prices", "living costs"],
    ),
    "economy": ("경제", ["econ", "economy", "economic"]),
    "immigration": ("이민", ["immg", "immigration", "immigrant", "border"]),
    "trade": (
        "무역·관세",
        ["trade", "trade and tariffs", "trade & tariffs", "trade/tariffs", "tariff", "tariffs"],
    ),
}
REQUIRED_ISSUES = set(ISSUES)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; KHS-Silver-Bulletin-Watch/2.0)",
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


def issue_for_header(v) -> Optional[str]:
    """Map only *column headers* to issue keys.

    This intentionally never scans row text such as pollster names. The old
    bootstrap parser did that and could mistake a pollster containing words
    like Economy/Trade for an issue-average observation.
    """
    s = norm(v)
    for key, (_, aliases) in ISSUES.items():
        for alias in aliases:
            if s == norm(alias):
                return key
    return None


def parse_date(v) -> Optional[dt.datetime]:
    s = str(v or "").strip()
    if not s:
        return None
    for candidate in (s, s.replace("Z", "+00:00")):
        try:
            d = dt.datetime.fromisoformat(candidate)
            if d.tzinfo is None:
                d = d.replace(tzinfo=dt.timezone.utc)
            return d.astimezone(dt.timezone.utc)
        except ValueError:
            pass
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%m/%d/%y", "%b %d, %Y", "%B %d, %Y"):
        try:
            return dt.datetime.strptime(s, fmt).replace(tzinfo=dt.timezone.utc)
        except ValueError:
            pass
    return None


def row_date(row: dict, headers: list[str]) -> Optional[dt.datetime]:
    for h in headers:
        if norm(h) in {"modeldate", "date", "dates", "day", "time"}:
            parsed = parse_date(row.get(h))
            if parsed:
                return parsed
    return None


def discover_charts(page: str) -> list[str]:
    soup = BeautifulSoup(page, "html.parser")
    found = []
    for iframe in soup.find_all("iframe"):
        src = str(iframe.get("src") or "")
        if "datawrapper.dwcdn.net" not in src:
            continue
        if src.startswith("//"):
            src = "https:" + src
        m = re.search(r"https?://datawrapper\.dwcdn\.net/[A-Za-z0-9]+/\d+/?", src)
        if not m:
            continue
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


def extract_issue_average(headers: list[str], rows: list[dict]) -> Optional[dict]:
    """Return latest row only when one dataset exposes all four issue columns."""
    header_map: Dict[str, str] = {}
    for h in headers:
        key = issue_for_header(h)
        if key:
            header_map[key] = h
    if set(header_map) != REQUIRED_ISSUES:
        return None

    candidates = []
    for idx, row in enumerate(rows):
        values = {}
        valid = True
        for key, h in header_map.items():
            x = num(row.get(h))
            if x is None:
                valid = False
                break
            values[key] = x
        if not valid:
            continue
        d = row_date(row, headers)
        sort_key = d.timestamp() if d else float(idx)
        candidates.append((sort_key, idx, d, values))
    if not candidates:
        return None
    _, idx, d, values = max(candidates, key=lambda item: (item[0], item[1]))
    return {
        "values": values,
        "date": d.date().isoformat() if d else "",
        "row_index": idx,
        "headers": header_map,
    }


def load_state() -> dict:
    if not STATE.exists():
        return {}
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def fmt(x: float) -> str:
    return f"{x:+.1f}%p"


def current_values_only(compact: dict) -> list[str]:
    lines = []
    for key in ("cost_of_living", "economy", "immigration", "trade"):
        if key in compact:
            lines.append(f"- {ISSUES[key][0]}: {fmt(float(compact[key]['value']))}")
    return lines


def main() -> int:
    OUT.mkdir(exist_ok=True)
    for p in (ALERT, PENDING, STATUS, DEBUG):
        p.unlink(missing_ok=True)

    debug = {"parser_version": PARSER_VERSION, "page": PAGE_URL, "charts": [], "errors": []}
    try:
        response = requests.get(PAGE_URL, headers=HEADERS, timeout=30)
        response.raise_for_status()
        charts = discover_charts(response.text)
    except Exception as e:
        charts = []
        debug["errors"].append(f"page: {type(e).__name__}: {e}")
    for fallback in FALLBACK_CHARTS:
        if fallback not in charts:
            charts.append(fallback)

    datasets = []
    for base in charts:
        url = base.rstrip("/") + "/dataset.csv"
        item = {"chart": base, "dataset": url}
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            r.raise_for_status()
            headers, rows = parse_csv(r.text)
            issue_headers = {key: h for h in headers if (key := issue_for_header(h))}
            extracted = extract_issue_average(headers, rows)
            item.update(
                {
                    "status": r.status_code,
                    "headers": headers,
                    "rows": len(rows),
                    "recognized_issue_headers": issue_headers,
                    "is_issue_average": extracted is not None,
                }
            )
            if extracted:
                datasets.append((base, extracted))
        except Exception as e:
            item["error"] = f"{type(e).__name__}: {e}"
            debug["errors"].append(f"{url}: {item['error']}")
        debug["charts"].append(item)

    if not datasets:
        DEBUG.write_text(json.dumps(debug, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        STATUS.write_text(
            "Silver Bulletin 확인 불가: 경제·이민·무역·관세·물가·생활비 4개 이슈 평균 열을 모두 가진 데이터셋을 찾지 못했습니다. 기존 기준값 보존, Telegram 미발송.\n",
            encoding="utf-8",
        )
        return 2

    # Prefer the live-discovered complete issue dataset with the latest dated row.
    def dataset_rank(item):
        base, payload = item
        d = parse_date(payload.get("date"))
        return (d.timestamp() if d else -1, 1 if base in charts else 0)

    base, extracted = max(datasets, key=dataset_rank)
    values = extracted["values"]
    data_date = extracted.get("date", "")
    debug["selected_issue_chart"] = base
    debug["selected_data_date"] = data_date
    debug["selected_values"] = values
    DEBUG.write_text(json.dumps(debug, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    previous = load_state()
    old = previous.get("values", {}) if isinstance(previous, dict) else {}
    old_version = int(previous.get("parser_version", 0) or 0) if isinstance(previous, dict) else 0
    correction = bool(old) and old_version < PARSER_VERSION

    compact = {
        key: {"value": round(float(values[key]), 6), "chart": base}
        for key in ("cost_of_living", "economy", "immigration", "trade")
    }
    current = {
        "parser_version": PARSER_VERSION,
        "source": PAGE_URL,
        "issue_chart": base,
        "data_date": data_date,
        "checked_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "values": compact,
    }

    changed = correction or not old
    if old and not correction:
        for key, payload in compact.items():
            before = old.get(key)
            if isinstance(before, dict):
                before = before.get("value")
            try:
                before_num = float(before)
            except (TypeError, ValueError):
                changed = True
                break
            if abs(float(payload["value"]) - before_num) >= 0.05:
                changed = True
                break

    if changed:
        if correction:
            lines = ["[Silver Bulletin 트럼프 이슈 지지율 감시] 기준값 정정", ""]
            lines.extend(current_values_only(compact))
            lines += [
                "",
                "→ 이전 시험 메시지는 원시 여론조사 표를 이슈 평균으로 잘못 읽은 값이라 폐기했습니다.",
                "→ 앞으로 Silver Bulletin의 4개 이슈 평균 차트만 추적하고, 값이 실제로 바뀔 때만 알립니다.",
            ]
        elif not old:
            lines = ["[Silver Bulletin 트럼프 이슈 지지율 감시] Telegram 연결 완료", ""]
            lines.extend(current_values_only(compact))
            lines += ["", "→ 현재 4개 이슈 평균을 기준값으로 저장했습니다. 이후 값이 바뀔 때만 알립니다."]
        else:
            lines = ["[Silver Bulletin 트럼프 이슈 지지율 감시] 변화 감지", ""]
            deltas = []
            for key in ("cost_of_living", "economy", "immigration", "trade"):
                now = float(compact[key]["value"])
                before = old.get(key)
                if isinstance(before, dict):
                    before = before.get("value")
                try:
                    before_num = float(before)
                except (TypeError, ValueError):
                    before_num = None
                if before_num is None:
                    lines.append(f"- {ISSUES[key][0]}: {fmt(now)}")
                else:
                    delta = now - before_num
                    deltas.append((abs(delta), ISSUES[key][0], delta))
                    lines.append(f"- {ISSUES[key][0]}: {fmt(before_num)} → {fmt(now)} ({delta:+.1f}%p)")
            if deltas:
                _, label, delta = max(deltas)
                lines += ["", f"→ 가장 큰 변화: {label} {abs(delta):.1f}%p {'개선' if delta > 0 else '악화' if delta < 0 else '변화 없음'}"]

        if data_date:
            lines += ["", f"- 기준일: {data_date}"]
        lines += [f"- 원문: {PAGE_URL}"]
        ALERT.write_text("\n".join(lines) + "\n", encoding="utf-8")
        PENDING.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    STATUS.write_text(
        "Silver Bulletin 4개 이슈 평균 확인 완료 — "
        + ("기준값 정정" if correction else "기준값/변화 감지" if changed else "변화 없음")
        + f" — 데이터셋 {base}"
        + (f" — 기준일 {data_date}" if data_date else "")
        + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
