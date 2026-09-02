#!/usr/bin/env python3
import html
import re

import semiconductor_consumption_watch_v2 as base
import semiconductor_consumption_watch_v4 as v4


def number_highlights(text):
    """숫자만 나열하지 않고 문맥상 의미를 붙여서 보여준다."""
    t = base.clean(text)
    lines = []

    # 기존 한국은행 핵심 지표 규칙 유지
    lines.extend(v4.number_highlights(t))

    # v4가 일반 '주요 수치' 한 줄만 만든 경우 제거하고 더 구체적인 규칙으로 대체한다.
    if len(lines) == 1 and "<b>주요 수치</b>" in lines[0]:
        lines = []

    # SK하이닉스 성과급·주택 매입 사례
    if "2964%" in t and "성과급" in t:
        lines.append("• <b>성과급</b> 기본급의 2,964% — 역대 최대 수준")

    if "45건" in t and "12건" in t and ("동탄" in t or "부동산" in t or "아파트" in t):
        lines.append("• <b>동탄 매입·2월</b> 12건 → 45건, 전년 동월 대비 약 3.8배")
    elif "약 4배" in t and ("동탄" in t or "부동산" in t or "아파트" in t):
        lines.append("• <b>주택 매입</b> 전년 동월 대비 약 4배")

    if "158건" in t and "69건" in t and ("동탄" in t or "부동산" in t or "아파트" in t):
        lines.append("• <b>동탄 매입·2~7월</b> 69건 → 158건, 전년 동기 대비 약 2.3배")
    elif "약 2배" in t and ("동탄" in t or "부동산" in t or "아파트" in t):
        lines.append("• <b>주택 매입 누적</b> 전년 동기 대비 약 2배")

    # 일반 증감 문맥도 가능한 경우 의미를 붙인다.
    if not lines:
        pairs = [
            (r"(\d[\d,]*)건[^\n]{0,100}?(?:전년|작년)[^\n]{0,100}?(\d[\d,]*)건", "건수 변화"),
            (r"(\d+(?:\.\d+)?)%[^\n]{0,100}?(\d+(?:\.\d+)?)%", "비율 변화"),
        ]
        for pattern, label in pairs:
            m = re.search(pattern, t)
            if m:
                lines.append(f"• <b>{label}</b> {html.escape(m.group(2))} → {html.escape(m.group(1))}")
                break

    # 끝까지 의미를 못 붙이면 숫자만 나열하지 않고 본문 확인 필요로 표시한다.
    if not lines:
        lines.append("• <b>핵심 수치</b> 본문 문맥에서 의미 확인 필요")

    # 중복 제거
    out = []
    for line in lines:
        if line not in out:
            out.append(line)
    return out[:7]


if __name__ == "__main__":
    v4.number_highlights = number_highlights
    base.build_alert = v4.build_alert
    base.main()
