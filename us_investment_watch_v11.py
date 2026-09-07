#!/usr/bin/env python3
from __future__ import annotations

import re

import us_investment_watch_v9 as v9
import us_investment_watch_v10 as v10

# v11: Telegram 사용자 표시 문구는 한국어를 우선한다.
# 기사 원문 검색용 키워드/내부 식별자는 원문을 유지하되, 송출되는 설명어·공정명·계약명·단위는 한국어로 변환한다.
# AP1000/APR1400 같은 모델명과 법규 번호는 식별을 위해 그대로 둔다.

_ORIGINAL_FACT_VALUE_TEXT = v9.fact_value_text
_ORIGINAL_BUILD_ALERT = v9.build_alert

TEXT_REPLACEMENTS = [
    ("Alaska LNG", "알래스카 액화천연가스"),
    ("Alaska", "알래스카"),
    ("LNG", "액화천연가스"),
    ("I-SPV", "개별 투자 특수목적법인"),
    ("SPV", "특수목적법인"),
    ("PPA", "장기 전력판매계약"),
    ("EPC", "설계·조달·시공"),
    ("FID", "최종투자결정"),
    ("ERCOT", "텍사스 전력망"),
    ("Lewis Energy Group", "루이스 에너지 그룹"),
    ("POSCO International", "포스코인터내셔널"),
    ("POSCO", "포스코"),
    ("AI", "인공지능"),
    ("MOU", "업무협약"),
    ("risk pooling", "위험 통합"),
    ("Risk Pooling", "위험 통합"),
]


def _koreanize_text(text: str) -> str:
    out = text
    for src, dst in TEXT_REPLACEMENTS:
        out = out.replace(src, dst)
    return out


def koreanize_html_text(html_text: str) -> str:
    """HTML 태그/링크 URL은 건드리지 않고 사용자에게 보이는 텍스트만 한국어화."""
    parts = re.split(r'(<[^>]+>)', html_text)
    for i, part in enumerate(parts):
        if part.startswith("<") and part.endswith(">"):
            continue
        parts[i] = _koreanize_text(part)
    return "".join(parts)


def fact_value_text_ko(key: str, value, fx: float) -> str:
    # 물리 단위도 사용자 표시에서는 한국어로 쓴다.
    if key.endswith("_gw"):
        if isinstance(value, (int, float)):
            return f"{float(value):g}기가와트"
        if isinstance(value, list):
            return " / ".join(
                f"{float(v):g}기가와트" if isinstance(v, (int, float)) else _koreanize_text(str(v))
                for v in value
            )
    if key.endswith("_mtpa"):
        if isinstance(value, (int, float)):
            # 1 MTPA = 연간 100만톤
            million_tons = float(value)
            man_tons = million_tons * 100
            if abs(man_tons - round(man_tons)) < 1e-9:
                return f"연간 {int(round(man_tons)):,}만톤"
            return f"연간 {man_tons:,.1f}만톤"
        if isinstance(value, list):
            rendered = []
            for v in value:
                if isinstance(v, (int, float)):
                    man_tons = float(v) * 100
                    rendered.append(f"연간 {man_tons:,.0f}만톤")
                else:
                    rendered.append(_koreanize_text(str(v)))
            return " / ".join(rendered)
    return _koreanize_text(_ORIGINAL_FACT_VALUE_TEXT(key, value, fx))


def build_alert_ko(now, changes, fx: float, fx_source: str) -> str:
    # 기존 숫자·조건·확정도 로직은 그대로 유지하고 최종 사용자 표시만 한국어화한다.
    return koreanize_html_text(_ORIGINAL_BUILD_ALERT(now, changes, fx, fx_source))


# v10 -> v9.main() 경로에서 이 두 함수를 사용하도록 교체.
v9.fact_value_text = fact_value_text_ko
v9.build_alert = build_alert_ko


def main() -> int:
    return v10.main()


if __name__ == "__main__":
    raise SystemExit(main())
