import re

import quantum_government_investment_watch as base
import quantum_government_investment_watch_v2 as v2
import quantum_government_investment_watch_v6 as v6


# 번역기가 "$675M"을 "$ 6억 7,500만"처럼 '통화기호 + 한국식 단위'로 바꾸는 경우를 처리한다.
HYBRID_SYMBOL_KOREAN_RE = re.compile(
    r"(US\$|JP¥|CN¥|\$|€|£|¥)\s*"
    r"(?:(\d[\d,]*(?:\.\d+)?)\s*조)?\s*"
    r"(?:(\d[\d,]*(?:\.\d+)?)\s*억)?\s*"
    r"(?:(\d[\d,]*(?:\.\d+)?)\s*만)?",
    flags=re.I,
)


def _hybrid_amount(groups):
    jo, eok, man = groups
    if not any((jo, eok, man)):
        return None
    total = 0.0
    if jo:
        total += float(jo.replace(",", "")) * 1_000_000_000_000
    if eok:
        total += float(eok.replace(",", "")) * 100_000_000
    if man:
        total += float(man.replace(",", "")) * 10_000
    return total


def _symbol_code(symbol: str):
    for key, code in v6.SYMBOL_CODES.items():
        if key.lower() == symbol.lower():
            return code
    return None


def _has_krw_immediately_after(text: str, end: int) -> bool:
    tail = text[end:end + 60]
    return bool(re.match(r"\s*\(\s*약\s*[0-9,.]+(?:조[0-9,.]*억|조|억|만)?원\s*\)", tail))


def _convert_hybrid_symbol_money(message: str) -> str:
    used_codes = set()

    def repl(m):
        amount = _hybrid_amount(m.groups()[1:4])
        if amount is None or _has_krw_immediately_after(message, m.end()):
            return m.group(0)
        code = _symbol_code(m.group(1))
        if not code:
            return m.group(0)
        rate, _, _ = v6.rate_to_krw(code)
        used_codes.add(code)
        return f"{m.group(0)}({v6.format_krw(amount * rate)})"

    out = HYBRID_SYMBOL_KOREAN_RE.sub(repl, message)

    if used_codes:
        details = []
        for code in sorted(used_codes):
            rate, date, provider = v6.rate_to_krw(code)
            label = v6.CODE_KOREAN.get(code, code)
            if code == "JPY":
                details.append(f"100{label}={rate * 100:,.2f}원 · {provider} {date}")
            else:
                details.append(f"1{label}={rate:,.2f}원 · {provider} {date}")
        fx_line = "환산 기준: " + " / ".join(details)
        # 기존 환산 기준이 있으면 통화별 중복 줄을 만들지 않고 교체한다.
        out = re.sub(r"\n환산 기준:[^\n]*", "", out)
        marker = "\n\n<a href="
        if marker in out:
            out = out.replace(marker, f"\n{fx_line}\n\n<a href=", 1)
        else:
            out += "\n" + fx_line

    return out


def _all_foreign_money_converted(text: str) -> bool:
    patterns = [v6.KOREAN_MONEY_RE, v6.SYMBOL_MONEY_RE, v6.CODE_MONEY_RE, HYBRID_SYMBOL_KOREAN_RE]
    for pat in patterns:
        for m in pat.finditer(text):
            if pat is HYBRID_SYMBOL_KOREAN_RE and _hybrid_amount(m.groups()[1:4]) is None:
                continue
            if not _has_krw_immediately_after(text, m.end()):
                return False
    return True


# v6가 이미 일반 달러/유로/엔 표기를 처리하므로, v7은 번역 후 혼합 표기를 먼저 보완하고 최종 검증한다.
_ORIGINAL_V6_CONVERTER = v6.convert_foreign_money


def convert_foreign_money_strict(message: str) -> str:
    out = _convert_hybrid_symbol_money(message)
    out = _ORIGINAL_V6_CONVERTER(out)
    if not _all_foreign_money_converted(out):
        raise RuntimeError("외화 금액이 남아 있어 원화 환산 없는 알림을 차단했습니다.")
    return out


# v6의 build_message_with_krw가 참조하는 전역 변환 함수를 교체한다.
v6.convert_foreign_money = convert_foreign_money_strict
base.build_message = v6.build_message_with_krw


# 2026-09-08 공식 최종지원의 단순 묶음 재기사도 차단한다.
# 예: GlobalFoundries 3.75억달러 + 양자 스타트업 3곳 각 1억달러 = 6.75억달러.
_RAW_FILTERED_RSS = base.fetch_news_rss

KNOWN_AGGREGATE_RECAP_MONEY = [
    "$675m", "$675 million", "$ 6억 7,500만", "675 million",
    "$775m", "$775 million", "$ 7억 7,500만", "775 million",
]

ACTUAL_NEW_EVENT_TERMS = [
    "payment received", "payment released", "disbursed", "disbursement",
    "milestone achieved", "milestone completed", "new tranche", "additional tranche",
    "award amended", "award increased", "award reduced", "additional award",
    "government purchase", "purchase order", "procurement award", "supplier selected",
    "commercial order", "customer order", "installed", "deployment completed",
]


def _is_known_sep8_aggregate_recap(item) -> bool:
    text = f"{item.get('title','')} {item.get('summary','')}".lower()
    has_gf = "globalfoundries" in text or "global foundries" in text
    has_quantum_group = "quantum" in text and any(x in text for x in ["startup", "startups", "companies", "firms", "칩 거래", "최종"])
    has_final_chips = "chips" in text and any(x in text for x in ["final", "definitive", "최종"])
    has_known_money = any(x.lower() in text for x in KNOWN_AGGREGATE_RECAP_MONEY)
    has_real_new_event = any(x in text for x in ACTUAL_NEW_EVENT_TERMS)
    return has_gf and has_quantum_group and has_final_chips and has_known_money and not has_real_new_event


def fetch_news_rss_v7(source):
    items = _RAW_FILTERED_RSS(source)
    out = {}
    for url, item in items.items():
        if _is_known_sep8_aggregate_recap(item):
            print(f"[QUANTUM DEDUPE] 2026-09-08 공식 최종지원 묶음 재기사 제외: {item.get('title','')}")
            continue
        out[url] = item
    return out


base.fetch_news_rss = fetch_news_rss_v7


if __name__ == "__main__":
    v2.main()
