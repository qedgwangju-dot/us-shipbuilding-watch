from __future__ import annotations

import csv
import datetime as dt
import html
import io
import json
import pathlib
import re
from typing import Dict, Optional, Tuple

import requests
from bs4 import BeautifulSoup

PAGE_URL = "https://www.natesilver.net/p/trump-approval-ratings-nate-silver-bulletin"
STATE = pathlib.Path("silver_bulletin_state.json")
OUT = pathlib.Path("out")
ALERT = OUT / "silver_bulletin_alert.txt"
PENDING = OUT / "silver_bulletin_state_pending.json"
STATUS = OUT / "silver_bulletin_status.txt"
DEBUG = OUT / "silver_bulletin_debug.json"
PARSER_VERSION = 4
MAX_DATA_AGE_DAYS = 7
SINGLE_CHECK_THRESHOLD_PP = 2.0
CUMULATIVE_THRESHOLD_PP = 3.0

# Silver Bulletin has historically used this Datawrapper chart id for the
# four-topic net issue average. The numeric Datawrapper revision changes when
# a new chart is published, so every run resolves the latest public revision.
FALLBACK_CHARTS = ["https://datawrapper.dwcdn.net/RFXsV/73/"]

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
    "User-Agent": "Mozilla/5.0 (compatible; KHS-Silver-Bulletin-Watch/3.0)",
    "Cache-Control": "no-cache",
}
SESSION = requests.Session()
SESSION.headers.update(HEADERS)
_DATASET_CACHE: Dict[Tuple[str, int], Optional[str]] = {}


def norm(v) -> str:
    s = html.unescape(str(v or "")).lower().replace("−", "-").replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", s).strip()


def num(v) -> Optional[float]:
    s = norm(v).replace(",", "")
    if not s or re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}", s):
        return None
    if not re.fullmatch(r"[-+]?\d+(?:\.\d+)?\s*%?", s):
        return None
    x = float(s.replace("%", "").strip())
    return x if abs(x) <= 100 else None


def issue_for_header(v) -> Optional[str]:
    # Only headers can identify an issue. Never scan raw pollster/row text.
    s = norm(v)
    for key, (_, aliases) in ISSUES.items():
        if any(s == norm(alias) for alias in aliases):
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
    for fmt in (
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%m/%d/%Y",
        "%m/%d/%y",
        "%b %d, %Y",
        "%B %d, %Y",
    ):
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
        m = re.search(r"https?://datawrapper\.dwcdn\.net/([A-Za-z0-9]+)/([0-9]+)/?", src)
        if not m:
            continue
        url = f"https://datawrapper.dwcdn.net/{m.group(1)}/{int(m.group(2))}/"
        if url not in found:
            found.append(url)
    return found


def parse_chart_url(base: str) -> Optional[Tuple[str, int]]:
    m = re.search(r"datawrapper\.dwcdn\.net/([A-Za-z0-9]+)/([0-9]+)/?", base)
    return (m.group(1), int(m.group(2))) if m else None


def dataset_text(chart_id: str, revision: int, timeout: int = 20) -> Optional[str]:
    key = (chart_id, revision)
    if key in _DATASET_CACHE:
        return _DATASET_CACHE[key]
    url = f"https://datawrapper.dwcdn.net/{chart_id}/{revision}/dataset.csv"
    try:
        r = SESSION.get(url, timeout=timeout, allow_redirects=True)
        if r.status_code == 200 and r.text.strip():
            _DATASET_CACHE[key] = r.text
        elif r.status_code in {404, 410}:
            _DATASET_CACHE[key] = None
        else:
            raise RuntimeError(f"Datawrapper 응답 오류: {chart_id}/{revision} HTTP {r.status_code}")
    except requests.RequestException as exc:
        raise RuntimeError(f"Datawrapper 연결 오류: {chart_id}/{revision}: {type(exc).__name__}") from exc
    return _DATASET_CACHE[key]


def latest_revision(chart_id: str, seed: int) -> int:
    """Find the highest contiguous public Datawrapper revision efficiently."""
    if dataset_text(chart_id, seed) is None:
        return seed

    low = seed
    step = 1
    high = seed + step
    # Exponential search for a missing upper bound.
    while high <= 65536 and dataset_text(chart_id, high) is not None:
        low = high
        step *= 2
        high = seed + step
    if high > 65536:
        high = 65537

    # Binary search assumes Datawrapper publish revisions are contiguous.
    left, right = low + 1, high - 1
    best = low
    while left <= right:
        mid = (left + right) // 2
        if dataset_text(chart_id, mid) is not None:
            best = mid
            left = mid + 1
        else:
            right = mid - 1
    return best


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
        for key, h in header_map.items():
            x = num(row.get(h))
            if x is None:
                break
            values[key] = x
        if set(values) != REQUIRED_ISSUES:
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
    return [
        f"- {ISSUES[key][0]}: {fmt(float(compact[key]['value']))}"
        for key in ("cost_of_living", "economy", "immigration", "trade")
        if key in compact
    ]


