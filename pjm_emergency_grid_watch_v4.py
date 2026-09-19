import html
import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

import pjm_emergency_grid_watch as base
import pjm_emergency_grid_watch_v3 as v3

EPT = ZoneInfo("America/New_York")
KST = ZoneInfo("Asia/Seoul")


def ko_date_from_english(value: str) -> str:
    value = (value or "").strip()
    for fmt in ("%B %d, %Y", "%b %d, %Y"):
        try:
            dt = datetime.strptime(value, fmt)
            return f"{dt.year}년 {dt.month}월 {dt.day}일"
        except Exception:
            pass
    return value


def ko_datetime_ept(value: str) -> tuple[str, str]:
    try:
        dt = datetime.strptime((value or "").strip(), "%m.%d.%Y %H:%M").replace(tzinfo=EPT)
        ept = f"{dt.year}년 {dt.month}월 {dt.day}일 {dt.strftime('%H:%M')} EPT"
        k = dt.astimezone(KST)
        kst = f"{k.year}년 {k.month}월 {k.day}일 {k.strftime('%H:%M')} KST"
        return ept, kst
    except Exception:
        return value, ""


def kst_now_ko():
    dt = datetime.now(KST)
    return f"{dt.year}년 {dt.month}월 {dt.day}일 {dt.strftime('%H:%M')} KST"


def parse_doe_pjm_orders_strict():
    raw = base.get(base.DOE_ORDERS_URL)
    soup = BeautifulSoup(raw, "html.parser")
    items = {}

    # 각 202(c) 명령의 자체 블록 안에 PJM이 명시된 경우만 수집한다.
    # 이전 버전처럼 앞뒤 500~900자 문맥을 붙이지 않아 인접한 MISO/NIPSCO 명령을 PJM으로 오인하지 않는다.
    candidates = soup.find_all(["li", "p"])
    for node in candidates:
        text = base.clean(node.get_text(" ", strip=True))
        m = re.search(r"(?:DOE\s+)?Order\s+No\.\s*(202-\d{2}-\d+[A-Z]?)", text, re.I)
        if not m:
            continue

        order_no = m.group(1).upper()
        low = text.lower()
        is_pjm = (
            "pjm interconnection" in low
            or "to pjm" in low
            or "(pjm)" in low
        )
        if not is_pjm:
            continue

        date_m = re.search(r"On\s+([A-Z][a-z]+\s+\d{1,2},\s+2026)", text)
        app_m = re.search(r"application(?: from PJM)?(?: submitted)?(?: on)?\s+([A-Z][a-z]+\s+\d{1,2},\s+2026)", text, re.I)
        begin_m = re.search(r"(?:in effect beginning|effective)(?: at [^,]+ on| on)?\s+([A-Z][a-z]+\s+\d{1,2},\s+2026)", text, re.I)
        end_m = re.search(r"(?:through|expire(?:s|d)?(?: at .*? on)?)\s+([A-Z][a-z]+\s+\d{1,2},\s+2026)", text, re.I)

        items[order_no] = {
            "order_no": order_no,
            "date": date_m.group(1) if date_m else "",
            "application_date": app_m.group(1) if app_m else "",
            "effective_date": begin_m.group(1) if begin_m else "",
            "end_date": end_m.group(1) if end_m else "",
            "context": text,
            "url": base.DOE_ORDERS_URL,
        }

    return items


def build_doe_order_message_ko(item):
    order_no = item["order_no"]
    url = html.escape(item["url"], quote=True)

    lines = []
    if item.get("application_date"):
        lines.append(f"PJM 신청일: <b>{html.escape(ko_date_from_english(item['application_date']))}</b>")
    if item.get("date"):
        lines.append(f"DOE 발령일: <b>{html.escape(ko_date_from_english(item['date']))}</b>")
    if item.get("effective_date"):
        lines.append(f"효력 시작일: <b>{html.escape(ko_date_from_english(item['effective_date']))}</b>")
    if item.get("end_date"):
        lines.append(f"종료 예정일: <b>{html.escape(ko_date_from_english(item['end_date']))}</b>")

    timeline = "\n".join(lines)
    if timeline:
        timeline += "\n"

    return (
        "🚨 <b>미국 전력망 비상운영·PJM 중요 변화</b>\n\n"
        f"<b>DOE, PJM에 202(c) 긴급명령 {html.escape(order_no)} 발령</b>\n"
        f"출처: <a href=\"{url}\">미 에너지부 202(c) 공식 명령 목록</a>\n"
        f"{timeline}"
        f"확인시각: {kst_now_ko()}\n\n"
        "• 단계: 연방전력법 202(c) 긴급명령\n"
        "• 의미: PJM이 지정 발전자원과 대형부하의 비상 백업발전을 신뢰도 유지에 필요한 경우 동원할 수 있는 권한 확보\n"
        "• 구분: 수요반응 호출과 별개의 공급측 비상수단이며, 일반 고객 순환정전이나 상시 시장참여 허용을 뜻하지 않음\n"
        "• 다음 확인: PJM 지정자원 사용 필요 판정, 실제 동원 MW·MWh, EEA 단계, 명령 연장·종료\n\n"
        f"<a href=\"{url}\"><b>원문</b></a>"
    )


