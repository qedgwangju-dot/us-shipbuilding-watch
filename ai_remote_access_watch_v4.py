import hashlib
import html
import os
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

import ai_remote_access_watch as base
import ai_remote_access_watch_v2 as v2
import ai_remote_access_watch_v3 as v3


# v3를 import하면 Google News 제목의 '- 매체명' 꼬리표 제거가 base에 적용된다.
_original_meaningful_snapshot = base.meaningful_snapshot
KST = ZoneInfo("Asia/Seoul")


def _norm(text: str) -> str:
    text = (text or "").lower()
    text = re.sub(r"[^a-z0-9가-힣]+", " ", text)
    return " ".join(text.split())


def _extract_bill_fields(raw: str):
    text = base.clean(raw)

    def pick(pattern, flags=re.I | re.S):
        m = re.search(pattern, text, flags=flags)
        return base.clean(m.group(1)) if m else ""

    bill = pick(r"Bill Number\s+(H\.\s*R\.\s*\d+|S\.\s*\d+)")
    last_action = pick(r"Last Action Date Listed\s+([A-Z][a-z]+\s+\d{1,2},\s+\d{4})")
    action = pick(r"\bAction\s+(.+?)\s+Bill Number\b")
    version = pick(r"Bill Version\s+(.+?)\s+(?:Short Title|Full Title)\b")

    action = re.sub(r"^(?:Toggle\s+)?Actions?\s+", "", action, flags=re.I).strip()
    return bill, last_action, action, version


def _iso_us_date(value: str) -> str:
    try:
        return datetime.strptime((value or "").strip(), "%B %d, %Y").strftime("%Y-%m-%d")
    except Exception:
        return (value or "").strip()


def _bill_fields_from_item(item):
    summary = item.get("summary", "") or ""

    def pick(key):
        m = re.search(rf"(?:^|\|)\s*{re.escape(key)}=(.*?)(?=\s*\|\s*[a-z_]+=|$)", summary, flags=re.I | re.S)
        return base.clean(m.group(1)) if m else ""

    return {
        "bill": pick("bill"),
        "last_action": pick("last_action"),
        "action": pick("action"),
        "version": pick("version"),
    }


def _rss_date_line(item) -> str:
    raw = (item.get("rss_pub_date") or "").strip()
    if not raw:
        return ""
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        kst = dt.astimezone(KST)
        return f"RSS 공개시각: <b>{kst.strftime('%Y-%m-%d %H:%M')} KST</b>"
    except Exception:
        return f"RSS 공개시각: <b>{html.escape(raw)}</b>"


def meaningful_snapshot(page):
    """GovInfo 법안 페이지의 동적 UI 전체가 아니라 실제 입법 상태만 해시한다."""
    raw = base.get_text(page["url"])

    if page["kind"] == "bill":
        bill, last_action, action, version = _extract_bill_fields(raw)
        if not bill:
            m = re.search(r"(H\.R\.\s*\d+|S\.\s*\d+)", page["name"], flags=re.I)
            bill = m.group(1) if m else page["name"]

        if action or last_action:
            snapshot = " | ".join(
                x for x in [
                    f"bill={bill}",
                    f"last_action={last_action}" if last_action else "",
                    f"action={action}" if action else "",
                    f"version={version}" if version else "",
                ] if x
            )
        else:
            snapshot = f"bill={bill} | status_parse_unavailable"

        digest = hashlib.sha256(snapshot.encode("utf-8")).hexdigest()
        item = {
            "source": page["name"],
            "title": page["name"],
            "url": page["url"],
            "summary": snapshot,
            "stage": "공식",
        }
        return digest, snapshot, item

    return _original_meaningful_snapshot(page)


def _new_action(snapshot: str) -> str:
    m = re.search(r"action=(.*?)(?:\s*\|\s*version=|$)", snapshot or "", flags=re.I | re.S)
    return _norm(m.group(1)) if m else ""


def _same_semantic_bill_status(old_snapshot: str, new_snapshot: str) -> bool:
    """이전 버전의 불안정한 스냅샷에서 같은 입법 행동이면 마이그레이션 알림을 막는다."""
    old_n = _norm(old_snapshot)
    new_n = _norm(new_snapshot)
    if old_n == new_n:
        return True

    action = _new_action(new_snapshot)
    if action and action in old_n:
        return True

    anchors = [
        "received read twice and referred to the committee on banking housing and urban affairs",
        "introduced the following bill which was read twice and referred to the committee on banking housing and urban affairs",
    ]
    for anchor in anchors:
        if anchor in old_n and anchor in new_n:
            return True
    return False


def _append_time_lines(message: str, item) -> str:
    rss_line = _rss_date_line(item)
    if not rss_line:
        return message

    # 출처 바로 아래에는 기사 RSS 공개시각만 표시한다. 감시 확인시각은 표시하지 않는다.
    pattern = r"(출처: <a href=\"[^\"]+\">.*?</a>)\n\n"
    if re.search(pattern, message):
        return re.sub(pattern, rf"\1\n{rss_line}\n\n", message, count=1)
    return message


