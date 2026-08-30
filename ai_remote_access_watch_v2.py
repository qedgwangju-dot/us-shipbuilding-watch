import html

import ai_remote_access_watch as base


def _source_label(source: str) -> str:
    source = (source or "").strip()
    if " · " in source:
        tail = source.split(" · ")[-1].strip()
        if tail:
            return tail
    return source or "공식자료"


def _countries(text: str):
    return base.countries_in(text)


def build_message(item):
    article_text = base.extract_article_text(item)
    raw = f"{item.get('title', '')} {item.get('summary', '')} {article_text}"
    low = raw.lower()
    title = base.korean_title(item.get("title", ""))
    bullets = []

    # The Information 2026-08-28 보도는 현재 기준선의 핵심 이슈이므로
    # 기계번역 대신 검증된 투자 요약을 사용한다.
    if "trump administration working on ai rule to curb china" in low or (
        "remote access" in low and "thailand" in low and "singapore" in low and "ai diffusion" in low
    ):
        title = "미국, 중국의 해외 데이터센터 원격 GPU 접근 차단 규칙 검토"
        bullets = [
            "단계: BIS 내부 검토·보도 단계 — 공식 초안·최종규칙은 아직 미공개, 이르면 9월 업계 의견수렴 가능",
            "핵심 변화: 중국 직접수출뿐 아니라 태국·싱가포르 등 제3국 데이터센터의 실제 원격사용자까지 통제축을 넓히는 방안 검토",
            "적용 방식: 원격접근 자체를 바로 금지하기보다 제3국 GPU 수출에 라이선스를 걸고 중국 고객 원격접근 차단을 조건화하는 방식이 유력",
            "KYC는 신규가 아님: 중국향 첨단칩 제도에도 고객·원격사용자 확인이 이미 존재하며, 새 쟁점은 이를 제3국 데이터센터로 확대할지 여부",
            "투자 관점: NVIDIA·AMD·HBM의 실제 악재는 GPU 주문 취소일 때. 다른 CSP·지역으로 재배치되면 SK하이닉스·삼성전자 HBM 총수요 영향은 제한적",
            "다음 확인: BIS 초안, 대상국·ECCN·성능기준, KYC 범위, 라이선스 조건, Remote Access Security Act 진행, 실제 GPU 주문 취소 여부",
        ]
    elif "remote access security act" in low or "remote access of items" in low:
        bullets = [
            "단계: 미국 의회 공식 입법 변화",
            "핵심 변화: 인터넷·클라우드를 통한 외국인의 통제 품목 원격접근을 수출통제 대상으로 명시하는 법적 권한 강화",
            "현재 기준선: H.R.2683은 2026년 1월 하원 369대22 통과 후 상원 Banking Committee에 회부된 상태",
            "투자 관점: 법 통과 시 BIS가 제3국 GPU 수출을 우회 규제하는 것보다 중국 기업의 원격접근 자체를 직접 통제할 기반이 강해짐",
            "다음 확인: 상원 위원회 심사·수정안·NDAA 포함 여부·본회의 표결",
        ]
    elif "may 31, 2026" in low or "country group d:5" in low or "ultimate parent" in low:
        bullets = [
            "단계: BIS 공식 집행지침",
            "달라진 점: 중국·마카오 등 D:5 국가에 본사 또는 최종모회사를 둔 기업은 제3국 자회사라도 첨단컴퓨팅 품목 수령에 라이선스가 필요함을 재확인",
            "중요: 이 요건은 2023년부터 존재하던 규정으로, 2026년에 새로 만든 규제가 아니라 기존 규정의 집행 재확인",
            "투자 관점: 해외 중국계 데이터센터·자회사의 GPU 조달 경로는 좁아지지만, 기존 정상 데이터센터 장비의 사용·보관·정비를 즉시 중단시키는 조치는 아님",
            "다음 확인: 라이선스 승인·거절, 제3국 데이터센터 고객구조, 실제 NVIDIA·AMD 주문 변화",
        ]
    elif "ai action plan implementation" in low or "secure export of advanced ai chips" in low or "ai diffusion" in low:
        bullets = [
            "단계: BIS 공식 규제계획 변화",
            "달라진 점: 바이든 AI Diffusion Rule을 공식 폐기하고 더 좁은 형태의 첨단 AI 칩 안전수출 규칙으로 재설계하는 방향",
            "일정: 기존 규제계획의 2026년 7월 최종조치 목표는 이미 지연된 상태이므로 새 공식 일정이 핵심",
            "투자 관점: 국가별 광범위 쿼터보다 중국 우회접근·최종사용자 통제가 중심이면 NVIDIA 총출하 영향은 실제 주문 취소 규모에 좌우",
            "다음 확인: Federal Register, 대상국, ECCN·성능기준, 데이터센터 KYC·원격접근 조건",
        ]
    else:
        if item.get("stage") == "보도":
            bullets.append("단계: 정책 검토·보도 단계 — BIS 공식자료 확인 전에는 확정 규제로 보지 않음")
        else:
            bullets.append("단계: 미국 정부·연방관보·의회 공식자료 변화")

        if "remote access" in low:
            bullets.append("달라진 점: 중국 기업의 제3국 데이터센터·클라우드 원격 GPU 접근 통제 범위 또는 집행방식에 새 변화")
        elif "license" in low or "licensing" in low:
            bullets.append("달라진 점: 첨단 AI 칩의 국가·최종사용자별 라이선스 조건에 새 변화")
        else:
            bullets.append("새 변화: 첨단 AI 칩 해외수출·중국 우회사용 통제 관련 새 자료 확인")

        found = _countries(raw)
        if found:
            bullets.append(f"대상 지역: {', '.join(found[:5])} — 실제 규제 대상국인지 사례 언급인지 구분 필요")
        elif any(x in low for x in ["know your customer", "kyc", "remote end user", "end user"]):
            bullets.append("KYC·최종사용자: 고객 신원·최종모회사·원격사용자 확인 범위가 핵심")

        bullets.append("투자 관점: NVIDIA·AMD의 실제 악재는 GPU 주문 취소일 때이며, 다른 고객·지역으로 재배치되면 HBM 총수요 영향은 제한적")
        bullets.append("다음 확인: BIS 공식 초안·Federal Register, 대상 국가·칩 기준, 라이선스·KYC 조건, 실제 GPU 주문 변화")

    safe_url = html.escape(item["url"], quote=True)
    safe_source = html.escape(_source_label(item.get("source", "")))
    bullet_text = "\n".join(f"• {html.escape(x)}" for x in bullets[:6])
    return (
        "🚨 <b>미국 AI 칩·원격접근 규제 중요 변화</b>\n\n"
        f"<b>{html.escape(title)}</b>\n"
        f"출처: <a href=\"{safe_url}\">{safe_source}</a>\n\n"
        f"{bullet_text}\n\n"
        f"<a href=\"{safe_url}\"><b>원문</b></a>"
    )


base.build_message = build_message


if __name__ == "__main__":
    base.main()