def build_pjm_message_ko(item):
    # v3의 내용 판정은 유지하고, 날짜 표기만 한국식으로 재구성한다.
    body = item["body"]
    low = body.lower()
    title = "PJM 전력망 비상운영 단계 변화"
    bullets = []

    if "has requested a 202(c) order" in low or "request for 202(c) emergency use" in low:
        title = "PJM, DOE에 202(c) 긴급명령 신청…백업발전·배출제한 발전 동원 요청"
        bullets.append("단계: PJM 신청 — 아직 DOE 발령 전 단계")
        bullets.append("달라진 점: PJM이 대형부하 백업발전과 배출제한 발전자원을 비상수단으로 사용할 권한을 DOE에 공식 요청")
    elif "202-26-45" in low or "backup generation" in low or "back-up generators" in low:
        title = "PJM, DOE 202(c) 긴급명령에 따른 백업발전·지정자원 운용 상태 변화"
        if "has identified a reliability need" in low:
            bullets.append("달라진 점: DOE 발령 이후 PJM이 지정자원의 실제 사용 필요를 공식 판단")
        if "has not identified a reliability need" in low:
            bullets.append("달라진 점: PJM이 다음 운영일에는 지정자원 추가 사용이 필요 없다고 판단")
        if "not expected to continue past" in low:
            bullets.append("현재 판정: 비상계통 상황의 지속 가능성이 낮아졌다고 공지")
        if "does not constitute a directive to operate at maximum output" in low:
            bullets.append("주의: 지정설비 최대출력을 자동 지시하는 명령이 아니라 PJM 급전지시에 따라 필요한 만큼 운전")
    elif "eea2" in low or "nerc level eea2" in low:
        title = "PJM, EEA2·긴급 수요감축 발령"
        bullets.append("달라진 점: 단순 사전경보를 넘어 NERC EEA2·긴급 수요감축 실행 단계로 상승")
    elif "eea1" in low or "nerc eea 1" in low:
        title = "PJM, EEA1·최대발전 비상경보 발령"
        bullets.append("달라진 점: 운영예비력 압박에 대응해 최대발전·부하관리 경보 단계로 상승")
    elif "load mgmt reduction action" in low:
        title = "PJM, 수요반응 긴급감축 실행"
        bullets.append("달라진 점: Capacity Performance 수요반응 자원이 실제 감축 실행 단계로 진입")

    if item.get("regions"):
        bullets.append(f"적용 지역: {item['regions']}")

    if item.get("end"):
        end_ept, end_kst = ko_datetime_ept(item["end"])
        if end_kst:
            bullets.append(f"유효 종료시각: {end_ept} → {end_kst}")
        else:
            bullets.append(f"유효 종료시각: {end_ept}")

    bullets.append("다음 확인: EEA 단계 상·하향, 수요반응 해제, 백업발전 실제 동원 MW·MWh, 지정자원 목록·운전지시, 명령 연장·종료")

    start = item.get("start") or ""
    timeline = ""
    if start:
        start_ept, start_kst = ko_datetime_ept(start)
        timeline = f"공식 발효시각: <b>{html.escape(start_ept)}</b>"
        if start_kst:
            timeline += f" → <b>{html.escape(start_kst)}</b>"
        timeline += "\n"

    url = html.escape(item["url"], quote=True)
    return (
        "🚨 <b>미국 전력망 비상운영·PJM 중요 변화</b>\n\n"
        f"<b>{html.escape(title)}</b>\n"
        f"출처: <a href=\"{url}\">PJM Emergency Procedures</a>\n"
        f"{timeline}"
        f"확인시각: {kst_now_ko()}\n\n"
        + "\n".join(f"• {html.escape(x)}" for x in bullets[:6])
        + f"\n\n<a href=\"{url}\"><b>원문</b></a>"
    )


def sanitize_state_to_actual_pjm_orders():
    state = base.load_state()
    try:
        actual = set(parse_doe_pjm_orders_strict().keys())
    except Exception:
        return
    old = set(state.get("doe_order_nos", []))
    cleaned = sorted(old & actual)
    if cleaned != sorted(old):
        print(f"[PJM STATE CLEAN] 비-PJM 오인식 DOE 명령 제거: {sorted(old - actual)}")
        state["doe_order_nos"] = cleaned
        base.save_state(state)


base.parse_doe_pjm_orders = parse_doe_pjm_orders_strict
base.build_doe_order_message = build_doe_order_message_ko
base.build_pjm_message = build_pjm_message_ko
base.kst_now = kst_now_ko

if __name__ == "__main__":
    sanitize_state_to_actual_pjm_orders()
    base.main()
