#!/usr/bin/env python3
import html
import re

import semiconductor_consumption_watch_v2 as base


def display_title(title, source=""):
    title = base.clean(title)
    source = base.clean(source)
    if source:
        title = re.sub(r"\s[-–—|]\s*" + re.escape(source) + r"\s*$", "", title, flags=re.I)
    return title


def has_all(text, *tokens):
    low = text.lower()
    return all(token.lower() in low for token in tokens)


def number_highlights(text):
    """숫자를 한 줄에 몰아넣지 않고 의미별로 묶어 보여준다."""
    t = base.clean(text)
    lines = []

    if "1.6%" in t and "3.7%" in t:
        lines.append("• <b>소비 격차</b> 2025년 성과급 지급 직후 +1.6% → 2026년 6월 +3.7%")
    elif "3.7%" in t:
        lines.append("• <b>소비 격차</b> 비수혜지역 대비 +3.7%")
    elif re.search(r"최대\s*4\s*%|약\s*4\s*%", t):
        lines.append("• <b>소비 효과</b> 비수혜지역 대비 최대 약 +4%")

    if ("1조1천억원" in t or "1조 1천억원" in t or "1조1,000억원" in t) and ("1조7천억원" in t or "1조 7천억원" in t or "1조7,000억원" in t):
        lines.append("• <b>누적 소비</b> 2026년 6월 약 1조1천억원 → 연말 약 1조7천억원 전망")
    elif "1조7천억원" in t or "1조 7천억원" in t or "1조7,000억원" in t:
        lines.append("• <b>누적 소비</b> 연말 약 1조7천억원 전망")

    if "27%" in t and "21%" in t:
        lines.append("• <b>소비파급률</b> 2025년 27% → 2026년 21%")

    if "16.1%" in t and "39.8%" in t:
        lines.append("• <b>비교 기준</b> 소비쿠폰 소비파급률 추정 16.1~39.8%")

    if "6.8%" in t and "4.6%" in t and "3.6%" in t:
        lines.append("• <b>반도체 노출도</b> 이천 6.8% > 화성 4.6% > 청주 흥덕구 3.6%")

    if ("0.06" in t and "0.09" in t and ("%p" in t or "%포인트" in t)):
        lines.append("• <b>2027 성장률</b> +0.06~0.09%p 제고 가능성")
    elif "0.09%p" in t or "0.09%포인트" in t or "0.09% 포인트" in t:
        lines.append("• <b>성장률</b> 최대 +0.09%p 제고 가능성")

    if "105.6%" in t and "77.2%" in t:
        lines.append("• <b>Tesla 인도량</b> 반도체 수혜지역 +105.6% vs 전국 +77.2%")
    elif "105.6%" in t and ("테슬라" in t.lower() or "tesla" in t.lower()):
        lines.append("• <b>Tesla 인도량</b> 반도체 수혜지역 +105.6%")

    # 위 특화 숫자가 없을 때만 일반 숫자 요약을 사용한다.
    if not lines:
        nums = re.findall(r"[+-]?\d+(?:\.\d+)?\s*(?:%p|%|배|조원|억원|대)", t)
        nums = list(dict.fromkeys(base.clean(x) for x in nums))[:6]
        if nums:
            lines.append("• <b>주요 수치</b> " + " · ".join(html.escape(x) for x in nums))

    return lines[:7]


def paragraph_label(text):
    low = base.clean(text).lower()
    if "테슬라" in low or "tesla" in low or "인도량" in low or "신규등록" in low:
        return "자동차·Tesla"
    if "주택" in low or "부동산" in low or "아파트" in low:
        return "지역 차이·자산흡수"
    if "소비파급률" in low or ("27%" in low and "21%" in low):
        return "소비파급률"
    if "gdp" in low or "성장률" in low:
        return "성장률 효과"
    if "누적" in low or "1조" in low:
        return "소비 규모"
    if "성과급" in low:
        return "성과급→소비"
    if "고가" in low or "백화점" in low or "재량" in low:
        return "소비 품목"
    return "핵심 내용"


def readable_body(sentences):
    out = []
    used_labels = {}
    for raw in sentences[:3]:
        text = base.clean(base.to_korean(raw))
        if not text:
            continue
        label = paragraph_label(text)
        used_labels[label] = used_labels.get(label, 0) + 1
        suffix = f" {used_labels[label]}" if used_labels[label] > 1 else ""
        out.append(f"• <b>{html.escape(label + suffix)}</b> {html.escape(text[:700])}")
    return out


def next_check_lines(text):
    low = base.clean(text).lower()
    lines = []
    if any(x in low for x in ("고가", "백화점", "테슬라", "tesla", "자동차", "주택", "부동산")):
        lines.append("• 고가 소비·주택 쏠림이 줄고 생활·서비스 소비로 확산되는지")
    if "성과급" in low:
        lines.append("• 다음 성과급 지급 때 소비파급률이 21%에서 다시 높아지는지")
    if "성장률" in low or "gdp" in low or "0.09" in low:
        lines.append("• 2027년 성장률 +0.06~0.09%p 전망이 실제 성과급·고용 증가로 확인되는지")
    if "테슬라" in low or "tesla" in low:
        lines.append("• Tesla 증가가 전국 평균을 계속 웃도는지, 국산 소비로도 확산되는지")
    if not lines:
        lines.append("• 고가 소비 편중 → 생활·서비스 소비 확산 여부")
    return lines[:3]


def build_alert(item, url, sentences):
    title = base.to_korean(display_title(item.get("title", ""), item.get("source", "")))
    body = [base.to_korean(s) for s in sentences[:3]]
    joined = " ".join([title] + body)

    highlights = number_highlights(joined)
    detail_lines = readable_body(body)
    checks = next_check_lines(joined)

    pub = item.get("pub_kst", "")
    try:
        pub = base.dt.datetime.fromisoformat(pub).astimezone(base.KST).strftime("%Y-%m-%d %H:%M KST")
    except Exception:
        pass

    parts = [
        "📊 <b>[반도체 수혜지역 소비 웹감시]</b>",
        f"<b>{html.escape(title)}</b>",
        "",
        "<b>한눈에 보기</b>",
        *highlights,
        "",
        "<b>핵심 내용</b>",
        *detail_lines,
        "",
        "<b>다음 확인</b>",
        *checks,
        "",
        f"• <b>분류</b> {html.escape(base.classify(joined))}",
        f"• <b>시각</b> {html.escape(pub)}",
        f"<a href=\"{html.escape(url, quote=True)}\">원문</a>",
    ]
    return "\n".join(parts)


if __name__ == "__main__":
    base.build_alert = build_alert
    base.main()
