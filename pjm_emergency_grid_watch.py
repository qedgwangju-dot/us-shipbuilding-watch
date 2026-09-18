import html
import json
import os
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

STATE_FILE = Path("pjm_emergency_grid_state.json")
TIMEOUT = 30
KST = ZoneInfo("Asia/Seoul")
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; PJM-Emergency-Watch/1.0)"}

DOE_ORDERS_URL = "https://www.energy.gov/ceser/2026-doe-202c-orders"
DOE_ARTICLES_URL = "https://www.energy.gov/articles"
PJM_DASHBOARD_URL = "https://emergencyprocedures.pjm.com/ep/pages/dashboard.jsf"

MATERIAL_PJM_TYPES = (
    "Emergency Load Mgmt Reduction Action",
    "Pre-Emergency Load Mgmt Reduction Action",
    "Maximum Generation Emergency",
    "Capacity Emergency",
    "NERC EEA 1",
    "NERC EEA 2",
    "NERC EEA 3",
    "Manual Load Dump",
)

MATERIAL_PJM_TEXT = (
    "DOE 202(c)",
    "backup generation",
    "back-up generators",
    "Emergency Back-up Generation",
    "specified resources",
    "EEA1",
    "EEA2",
    "EEA3",
    "load management",
    "Maximum Generation Emergency",
)

def get(url: str) -> str:
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r.text

def load_state():
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}

def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

def clean(text: str) -> str:
    return " ".join((text or "").split())

def parse_pjm_messages():
    raw = get(PJM_DASHBOARD_URL)
    text = BeautifulSoup(raw, "html.parser").get_text(" ", strip=True)
    # JSF 표가 평문으로 렌더되는 구조를 이용해 메시지 단위로 자른다.
    chunks = re.split(r"(?=Msg ID:\s*\d+)", text)
    items = {}
    for chunk in chunks:
        mid = re.search(r"Msg ID:\s*(\d+)", chunk)
        if not mid:
            continue
        msg_id = mid.group(1)
        mt = re.search(r"Message Type:\s*(.*?)(?=Priority:)", chunk, re.I)
        pr = re.search(r"Priority:\s*(.*?)(?=Effective Start Time:)", chunk, re.I)
        st = re.search(r"Effective Start Time:\s*(.*?)(?=Effective End Time:|Regions)", chunk, re.I)
        rg = re.search(r"Regions\s+(.*?)(?=A |An |The |Additional Comments:|Msg ID:|$)", chunk, re.I)
        msg_type = clean(mt.group(1) if mt else "")
        priority = clean(pr.group(1) if pr else "")
        start = clean(st.group(1) if st else "")
        regions = clean(rg.group(1) if rg else "")
        body = clean(chunk)

        material = any(x.lower() in msg_type.lower() for x in MATERIAL_PJM_TYPES) or any(
            x.lower() in body.lower() for x in MATERIAL_PJM_TEXT
        )
        # 지역 단일 과부하 경고는 별도 수급 이벤트가 아니므로 제외.
        if "Post Contingency Local Load Relief Warning" in msg_type and not any(x.lower() in body.lower() for x in ("eea", "doe 202(c)", "backup generation")):
            material = False
        if not material:
            continue
        items[msg_id] = {
            "id": msg_id,
            "type": msg_type,
            "priority": priority,
            "start": start,
            "regions": regions,
            "body": body,
            "url": f"https://emergencyprocedures.pjm.com/ep/pages/viewposting.jsf?id={msg_id}",
        }
    return items

def parse_doe_pjm_orders():
    raw = get(DOE_ORDERS_URL)
    text = BeautifulSoup(raw, "html.parser").get_text(" ", strip=True)
    # 'Order No. 202-26-45 ... PJM'처럼 나타나는 번호를 수집하되 주변 문맥에 PJM이 있는 경우만 유지한다.
    items = {}
    for m in re.finditer(r"(?:DOE\s+)?Order\s+No\.\s*(202-\d{2}-\d+[A-Z]?)", text, re.I):
        order_no = m.group(1).upper()
        lo = max(0, m.start() - 500)
        hi = min(len(text), m.end() + 900)
        context = clean(text[lo:hi])
        if "PJM" not in context and "PJM Interconnection" not in context:
            continue
        date_m = re.search(r"On\s+([A-Z][a-z]+\s+\d{1,2},\s+2026)", context)
        date = date_m.group(1) if date_m else ""
        items[order_no] = {
            "order_no": order_no,
            "date": date,
            "context": context,
            "url": DOE_ORDERS_URL,
        }
    return items

