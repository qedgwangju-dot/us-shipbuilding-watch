import html
import re
from functools import lru_cache

import requests

import quantum_government_investment_watch as base
import quantum_government_investment_watch_v2 as v2
import quantum_government_investment_watch_v5 as v5


# 외화가 알림에 등장하면 해당 금액 바로 뒤에 원화 환산을 붙인다.
# 환율 조회가 실패해 외화 금액을 원화로 바꾸지 못하면 해당 알림은 보내지 않는다.
CURRENCIES = {
    "달러": ("USD", "달러"),
    "유로": ("EUR", "유로"),
    "엔": ("JPY", "엔"),
    "파운드": ("GBP", "파운드"),
    "위안": ("CNY", "위안"),
    "홍콩달러": ("HKD", "홍콩달러"),
    "싱가포르달러": ("SGD", "싱가포르달러"),
    "캐나다달러": ("CAD", "캐나다달러"),
    "호주달러": ("AUD", "호주달러"),
    "스위스프랑": ("CHF", "스위스프랑"),
}

SYMBOL_CODES = {
    "$": "USD",
    "US$": "USD",
    "€": "EUR",
    "£": "GBP",
    "¥": "JPY",
    "JP¥": "JPY",
    "CN¥": "CNY",
}

CODE_KOREAN = {
    "USD": "달러", "EUR": "유로", "JPY": "엔", "GBP": "파운드", "CNY": "위안",
    "HKD": "홍콩달러", "SGD": "싱가포르달러", "CAD": "캐나다달러",
    "AUD": "호주달러", "CHF": "스위스프랑",
}

_RATE_CACHE = {}


@lru_cache(maxsize=32)
def rate_to_krw(code: str):
    """ECB를 우선 사용하고, 해당 통화가 ECB 경로에서 없을 때만 Frankfurter 통합 소스로 재조회한다."""
    code = code.upper()
    urls = [
        (f"https://api.frankfurter.dev/v2/rate/{code}/KRW?providers=ECB", "ECB"),
        (f"https://api.frankfurter.dev/v2/rate/{code}/KRW", "Frankfurter 중앙은행 집계"),
    ]
    last_error = None
    for url, provider in urls:
        try:
            r = requests.get(url, headers=base.HEADERS, timeout=base.TIMEOUT)
            r.raise_for_status()
            data = r.json()
            # v2 single-rate 응답은 객체 형태를 사용한다.
            rate = data.get("rate") if isinstance(data, dict) else None
            date = data.get("date") if isinstance(data, dict) else None
            if rate is None and isinstance(data, dict):
                # API 형태 변경에 대비한 보조 파싱
                rate = (data.get("rates") or {}).get("KRW")
            rate = float(rate)
            if rate > 0:
                _RATE_CACHE[code] = (rate, str(date or ""), provider)
                return _RATE_CACHE[code]
        except Exception as e:
            last_error = e
    raise RuntimeError(f"{code}/KRW 환율 조회 실패: {last_error}")


def parse_korean_number(parts):
    jo, eok, man, unit = parts
    total = 0.0
    used = False
    for value, mult in ((jo, 1_000_000_000_000), (eok, 100_000_000), (man, 10_000), (unit, 1)):
        if value:
            used = True
            total += float(value.replace(",", "")) * mult
    return total if used else None