def main() -> int:
    OUT.mkdir(exist_ok=True)
    for p in (ALERT, PENDING, STATUS, DEBUG):
        p.unlink(missing_ok=True)

    debug = {"parser_version": PARSER_VERSION, "page": PAGE_URL, "charts": [], "errors": []}
    try:
        response = SESSION.get(PAGE_URL, timeout=30)
        response.raise_for_status()
        charts = discover_charts(response.text)
    except Exception as e:
        debug["errors"].append(f"page: {type(e).__name__}: {e}")
        DEBUG.write_text(json.dumps(debug, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        STATUS.write_text("Silver Bulletin 공식 페이지 확인 실패. 과거 값을 재사용하거나 Telegram으로 보내지 않습니다.\n", encoding="utf-8")
        return 2
    if not any("RFXsV/" in chart for chart in charts):
        debug["errors"].append("공식 페이지에서 이슈별 차트 RFXsV 링크가 확인되지 않음")
        DEBUG.write_text(json.dumps(debug, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        STATUS.write_text("Silver Bulletin 공식 이슈 차트 식별 실패. 알림 중단 및 재검증 필요.\n", encoding="utf-8")
        return 2

    datasets = []
    seen_ids = set()
    for embedded in charts:
        parsed = parse_chart_url(embedded)
        if not parsed:
            continue
        chart_id, embedded_revision = parsed
        if chart_id in seen_ids:
            continue
        seen_ids.add(chart_id)

        # First inspect the embedded revision. If it has the four issue columns,
        # resolve the latest revision of this same chart id before reading values.
        embedded_text = dataset_text(chart_id, embedded_revision)
        item = {
            "embedded_chart": embedded,
            "chart_id": chart_id,
            "embedded_revision": embedded_revision,
        }
        try:
            if embedded_text is None:
                item["error"] = "embedded dataset unavailable"
                debug["charts"].append(item)
                continue
            embedded_headers, _ = parse_csv(embedded_text)
            issue_headers = {key: h for h in embedded_headers if (key := issue_for_header(h))}
            item["embedded_headers"] = embedded_headers
            item["recognized_issue_headers"] = issue_headers
            if set(issue_headers) != REQUIRED_ISSUES:
                item["is_issue_average"] = False
                debug["charts"].append(item)
                continue

            latest = latest_revision(chart_id, embedded_revision)
            latest_text = dataset_text(chart_id, latest)
            if latest_text is None:
                raise RuntimeError("latest dataset unavailable")
            headers, rows = parse_csv(latest_text)
            extracted = extract_issue_average(headers, rows)
            if not extracted:
                raise RuntimeError("latest revision no longer exposes all four issue columns")

            latest_base = f"https://datawrapper.dwcdn.net/{chart_id}/{latest}/"
            item.update(
                {
                    "is_issue_average": True,
                    "latest_revision": latest,
                    "latest_chart": latest_base,
                    "latest_headers": headers,
                    "latest_rows": len(rows),
                    "latest_data_date": extracted.get("date", ""),
                }
            )
            datasets.append((latest_base, extracted, latest))
        except Exception as e:
            item["error"] = f"{type(e).__name__}: {e}"
            debug["errors"].append(f"{chart_id}: {item['error']}")
        debug["charts"].append(item)

    if not datasets:
        DEBUG.write_text(json.dumps(debug, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        STATUS.write_text(
            "Silver Bulletin 확인 불가: 최신 4개 이슈 평균 데이터셋을 찾지 못했습니다. 기존 기준값 보존, Telegram 미발송.\n",
            encoding="utf-8",
        )
        return 2

    def dataset_rank(item):
        _, payload, revision = item
        d = parse_date(payload.get("date"))
        return (d.timestamp() if d else -1, revision)

    base, extracted, revision = max(datasets, key=dataset_rank)
    values = extracted["values"]
    data_date = extracted.get("date", "")
    data_dt = parse_date(data_date)
    if not data_dt:
        debug["errors"].append("selected dataset has no parseable data date")
        DEBUG.write_text(json.dumps(debug, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        STATUS.write_text(
            "Silver Bulletin 확인 불가: 최신 이슈 평균의 기준일을 확인하지 못했습니다. 기존 기준값 보존, Telegram 미발송.\n",
            encoding="utf-8",
        )
        return 2

    age_days = (dt.datetime.now(dt.timezone.utc).date() - data_dt.date()).days
    debug["selected_issue_chart"] = base
    debug["selected_revision"] = revision
    debug["selected_data_date"] = data_date
    debug["selected_data_age_days"] = age_days
    debug["selected_values"] = values

    if age_days < 0 or age_days > MAX_DATA_AGE_DAYS:
        debug["errors"].append(f"stale issue dataset: age_days={age_days}")
        DEBUG.write_text(json.dumps(debug, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        STATUS.write_text(
            f"Silver Bulletin 확인 불가: 최신 공개 이슈 데이터 기준일이 {data_date}로 {age_days}일 경과했습니다. 기존 기준값 보존, Telegram 미발송.\n",
            encoding="utf-8",
        )
        return 2

    DEBUG.write_text(json.dumps(debug, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    previous = load_state()
    old_values = previous.get("values", {}) if isinstance(previous, dict) else {}
    if not isinstance(old_values, dict):
        old_values = {}
    prior_date = str(previous.get("data_date") or "") if isinstance(previous, dict) else ""
    if prior_date and data_date < prior_date:
        debug["errors"].append(f"과거 날짜로 역행: {prior_date} -> {data_date}")
        DEBUG.write_text(json.dumps(debug, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        STATUS.write_text("데이터 날짜 역행 감지. 과거 값 재전송 및 기준값 덮어쓰기 차단.\n", encoding="utf-8")
        return 2

    compact = {
        key: {"value": round(float(values[key]), 6), "chart": base}
        for key in ("cost_of_living", "economy", "immigration", "trade")
    }
    prior_alert_values = previous.get("last_alert_values", old_values)
    if not isinstance(prior_alert_values, dict):
        prior_alert_values = {}
    has_baseline = all(key in old_values for key in REQUIRED_ISSUES)
    has_alert_baseline = all(key in prior_alert_values for key in REQUIRED_ISSUES)

    def as_float(mapping: dict, key: str) -> float:
        item = mapping[key]
        return float(item["value"] if isinstance(item, dict) else item)

    def max_change(baseline: dict) -> float:
        return max(abs(float(compact[key]["value"]) - as_float(baseline, key)) for key in REQUIRED_ISSUES)

    step_change = max_change(old_values) if has_baseline else 0.0
    cumulative_change = max_change(prior_alert_values) if has_alert_baseline else 0.0
    notify = (
        has_baseline
        and has_alert_baseline
        and (
            step_change >= SINGLE_CHECK_THRESHOLD_PP
            or cumulative_change >= CUMULATIVE_THRESHOLD_PP
        )
    )
    # Legacy migration: v3 had only the last successfully sent snapshot.
    # Its values remain the alert baseline, preventing false startup alerts.
    current = {
        "parser_version": PARSER_VERSION,
        "source": PAGE_URL,
        "issue_chart": base,
        "datawrapper_revision": revision,
        "data_date": data_date,
        "checked_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "values": compact,
        "last_alert_values": compact if notify or not has_alert_baseline else prior_alert_values,
        "last_alert_data_date": (
            data_date if notify or not has_alert_baseline
            else str(previous.get("last_alert_data_date") or prior_date)
        ),
    }
    # Store observed values even if no alert: this decouples freshness from
    # alerting and makes the 2 pp single-check / 3 pp cumulative rules accurate.
    state_changed = (
        not has_baseline
        or previous.get("parser_version") != PARSER_VERSION
        or previous.get("data_date") != data_date
        or previous.get("datawrapper_revision") != revision
        or any(
            abs(as_float(old_values, key) - float(compact[key]["value"])) > 1e-7
            for key in REQUIRED_ISSUES
        )
        or notify
    )

    if notify:
        lines = ["[Silver Bulletin 트럼프 이슈 지지율 감시] 변화 감지", ""]
        deltas = []
        for key in ("cost_of_living", "economy", "immigration", "trade"):
            now_value = float(compact[key]["value"])
            before = as_float(prior_alert_values, key)
            delta = now_value - before
            label = ISSUES[key][0]
            deltas.append((abs(delta), label, delta))
            lines.append(f"- {label}: {fmt(before)} → {fmt(now_value)} ({delta:+.1f}%p)")
        _, max_label, biggest_delta = max(deltas)
        lines.extend([
            "",
            f"→ 가장 큰 변화: {max_label} {abs(biggest_delta):.1f}%p "
            + ("개선" if biggest_delta > 0 else "악화" if biggest_delta < 0 else "변화 없음"),
            "",
            f"- 기준일: {data_date}",
            f"- 원문: {PAGE_URL}",
        ])
        ALERT.write_text("\n".join(lines) + "\n", encoding="utf-8")

    if state_changed:
        PENDING.write_text(
            json.dumps(current, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    DEBUG.write_text(json.dumps({
        **debug,
        "threshold_step_pp": SINGLE_CHECK_THRESHOLD_PP,
        "threshold_cumulative_pp": CUMULATIVE_THRESHOLD_PP,
        "max_step_change_pp": round(step_change, 6),
        "max_cumulative_change_pp": round(cumulative_change, 6),
        "notification_required": notify,
        "state_refresh_required": state_changed,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    STATUS.write_text(
        f"Silver Bulletin 4개 이슈 검증 완료 — 기준일 {data_date} "
        f"— Datawrapper {revision} — 최대 직전변화 {step_change:.3f}%p "
        f"— 최대 누적변화 {cumulative_change:.3f}%p "
        f"— 알림 {'발송 예정' if notify else '미발송(기준 미충족)'}"
        f" — 상태 갱신 {'필요' if state_changed else '불필요'}\n",
        encoding="utf-8",
    )
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