def send_telegram(text: str):
    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    if not token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN 또는 TELEGRAM_CHAT_ID가 없습니다.")
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": text[:4096],
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
        },
        timeout=TIMEOUT,
    )
    r.raise_for_status()

def kst_now():
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")

def build_pjm_message(item):
    body = item["body"]
    low = body.lower()
    title = "PJM 전력망 비상운영 단계 변화"
    bullets = []

    if "202-26-45" in low or "backup generation" in low or "back-up generators" in low:
        title = "PJM, DOE 202(c) 긴급명령에 따른 백업발전·지정자원 운용 상태 변화"
        if "has identified a reliability need" in low:
            bullets.append("달라진 점: PJM이 DOE Order 202-26-45의 지정자원을 9월 17일 운영일에 사용할 신뢰도 필요가 있다고 공식 판단")
        if "has not identified a reliability need" in low:
            bullets.append("달라진 점: PJM이 9월 18일 운영일에는 지정자원 추가 사용 필요가 없다고 판단")
        if "not expected to continue past 9/17" in low:
            bullets.append("현재 판정: 비상계통 상황이 9월 17일 이후 지속될 것으로 예상하지 않는다고 공지")
        if "does not constitute a directive to operate at maximum output" in low:
            bullets.append("주의: 명령은 지정설비의 최대출력·전부하 운전을 자동 지시하는 것이 아니라 PJM 급전지시에 따라 필요한 만큼 운전하는 구조")
    elif "eea2" in low or "nerc level eea2" in low:
        title = "PJM, EEA2·긴급 수요감축 발령"
        bullets.append("달라진 점: 단순 사전경보를 넘어 일부 지역에서 NERC EEA2와 Emergency Load Management Reduction Action이 발령")
    elif "eea1" in low or "nerc eea 1" in low:
        title = "PJM, EEA1·최대발전 비상경보 발령"
        bullets.append("달라진 점: PJM 전역의 운영예비력 압박에 대응해 최대발전·부하관리 경보 단계로 상승")
    elif "load mgmt reduction action" in low:
        title = "PJM, 수요반응 긴급감축 실행"
        bullets.append("달라진 점: Capacity Performance 수요반응 자원이 실제 감축 실행 단계로 진입")

    if item.get("regions"):
        bullets.append(f"적용 지역: {item['regions']}")
    if item.get("start"):
        bullets.append(f"발효 시각: {item['start']} EPT")
    bullets.append("다음 확인: EEA 단계 상·하향, 수요반응 해제, 백업발전 실제 동원 MW·MWh, 지정자원 목록·운전지시, 명령 연장·종료")

    url = html.escape(item["url"], quote=True)
    return (
        "🚨 <b>미국 전력망 비상운영·PJM 중요 변화</b>\n\n"
        f"<b>{html.escape(title)}</b>\n"
        f"출처: <a href=\"{url}\">PJM Emergency Procedures</a>\n"
        f"공식 게시 시각: <b>{html.escape(item.get('start') or '')} EPT</b>\n"
        f"확인시각: {kst_now()}\n\n"
        + "\n".join(f"• {html.escape(x)}" for x in bullets[:6])
        + f"\n\n<a href=\"{url}\"><b>원문</b></a>"
    )

def build_doe_order_message(item):
    order_no = item["order_no"]
    url = html.escape(item["url"], quote=True)
    return (
        "🚨 <b>미국 전력망 비상운영·PJM 중요 변화</b>\n\n"
        f"<b>DOE, PJM에 202(c) 긴급명령 {html.escape(order_no)} 발령</b>\n"
        f"출처: <a href=\"{url}\">미 에너지부 202(c) 공식 명령 목록</a>\n"
        + (f"공식 발령일: <b>{html.escape(item.get('date',''))}</b>\n" if item.get("date") else "")
        + f"확인시각: {kst_now()}\n\n"
        "• 단계: 연방전력법 202(c) 긴급명령\n"
        "• 의미: PJM이 지정 발전자원과 대형부하의 비상 백업발전을 신뢰도 유지에 필요한 경우 동원할 수 있는 권한 확보\n"
        "• 구분: 수요반응 호출과 별개의 공급측 비상수단이며, 일반 고객 순환정전이나 상시 시장참여 허용을 뜻하지 않음\n"
        "• 다음 확인: PJM 지정자원 사용 필요 판정, 실제 동원 MW·MWh, EEA 단계, 명령 연장·종료\n\n"
        f"<a href=\"{url}\"><b>원문</b></a>"
    )