def build_message(item):
    low = f"{item.get('title', '')} {item.get('summary', '')}".lower()
    safe_url = html.escape(item["url"], quote=True)
    fields = _bill_fields_from_item(item)
    official_date = _iso_us_date(fields.get("last_action", ""))

    if "h.r.2683" in low or "h.r. 2683" in low or "hr2683" in low:
        action_line = (
            f"• 최종 공식 행동일: <b>{html.escape(official_date)}</b> (미 의회 현지일·GovInfo 기준)\n"
            if official_date else ""
        )
        message = (
            "🚨 <b>미국 AI 칩·원격접근 규제 중요 변화</b>\n\n"
            "<b>미국 Remote Access Security Act H.R.2683 진행 상황 변화</b>\n"
            f"출처: <a href=\"{safe_url}\">Remote Access Security Act H.R.2683</a>\n\n"
            "• 단계: 미국 의회 공식 입법 변화\n"
            f"{action_line}"
            "• 진행 경로: <b>2026-01-12</b> 하원 369대22 통과 → <b>2026-01-13</b> 상원 접수·2회 낭독 후 Senate Banking Committee 회부\n"
            "• 핵심: 인터넷·클라우드를 통한 통제 품목의 원격접근을 Export Control Reform Act의 규제 범위에 명시하려는 법안\n"
            "• 투자 관점: 상원 통과·법제화 시 BIS가 중국 기업의 해외 데이터센터 원격 GPU 접근을 직접 통제할 법적 기반이 강화\n"
            "• 다음 확인: Senate Banking Committee 심사·수정안·위원회 표결·상원 본회의·하원 재의결 필요 여부\n\n"
            f"<a href=\"{safe_url}\"><b>원문</b></a>"
        )
        return _append_time_lines(message, item)

    if "s.3519" in low or "s. 3519" in low or "s3519" in low:
        action_line = (
            f"• 최종 공식 행동일: <b>{html.escape(official_date)}</b> (미 의회 현지일·GovInfo 기준)\n"
            if official_date else ""
        )
        message = (
            "🚨 <b>미국 AI 칩·원격접근 규제 중요 변화</b>\n\n"
            "<b>미국 Remote Access Security Act S.3519 진행 상황 변화</b>\n"
            f"출처: <a href=\"{safe_url}\">Remote Access Security Act S.3519</a>\n\n"
            "• 단계: 미국 의회 공식 입법 변화\n"
            f"{action_line}"
            "• 진행 경로: <b>2025-12-17</b> 상원 발의·2회 낭독 후 Senate Banking Committee 회부 — 이후 공식 입법 행동이 바뀔 때만 알림\n"
            "• 핵심: Export Control Reform Act를 개정해 통제 품목의 원격접근을 수출통제 범위에 포함하려는 상원 법안\n"
            "• 투자 관점: 상원 심사 진전 시 H.R.2683과의 문안 조정·통합 가능성이 중요하며, 실제 법제화 전까지 NVIDIA·HBM 실적 영향은 규제 기대 단계\n"
            "• 다음 확인: Senate Banking Committee 심사·공동발의자·수정안·위원회 표결·H.R.2683과의 통합 여부\n\n"
            f"<a href=\"{safe_url}\"><b>원문</b></a>"
        )
        return _append_time_lines(message, item)

    return _append_time_lines(v2.build_message(item), item)


def page_is_bill(name: str) -> bool:
    return "Remote Access Security Act H.R.2683" in name or "Remote Access Security Act S.3519" in name


def main():
    old = base.load_state()
    listings, tracked = base.collect()
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
        old_data = old_tracked.get(name) or {}
        old_digest = old_data.get("digest")
        old_snapshot = old_data.get("snapshot", "")

        changed = bool(old and old_digest and old_digest != data["digest"])
        if changed and page_is_bill(name):
            if _same_semantic_bill_status(old_snapshot, data["snapshot"]):
                changed = False
                print(f"[AI DEDUPE] {name}: 실제 입법 상태 동일 — 중복 알림 차단")

        if changed:
            alerts.append(data["item"])
        new["tracked"][name] = {"digest": data["digest"], "snapshot": data["snapshot"][:2500]}

    if not old:
        base.save_state(new)
        print("[AI BASELINE] 첫 실행: 현재 자료를 기준선으로 저장하고 과거 알림은 보내지 않음")
        return

    token = os.environ.get("AI_REMOTE_TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("AI_REMOTE_TELEGRAM_CHAT_ID", "").strip()
    if alerts and (not token or not chat_id):
        print("[AI PENDING] 새 변화가 있으나 텔레그램 Secret 미설정 — 상태를 갱신하지 않음")
        return

    sent = 0
    for item in alerts[:8]:
        try:
            base.send_telegram(build_message(item))
            sent += 1
            print(f"[AI SENT] {item['source']} - {item['title']}")
        except Exception as e:
            print(f"[AI SEND FAIL] {e}")
            return

    base.save_state(new)
    print(f"[AI DONE] 신규 알림 {sent}건")


base.meaningful_snapshot = meaningful_snapshot
base.build_message = build_message


if __name__ == "__main__":
    main()
