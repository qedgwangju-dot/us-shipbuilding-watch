import html
import re

import grid_monitor_v2 as base

# 2026-08-31 Federal Register 공식 게재본까지 포함해 기준선을 강화한다.
FEDERAL_REGISTER_SOURCE = {
    "name": "미 연방관보 대통령 문서",
    "url": "https://www.federalregister.gov/documents/search?conditions%5Bterm%5D=bulk-power+system",
}
if not any(x.get("name") == FEDERAL_REGISTER_SOURCE["name"] for x in base.SOURCES):
    base.SOURCES.append(FEDERAL_REGISTER_SOURCE)

for term in ["executive order 14421", "eo 14421", "2026-17843", "grandfather", "existing contract"]:
    if term not in base.POLICY_TERMS:
        base.POLICY_TERMS.append(term)

_original_relevant = base.relevant


def relevant(title: str, url: str) -> bool:
    hay = f"{title} {url}".lower()
    if any(x in hay for x in ["executive order 14421", "2026-17843", "secure the united states bulk-power system"]):
        return True
    return _original_relevant(title, url)


def is_bulk_power_eo(text: str) -> bool:
    low = (text or "").lower()
    return (
        ("bulk-power system" in low or "bulk power system" in low)
        and "covered foreign entity" in low
        and ("120 days" in low or "pre-qualified" in low or "prequalified" in low)
    )


def title_korean(item, blocks):
    full = " ".join(blocks)
    if is_bulk_power_eo(full):
        return "연방관보 공식 EO 14421, 미 전력망 외산 장비 규제·미국 현지생산 프리미엄 강화"
    return base.title_korean(item, blocks)


def bulk_power_bullets():
    return [
        "번호 정정: 8월 31일 Federal Register·GovInfo 공식 게재본은 Executive Order 14421. 백악관 페이지는 동일 문서를 아직 EO 14420으로 표시해 불일치가 있어 알림은 연방관보 공식 번호 14421을 기준으로 함",
        "정확한 규제: 모든 외국산 장비 전면 금지가 아니라 Covered Foreign Entity가 관여하고 DOE가 사보타주·불법 원격접근·악의적 원격조작·공급중단 등 과도하거나 수용 불가능한 위험을 판정한 외국산 BPS 장비·핵심부품·소프트웨어·서비스의 신규 거래를 제한",
        "적용 범위: 69kV 이상 송전망을 포함한 Bulk-Power System이 대상이고 지역 배전은 제외. 변압기·고압차단기·보호계전·계통연계 인버터·BESS·중요 인프라용 UPS·RTU/PLC/IED·DCS/SIS와 소프트웨어·펌웨어·원격접속·수명주기 유지보수까지 심사",
        "기존 장비·계약: 8월 26일 이전 설치 외산 장비도 식별·격리·감시·보안강화·연결해제·교체·철거 가능. 기존 계약·라이선스가 있어도 8월 26일 이후 시작된 해당 거래는 자동 면책되지 않으며, 사전적격 공급업체로 지정돼도 DOE의 재규제 권한은 유지",
        "투자 관점·일정: 미국에서 제조·생산·조립한 제품은 foreign-produced 정의에서 빠질 수 있지만 연방조달 미국산 우대는 별도 기준. 현행 일반 Buy American은 2024~2028년 국내부품 원가 65% 초과, 2029년부터 75%. DOE 시행규칙은 2026년 12월 24일 전후, FAR 권고안은 2027년 2월 22일 전후이며 이후 FAR Council은 권고 접수 후 90일 이내 개정안 공고 여부를 검토. 효성중공업 Memphis·HD현대일렉트릭 Alabama의 초고압 현지생산 직접성이 가장 높고 LS ELECTRIC은 지역 배전 제품과 BPS 직접 수혜를 구분",
    ]


def structured_bullets(blocks):
    full = " ".join(blocks)
    if is_bulk_power_eo(full):
        return bulk_power_bullets()

    bullets = base.structured_bullets(blocks)
    fixed = []
    for bullet in bullets:
        bullet = bullet.replace("EO 14420 전용 에너지 인프라 FAR", "EO 14421 후속 에너지 인프라 FAR")
        bullet = bullet.replace("EO 14420", "EO 14421")
        fixed.append(bullet)
    return fixed


def build_message(item):
    blocks = base.clean_blocks(item["url"])
    title = title_korean(item, blocks)
    bullets = structured_bullets(blocks)
    if not bullets:
        bullets = base.fallback_bullets(blocks, 3)
    if not bullets:
        bullets = ["미국 전력망·전력기기 조달·보안 정책의 새 공식자료가 확인되었습니다."]

    safe_url = html.escape(item["url"], quote=True)
    safe_source = html.escape(item["source"])
    bullet_text = "\n".join(f"• {html.escape(x)}" for x in bullets[:5])

    # 실제 텔레그램에 표시되는 제목·bullet에 달러 금액이 있을 때만 환율 기준을 붙인다.
    displayed = " ".join([title] + bullets[:5])
    fx = base.get_fx()
    fx_line = ""
    if fx.get("USD") and (re.search(r"\$\s*\d", displayed) or re.search(r"\d[\d,.]*(?:조|억|만)?\s*달러", displayed)):
        fx_line = f"\n\n환산 기준: 1달러={fx['USD']:,.0f}원 · ECB {fx['date']}"

    return (
        "🚨 <b>미국 전력망·전력기기 정책 중요 변화</b>\n\n"
        f"<b>{html.escape(title)}</b>\n"
        f"출처: <a href=\"{safe_url}\">{safe_source}</a>\n\n"
        f"{bullet_text}"
        f"{html.escape(fx_line)}\n\n"
        f"<a href=\"{safe_url}\"><b>원문</b></a>"
    )


base.relevant = relevant
base.is_bulk_power_eo = is_bulk_power_eo
base.title_korean = title_korean
base.structured_bullets = structured_bullets
base.build_message = build_message


if __name__ == "__main__":
    base.main()
