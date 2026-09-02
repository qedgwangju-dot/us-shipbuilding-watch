#!/usr/bin/env python3
import html
import re

import semiconductor_consumption_watch_v2 as base
import semiconductor_consumption_watch_v4 as v4

LOCATIONS = ("동탄", "수원", "용인", "성남", "이천", "화성", "청주", "흥덕구", "영통", "기흥")


def _add(lines, text):
    if text and text not in lines:
        lines.append(text)


def _location(text):
    for loc in LOCATIONS:
        if loc in text:
            return loc
    return "주택"


def _ratio(new, old):
    try:
        new_i, old_i = int(new.replace(",", "")), int(old.replace(",", ""))
        if old_i <= 0:
            return ""
        multiple = new_i / old_i
        yoy = (multiple - 1) * 100
        return f"전년 대비 +{yoy:.1f}% · {multiple:.2f}배"
    except Exception:
        return ""


def semantic_highlights(text):
    """숫자를 반드시 뜻과 기준기간에 연결한다. 뜻을 못 붙인 숫자는 나열하지 않는다."""
    t = base.clean(text)
    lines = []

    # 한국은행 핵심 지표: v4의 의미가 붙은 규칙만 사용하고 '주요 수치' fallback은 버린다.
    for line in v4.number_highlights(t):
        if "<b>주요 수치</b>" not in line:
            _add(lines, line)

    # 성과급 규모
    m = re.search(r"기본급(?:의)?\s*([\d,]+(?:\.\d+)?)\s*%", t)
    if m and "성과급" in t:
        pct = float(m.group(1).replace(",", ""))
        _add(lines, f"• <b>성과급 규모</b> 기본급의 {pct:,.0f}% — 기본급 대비 {pct/100:.2f}배")

    # '지난 2월 ... 45건 ... 전년 동월(12건)' 유형
    m = re.search(r"(?:지난\s*)?2월[^.]{0,220}?([\d,]+)건[^.]{0,180}?전년\s*동월\s*\(([\d,]+)건\)", t)
    if m:
        new, old = m.group(1), m.group(2)
        loc = _location(m.group(0))
        _add(lines, f"• <b>{loc} 매입·2월</b> {old}건 → {new}건, {_ratio(new, old)}")

    # '2~7월 ... 158건 ... 전년 동기(69건)' 유형
    m = re.search(r"2\s*[~～\-]\s*7월[^.]{0,220}?([\d,]+)건[^.]{0,180}?전년\s*동기\s*\(([\d,]+)건\)", t)
    if m:
        new, old = m.group(1), m.group(2)
        loc = _location(m.group(0))
        _add(lines, f"• <b>{loc} 매입·2~7월</b> {old}건 → {new}건, {_ratio(new, old)}")

    # '76건으로 전년 동기(40건)보다 90% 증가' 유형
    for m in re.finditer(r"([가-힣A-Za-z]+)[^.]{0,90}?([\d,]+)건으로[^.]{0,100}?전년\s*동기\s*\(([\d,]+)건\)[^.]{0,80}?([\d.]+)%\s*증가", t):
        loc, new, old, pct = m.group(1), m.group(2), m.group(3), m.group(4)
        loc = next((x for x in LOCATIONS if x in m.group(0)), loc)
        _add(lines, f"• <b>{loc} 매입</b> {old}건 → {new}건, 전년 동기 대비 +{pct}%")

    # '95건에서 121건으로 27.3% 늘었다' 유형
    for m in re.finditer(r"([가-힣A-Za-z]+)[^.]{0,80}?([\d,]+)건에서\s*([\d,]+)건으로[^.]{0,60}?([\d.]+)%\s*(?:늘|증가)", t):
        loc, old, new, pct = m.group(1), m.group(2), m.group(3), m.group(4)
        loc = next((x for x in LOCATIONS if x in m.group(0)), loc)
        _add(lines, f"• <b>{loc} 매입</b> {old}건 → {new}건, 전년 동기 대비 +{pct}%")

    # 숫자의 의미를 확실히 붙일 수 없는 경우 숫자만 따로 보여주지 않는다.
    return lines[:8]


def build_alert(item, url, sentences):
    title = base.to_korean(v4.display_title(item.get("title", ""), item.get("source", "")))
    body = [base.to_korean(s) for s in sentences[:3]]
    joined = " ".join([title] + body)

    highlights = semantic_highlights(joined)
    detail_lines = v4.readable_body(body)
    checks = v4.next_check_lines(joined)

    pub = item.get("pub_kst", "")
    try:
        pub = base.dt.datetime.fromisoformat(pub).astimezone(base.KST).strftime("%Y-%m-%d %H:%M KST")
    except Exception:
        pass

    parts = [
        "📊 <b>[반도체 수혜지역 소비 웹감시]</b>",
        f"<b>{html.escape(title)}</b>",
    ]
    if highlights:
        parts.extend(["", "<b>한눈에 보기</b>", *highlights])

    parts.extend([
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
    ])
    return "\n".join(parts)


if __name__ == "__main__":
    base.build_alert = build_alert
    base.main()