def bootstrap_message():
    doe_url = html.escape("https://www.energy.gov/articles/energy-secretary-secures-mid-atlantic-grid-due-anticipated-stressed-system-conditions", quote=True)
    pjm_url = html.escape("https://emergencyprocedures.pjm.com/ep/pages/viewposting.jsf?id=105531", quote=True)
    return (
        "🚨 <b>미국 전력망 비상운영·PJM 중요 변화</b>\n\n"
        "<b>DOE, PJM에 Order 202-26-45 발령…백업발전·지정자원 동원 권한</b>\n"
        f"출처: <a href=\"{doe_url}\">미 에너지부 공식 발표</a> · <a href=\"{pjm_url}\">PJM 공식 운영공지</a>\n"
        "PJM 신청일: <b>2026-09-16</b>\n"
        "DOE 발령일: <b>2026-09-17</b>\n"
        f"확인시각: {kst_now()}\n\n"
        "• 원인: 고온 전망과 계획 송전정비가 겹치며 중부 대서양 전력망 공급 신뢰도 위험이 커져 PJM 신청에 따라 발령\n"
        "• 권한: 지정 발전자원 급전과, EEA3 선언 직전 또는 EEA3 상황에서 대형부하의 비상 백업발전 운전 지시 가능\n"
        "• 실제 운영: PJM은 9월 17일 지정자원 사용 필요를 공식 확인했고, 같은 날 일부 지역에 EEA2·긴급 수요감축도 발령\n"
        "• 최신 상태: PJM은 9월 18일 운영일에는 지정자원 추가 사용 필요가 없고 비상상황이 9월 17일 이후 지속될 것으로 예상하지 않는다고 공지\n"
        "• 물량 주의: DOE의 미국 전역 미활용 백업발전 35GW 이상 추산은 PJM 확보물량·실제 가동량이 아님. 이번 실제 동원 MW·MWh는 아직 별도 확인 필요\n"
        "• 유효기간: <b>2026-09-18 23:59 EPT까지</b> · 다음 확인은 실제 동원량·EEA 해제·명령 연장 여부\n\n"
        f"<a href=\"{doe_url}\"><b>원문</b></a>"
    )

def main():
    old = load_state()
    new = dict(old)
    seen_pjm = set(old.get("pjm_msg_ids", []))
    seen_orders = set(old.get("doe_order_nos", []))

    try:
        pjm = parse_pjm_messages()
    except Exception as e:
        print(f"[PJM WARN] Emergency Procedures 조회 실패: {e}")
        pjm = {}
    try:
        orders = parse_doe_pjm_orders()
    except Exception as e:
        print(f"[DOE WARN] 202(c) 명령 목록 조회 실패: {e}")
        orders = {}

    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()

    if not old.get("initialized"):
        if not token or not chat_id:
            print("[PJM PENDING] Telegram Secret이 없어 기준 알림 보류")
            return
        send_telegram(bootstrap_message())
        print("[PJM BOOTSTRAP SENT] DOE 202-26-45 / PJM backup generation")
        new["initialized"] = True
        new["bootstrap_sent"] = True
        new["pjm_msg_ids"] = sorted(set(pjm.keys()))
        new["doe_order_nos"] = sorted(set(orders.keys()))
        save_state(new)
        return

    alerts = []
    for order_no in sorted(set(orders.keys()) - seen_orders):
        alerts.append(("doe", orders[order_no]))
    for msg_id in sorted(set(pjm.keys()) - seen_pjm, key=lambda x: int(x)):
        alerts.append(("pjm", pjm[msg_id]))

    if alerts and (not token or not chat_id):
        print("[PJM PENDING] 신규 비상운영 변화가 있으나 Telegram Secret 없음")
        return

    sent = 0
    for kind, item in alerts[:8]:
        if kind == "doe":
            send_telegram(build_doe_order_message(item))
            print(f"[PJM SENT] DOE {item['order_no']}")
        else:
            send_telegram(build_pjm_message(item))
            print(f"[PJM SENT] PJM Msg {item['id']} {item['type']}")
        sent += 1

    new["initialized"] = True
    new["bootstrap_sent"] = bool(old.get("bootstrap_sent", True))
    new["pjm_msg_ids"] = sorted(set(seen_pjm) | set(pjm.keys()), key=lambda x: int(x))[-2000:]
    new["doe_order_nos"] = sorted(set(seen_orders) | set(orders.keys()))[-500:]
    save_state(new)
    print(f"[PJM DONE] 신규 알림 {sent}건")

if __name__ == "__main__":
    main()
