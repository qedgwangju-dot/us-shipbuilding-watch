import html
from datetime import datetime
from zoneinfo import ZoneInfo

import pjm_emergency_grid_watch as base

EPT = ZoneInfo("America/New_York")
KST = ZoneInfo("Asia/Seoul")


def ept_to_kst(value: str) -> str:
    try:
        dt = datetime.strptime(value.strip(), "%m.%d.%Y %H:%M").replace(tzinfo=EPT)
        return dt.astimezone(KST).strftime("%Y-%m-%d %H:%M KST")
    except Exception:
        return ""


def build_pjm_message(item):
    body = item["body"]
    low = body.lower()
    title = "PJM 전력망 비상운영 단계 변화"
    bullets = []

    if "202-26-45" in low or "backup generation" in low or "back-up generators" in low:
        title = "PJM, DOE 202(c) 긴급명령에 따른 백업발전·지정자원 운용 상태 변화"
        if "has identified a reliability need" in low:
            bullets.append("달라진 점: PJM이 DOE Order 202-26-45 지정자원을 9월 17일 운영일에 사용할 신뢰도 필요가 있다고 공식 판단")
        if "has not identified a reliability need" in low:
            bullets.append("달라진 점: PJM이 9월 18일 운영일에는 지정자원 추가 사용 필요가 없다고 공식 판단")
        if "not expected to continue past 9/17" in low:
            bullets.append("현재 판정: 비상계통 상황이 9월 17일 이후 지속될 것으로 예상하지 않는다고 공지")
        if "does not constitute a directive to operate at maximum output" in low:
            bullets.append("주의: 지정설비의 최대출력·전부하 운전을 자동 지시하는 명령이 아니라 PJM 급전지시에 따라 필요한 만큼 운전하는 구조")
    elif "eea2" in low or "nerc level eea2" in low:
        title = "PJM, EEA2·긴급 수요감축 발령"
        bullets.append("달라진 점: 단순 사전경보를 넘어 일부 지역에서 NERC EEA2와 긴급 수요감축 실행 단계로 상승")
    elif "eea1" in low or "nerc eea 1" in low:
        title = "PJM, EEA1·최대발전 비상경보 발령"
        bullets.append("달라진 점: PJM 전역의 운영예비력 압박에 대응해 최대발전·부하관리 경보 단계로 상승")
    elif "load mgmt reduction action" in low:
        title = "PJM, 수요반응 긴급감축 실행"
        bullets.append("달라진 점: Capacity Performance 수요반응 자원이 실제 감축 실행 단계로 진입")

    if item.get("regions"):
        bullets.append(f"적용 지역: {item['regions']}")
    bullets.append("다음 확인: EEA 단계 상·하향, 수요반응 해제, 백업발전 실제 동원 MW·MWh, 지정자원 목록·운전지시, 명령 연장·종료")

    start = item.get("start") or ""
    kst = ept_to_kst(start)
    if start:
        timeline = f"공식 발효시각: <b>{html.escape(start)} EPT</b>"
        if kst:
            timeline += f" → <b>{html.escape(kst)}</b>"
        timeline += "\n"
    else:
        timeline = ""

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


def build_doe_order_message(item):
    order_no = item["order_no"]
    url = html.escape(item["url"], quote=True)
    date = item.get("date", "")
    context = item.get("context", "")
    # DOE 목록 문맥에서 신청일·유효기간을 찾을 수 있으면 분리 표기한다.
    import re
    app = re.search(r"application(?: from PJM)? submitted on ([A-Z][a-z]+ \d{1,2}, 2026)", context, re.I)
    eff = re.search(r"in effect beginning(?: at [^,]+ on| on)? ([A-Z][a-z]+ \d{1,2}, 2026)", context, re.I)
    exp = re.search(r"(?:expire at .*? on|through) ([A-Z][a-z]+ \d{1,2}, 2026)", context, re.I)

    timeline = ""
    if app:
        timeline += f"PJM 신청일: <b>{html.escape(app.group(1))}</b>\n"
    if date:
        timeline += f"DOE 발령일: <b>{html.escape(date)}</b>\n"
    if eff:
        timeline += f"효력 시작일: <b>{html.escape(eff.group(1))}</b>\n"
    if exp:
        timeline += f"종료 예정일: <b>{html.escape(exp.group(1))}</b>\n"

    return (
        "🚨 <b>미국 전력망 비상운영·PJM 중요 변화</b>\n\n"
        f"<b>DOE, PJM에 202(c) 긴급명령 {html.escape(order_no)} 발령</b>\n"
        f"출처: <a href=\"{url}\">미 에너지부 202(c) 공식 명령 목록</a>\n"
        f"{timeline}"
        f"확인시각: {base.kst_now()}\n\n"
        "• 단계: 연방전력법 202(c) 긴급명령\n"
        "• 의미: PJM이 지정 발전자원과 대형부하의 비상 백업발전을 신뢰도 유지에 필요한 경우 동원할 수 있는 권한 확보\n"
        "• 구분: 수요반응 호출과 별개의 공급측 비상수단이며, 일반 고객 순환정전이나 상시 시장참여 허용을 뜻하지 않음\n"
        "• 다음 확인: PJM 지정자원 사용 필요 판정, 실제 동원 MW·MWh, EEA 단계, 명령 연장·종료\n\n"
        f"<a href=\"{url}\"><b>원문</b></a>"
    )


base.build_pjm_message = build_pjm_message
base.build_doe_order_message = build_doe_order_message

if __name__ == "__main__":
    base.main()