def format_krw(won: float) -> str:
    if won >= 1_000_000_000_000:
        jo = int(won // 1_000_000_000_000)
        eok = round((won - jo * 1_000_000_000_000) / 100_000_000)
        return f"약 {jo}조{eok:,.0f}억원" if eok else f"약 {jo}조원"
    if won >= 1_000_000_000:
        return f"약 {won / 100_000_000:,.0f}억원"
    if won >= 100_000_000:
        return f"약 {won / 100_000_000:,.2f}억원"
    if won >= 10_000:
        return f"약 {won / 10_000:,.0f}만원"
    return f"약 {won:,.0f}원"


def _already_has_krw(text: str, end: int) -> bool:
    tail = text[end:end + 45]
    return bool(re.match(r"\s*\(\s*약?\s*[0-9,.]+(?:조[0-9,.]*억|조|억|만)?원\s*\)", tail))


# 3억7,500만달러 / 5,355만2,620달러 / 100달러 등
KOREAN_MONEY_RE = re.compile(
    r"(?:(\d[\d,]*(?:\.\d+)?)조)?"
    r"(?:(\d[\d,]*(?:\.\d+)?)억)?"
    r"(?:(\d[\d,]*(?:\.\d+)?)만)?"
    r"(\d[\d,]*(?:\.\d+)?)?"
    r"(홍콩달러|싱가포르달러|캐나다달러|호주달러|스위스프랑|달러|유로|엔|파운드|위안)"
)

# $300M / US$ 100 million / €2.1 billion 등
SYMBOL_MONEY_RE = re.compile(
    r"(US\$|JP¥|CN¥|\$|€|£|¥)\s*"
    r"(\d[\d,]*(?:\.\d+)?)\s*"
    r"(billion|million|bn|mn|B|M)?\b",
    flags=re.I,
)

# 100 million USD / 2.5 billion EUR 등
CODE_MONEY_RE = re.compile(
    r"(\d[\d,]*(?:\.\d+)?)\s*"
    r"(billion|million|bn|mn|B|M)?\s*"
    r"(USD|EUR|JPY|GBP|CNY|RMB|HKD|SGD|CAD|AUD|CHF)\b",
    flags=re.I,
)


def scaled_number(num: str, scale: str) -> float:
    val = float(num.replace(",", ""))
    s = (scale or "").lower()
    if s in ("billion", "bn", "b"):
        val *= 1_000_000_000
    elif s in ("million", "mn", "m"):
        val *= 1_000_000
    return val


def convert_foreign_money(message: str) -> str:
    used_codes = set()

    def repl_korean(m):
        amount = parse_korean_number(m.groups()[:4])
        if amount is None or _already_has_krw(message, m.end()):
            return m.group(0)
        name = m.group(5)
        code = CURRENCIES[name][0]
        rate, _, _ = rate_to_krw(code)
        used_codes.add(code)
        return f"{m.group(0)}({format_krw(amount * rate)})"

    out = KOREAN_MONEY_RE.sub(repl_korean, message)

    def repl_symbol(m):
        if _already_has_krw(out, m.end()):
            return m.group(0)
        symbol = m.group(1)
        # 정규식 대소문자 허용 때문에 원래 표기를 표준키로 맞춘다.
        symbol_key = next((k for k in SYMBOL_CODES if k.lower() == symbol.lower()), symbol)
        code = SYMBOL_CODES.get(symbol_key)
        if not code:
            return m.group(0)
        amount = scaled_number(m.group(2), m.group(3))
        rate, _, _ = rate_to_krw(code)
        used_codes.add(code)
        return f"{m.group(0)}({format_krw(amount * rate)})"

    out = SYMBOL_MONEY_RE.sub(repl_symbol, out)

    def repl_code(m):
        if _already_has_krw(out, m.end()):
            return m.group(0)
        code = m.group(3).upper()
        if code == "RMB":
            code = "CNY"
        amount = scaled_number(m.group(1), m.group(2))
        rate, _, _ = rate_to_krw(code)
        used_codes.add(code)
        return f"{m.group(0)}({format_krw(amount * rate)})"

    out = CODE_MONEY_RE.sub(repl_code, out)

    # 환율 기준 줄은 중복 없이 마지막 원문 링크 직전에 모아서 표시한다.
    if used_codes:
        details = []
        for code in sorted(used_codes):
            rate, date, provider = rate_to_krw(code)
            label = CODE_KOREAN.get(code, code)
            if code == "JPY":
                details.append(f"100{label}={rate * 100:,.2f}원 · {provider} {date}")
            else:
                details.append(f"1{label}={rate:,.2f}원 · {provider} {date}")
        fx_line = "환산 기준: " + " / ".join(details)
        # 기존 단일 환산기준 줄이 있으면 최신 통합 줄로 교체한다.
        out = re.sub(r"\n환산 기준:[^\n]*", "", out)
        marker = "\n\n<a href="
        if marker in out:
            out = out.replace(marker, f"\n{html.escape(fx_line)}\n\n<a href=", 1)
        else:
            out += "\n" + html.escape(fx_line)

    return out


_original_build_message = base.build_message


def build_message_with_krw(item):
    message = _original_build_message(item)
    return convert_foreign_money(message)


base.build_message = build_message_with_krw


if __name__ == "__main__":
    v2.main()
