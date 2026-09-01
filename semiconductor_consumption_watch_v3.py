#!/usr/bin/env python3
import html
import re

import semiconductor_consumption_watch_v2 as base


def display_title(title, source=""):
    """표시용 제목에서는 Google News가 붙인 '- 매체명' 꼬리표만 제거한다."""
    title = base.clean(title)
    source = base.clean(source)
    if source:
        title = re.sub(r"\s[-–—|]\s*" + re.escape(source) + r"\s*$", "", title, flags=re.I)
    return title


def build_alert(item, url, sentences):
    title = base.to_korean(display_title(item.get("title", ""), item.get("source", "")))
    body = [base.to_korean(s) for s in sentences[:3]]
    joined = " ".join([title] + body)

    nums = re.findall(r"[+-]?\d+(?:\.\d+)?\s*(?:%p|%|배|조원|억원|대)", joined)
    num_line = " · ".join(dict.fromkeys(nums)) if nums else "핵심 수치 추가 확인 필요"
    bullets = "\n".join("• " + html.escape(base.clean(s)[:360]) for s in body)

    pub = item.get("pub_kst", "")
    try:
        pub = base.dt.datetime.fromisoformat(pub).astimezone(base.KST).strftime("%Y-%m-%d %H:%M KST")
    except Exception:
        pass

    # 비클릭 매체명·출처 표시는 제거하고 실제 클릭 가능한 원문 링크만 남긴다.
    return (
        "📊 <b>[반도체 수혜지역 소비 웹감시]</b>\n"
        f"<b>{html.escape(title)}</b>\n\n"
        f"• 시각: {html.escape(pub)}\n"
        f"• 분류: {html.escape(base.classify(joined))}\n"
        f"• 핵심 수치: {html.escape(num_line)}\n\n"
        f"{bullets}\n\n"
        "• 판정 기준: 고가 소비 편중 → 생활·서비스 소비 확산 여부\n"
        f"<a href=\"{html.escape(url, quote=True)}\">원문</a>"
    )


if __name__ == "__main__":
    base.build_alert = build_alert
    base.main()
