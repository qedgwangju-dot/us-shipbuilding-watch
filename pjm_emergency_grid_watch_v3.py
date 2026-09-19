import html
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

import pjm_emergency_grid_watch as base
import pjm_emergency_grid_watch_v2 as v2

EPT = ZoneInfo("America/New_York")
KST = ZoneInfo("Asia/Seoul")


def _parse_ept(value: str):
    try:
        return datetime.strptime((value or "").strip(), "%m.%d.%Y %H:%M").replace(tzinfo=EPT)
    except Exception:
        return None


def parse_pjm_messages_fresh():
    raw = base.get(base.PJM_DASHBOARD_URL)
    text = BeautifulSoup(raw, "html.parser").get_text(" ", strip=True)
    chunks = re.split(r"(?=Msg ID:\s*\d+)", text)
    items = {}
    now = datetime.now(EPT)

    for chunk in chunks:
        mid = re.search(r"Msg ID:\s*(\d+)", chunk)
        if not mid:
            continue
        msg_id = mid.group(1)
        mt = re.search(r"Message Type:\s*(.*?)(?=Priority:)", chunk, re.I)
        pr = re.search(r"Priority:\s*(.*?)(?=Effective Start Time:)", chunk, re.I)
        st = re.search(r"Effective Start Time:\s*(.*?)(?=Effective End Time:|Regions)", chunk, re.I)
        en = re.search(r"Effective End Time:\s*(.*?)(?=Regions)", chunk, re.I)
        rg = re.search(r"Regions\s+(.*?)(?=A |An |The |Additional Comments:|Msg ID:|$)", chunk, re.I)

        msg_type = base.clean(mt.group(1) if mt else "")
        priority = base.clean(pr.group(1) if pr else "")
        start = base.clean(st.group(1) if st else "")
        end = base.clean(en.group(1) if en else "")
        regions = base.clean(rg.group(1) if rg else "")
        body = base.clean(chunk)

        material = any(x.lower() in msg_type.lower() for x in base.MATERIAL_PJM_TYPES) or any(
            x.lower() in body.lower() for x in base.MATERIAL_PJM_TEXT
        )
        if "Post Contingency Local Load Relief Warning" in msg_type and not any(
            x.lower() in body.lower() for x in ("eea", "doe 202(c)", "backup generation")
        ):
            material = False
        if not material:
            continue

        start_dt = _parse_ept(start)
        end_dt = _parse_ept(end)

        # 핵심 게이트: '새 URL/새 Msg ID'가 아니라 현재 유효한 운영 변화만 알린다.
        # 이미 종료된 과거 경보가 PJM 검색범위에 뒤늦게 노출되어도 신규 알림으로 취급하지 않는다.
        if end_dt and end_dt < now:
            print(f"[PJM STALE] 종료된 과거 메시지 제외: {msg_id} {msg_type} | {end}")
            continue

        # 종료시각이 없는 메시지도 시작 후 24시간을 넘겨 처음 발견되면 과거 재노출로 본다.
        if not end_dt and start_dt and start_dt < now - timedelta(hours=24):
            print(f"[PJM STALE] 24시간 초과 과거 메시지 제외: {msg_id} {msg_type} | {start}")
            continue

        items[msg_id] = {
            "id": msg_id,
            "type": msg_type,
            "priority": priority,
            "start": start,
            "end": end,
            "regions": regions,
            "body": body,
            "url": f"https://emergencyprocedures.pjm.com/ep/pages/viewposting.jsf?id={msg_id}",
        }
    return items


def build_pjm_message_v3(item):
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
        bullets.append(f"유효 종료시각: {item['end']} EPT")
    bullets.append("다음 확인: EEA 단계 상·하향, 수요반응 해제, 백업발전 실제 동원 MW·MWh, 지정자원 목록·운전지시, 명령 연장·종료")

    start = item.get("start") or ""
    start_dt = _parse_ept(start)
    timeline = ""
    if start:
        timeline = f"공식 발효시각: <b>{html.escape(start)} EPT</b>"
        if start_dt:
            timeline += f" → <b>{start_dt.astimezone(KST).strftime('%Y-%m-%d %H:%M KST')}</b>"
        timeline += "\n"

    url = html.escape(item["url"], quote=True)
    return (
        "🚨 <b>미국 전력망 비상운영·PJM 중요 변화</b>\n\n"
        f"<b>{html.escape(title)}</b>\n"
        f"출처: <a href=\"{url}\">PJM Emergency Procedures</a>\n"
        f"{timeline}"
        f"확인시각: {base.kst_now()}\n\n"
        + "\n".join(f"• {html.escape(x)}" for x in bullets[:6])
        + f"\n\n<a href=\"{url}\"><b>원문</b></a>"
    )


base.parse_pjm_messages = parse_pjm_messages_fresh
base.build_pjm_message = build_pjm_message_v3

if __name__ == "__main__":
    base.main()
