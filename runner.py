import html
import re
import xml.etree.ElementTree as ET

import requests

import monitor

TEST_TITLE = "President Donald J. Trump Rebuilds the U.S. Navy and America’s Shipbuilding Industrial Base"
TEST_TITLE_KO = "트럼프 대통령, 미 해군·미국 조선산업 기반 재건"

TITLE_OVERRIDES = {
    "Acting SECNAV Appoints Andrew Magliochetti to Lead Defense Industrial Base Revitalization": "미 해군장관 대행, 방위산업 기반 재활성화 책임자로 Andrew Magliochetti 임명",
    "Acting SECNAV Appoints Andrew Magliochetti to Lead Defense Industrial Base Revitalization…": "미 해군장관 대행, 방위산업 기반 재활성화 책임자로 Andrew Magliochetti 임명",
}

RARE_EARTH_TITLE_PREFIX = "Department of War Announces a $750 Million Investment as Part of a $1.55 Billion Initiative to Secure Critical Rare-Earth Elements"

ECB_FX_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
_FX_CACHE = None

UNTRANSLATED_GENERAL_TERMS = [
    "department of", "office of", "assistant secretary", "industrial base policy",
    "economic defense unit", "defense logistics agency", "money-center bank",
    "money center bank", "investment banking", "private equity", "structured finance",
    "title:", "source:", "posted time", "published time", "site search",
]


def has_untranslated_general_english(text: str) -> bool:
    low = (text or "").lower()
    return any(term in low for term in UNTRANSLATED_GENERAL_TERMS)


def get_ecb_fx():
    """ECB 최신 기준환율로 각 통화 1단위당 원화 환산값을 계산한다."""
    global _FX_CACHE
    if _FX_CACHE is not None:
        return _FX_CACHE
    try:
        resp = requests.get(ECB_FX_URL, headers=monitor.HEADERS, timeout=monitor.TIMEOUT)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        date = ""
        rates = {"EUR": 1.0}
        for node in root.iter():
            if "time" in node.attrib:
                date = node.attrib["time"]
            cur = node.attrib.get("currency")
            rate = node.attrib.get("rate")
            if cur and rate:
                rates[cur] = float(rate)
        krw_per_eur = rates.get("KRW")
        if not krw_per_eur:
            raise ValueError("ECB KRW 기준환율 없음")
        per_krw = {"EUR": krw_per_eur}
        for code, per_eur in rates.items():
            if code == "EUR":
                continue
            if per_eur:
                per_krw[code] = krw_per_eur / per_eur
        _FX_CACHE = {"date": date, "per_krw": per_krw}
        return _FX_CACHE
    except Exception as e:
        print(f"[WARN] ECB 환율 조회 실패: {e}")
        _FX_CACHE = {"date": "", "per_krw": {}}
        return _FX_CACHE


def format_krw(value: float) -> str:
    eok = int(round(value / 100_000_000))
    if eok >= 10000:
        jo, rem = divmod(eok, 10000)
        return f"{jo}조{rem:,}억원" if rem else f"{jo}조원"
    if eok >= 1:
        return f"{eok:,}억원"
    man = int(round(value / 10_000))
    return f"{man:,}만원"


def format_foreign(value: float, code: str) -> str:
    names = {"USD": "달러", "EUR": "유로", "JPY": "엔", "GBP": "파운드", "CNY": "위안"}
    unit = names.get(code, code)
    if value >= 100_000_000:
        eok = value / 100_000_000
        if abs(eok - round(eok)) < 1e-9:
            return f"{int(round(eok))}억{unit}"
        whole = int(eok)
        man = int(round((eok - whole) * 10000))
        return f"{whole}억{man:,}만{unit}"
    if value >= 10_000:
        man = value / 10_000
        return f"{man:,.0f}만{unit}"
    return f"{value:,.0f}{unit}"


def extract_currency_amounts(text: str):
    """영문 공식자료에서 주요 외화 금액을 원단위로 추출한다."""
    text = text or ""
    specs = [
        ("USD", r"(?:(?:US)?\$|USD\s*)([0-9][0-9,]*(?:\.[0-9]+)?)\s*(trillion|billion|million|bn|mn|b|m)?\b"),
        ("EUR", r"(?:€|EUR\s*)([0-9][0-9,]*(?:\.[0-9]+)?)\s*(trillion|billion|million|bn|mn|b|m)?\b"),
        ("GBP", r"(?:£|GBP\s*)([0-9][0-9,]*(?:\.[0-9]+)?)\s*(trillion|billion|million|bn|mn|b|m)?\b"),
        ("JPY", r"(?:JPY\s*)([0-9][0-9,]*(?:\.[0-9]+)?)\s*(trillion|billion|million|bn|mn|b|m)?\b"),
        ("CNY", r"(?:(?:CNY|RMB)\s*)([0-9][0-9,]*(?:\.[0-9]+)?)\s*(trillion|billion|million|bn|mn|b|m)?\b"),
    ]
    mult = {
        "trillion": 1_000_000_000_000,
        "billion": 1_000_000_000,
        "million": 1_000_000,
        "bn": 1_000_000_000,
        "mn": 1_000_000,
        "b": 1_000_000_000,
        "m": 1_000_000,
        None: 1,
        "": 1,
    }
    out = []
    seen = set()
    for code, pattern in specs:
        for match in re.finditer(pattern, text, flags=re.I):
            num = float(match.group(1).replace(",", ""))
            unit = (match.group(2) or "").lower()
            value = num * mult.get(unit, 1)
            key = (code, round(value, 2))
            if key not in seen:
                seen.add(key)
                out.append((code, value))
    return out


def fx_annotation(original_text: str, fx, max_items=3) -> str:
    items = extract_currency_amounts(original_text)
    converted = []
    for code, value in items:
        rate = fx.get("per_krw", {}).get(code)
        if not rate:
            continue
        converted.append(f"{format_foreign(value, code)}≈{format_krw(value * rate)}")
        if len(converted) >= max_items:
            break
    if not converted:
        return ""
    if len(converted) == 1:
        return f" (원화 약 {converted[0].split('≈', 1)[1]})"
    return " (원화: " + " · ".join(converted) + ")"


def displayed_currency_codes(text: str):
    """실제 텔레그램에 표시되는 문구에 외화 금액이 있을 때만 환율 기준을 붙인다."""
    text = text or ""
    codes = {code for code, _ in extract_currency_amounts(text)}
    patterns = {
        "USD": r"\d[\d,.]*(?:조|억|만)?\s*달러",
        "EUR": r"\d[\d,.]*(?:조|억|만)?\s*유로",
        "JPY": r"\d[\d,.]*(?:조|억|만)?\s*엔",
        "GBP": r"\d[\d,.]*(?:조|억|만)?\s*파운드",
        "CNY": r"\d[\d,.]*(?:조|억|만)?\s*위안",
    }
    for code, pattern in patterns.items():
        if re.search(pattern, text):
            codes.add(code)
    return codes


def fx_rate_line(fx, codes):
    if not fx.get("date") or not codes:
        return ""
    pieces = []
    labels = {"USD": "달러", "EUR": "유로", "JPY": "엔", "GBP": "파운드", "CNY": "위안"}
    for code in ["USD", "EUR", "JPY", "GBP", "CNY"]:
        if code not in codes:
            continue
        rate = fx.get("per_krw", {}).get(code)
        if not rate:
            continue
        if code == "JPY":
            pieces.append(f"100엔={rate * 100:,.0f}원")
        else:
            pieces.append(f"1{labels.get(code, code)}={rate:,.0f}원")
    if not pieces:
        return ""
    return f"환산 기준: {' · '.join(pieces)} · ECB {fx['date']}"


def polish_korean(text: str) -> str:
    text = " ".join((text or "").split())
    replacements = {
        "도널드 J. 트럼프 대통령": "트럼프 대통령",
        "미 해군과 미국의 조선 산업 기지를 재건하다": "미 해군·미국 조선산업 기반 재건",
        "미 해군과 미국 조선 산업 기반을 재건하다": "미 해군·미국 조선산업 기반 재건",
        "미국의 조선 산업 기반": "미국 조선산업 기반",
        "조선 산업 기지": "조선산업 기반",
        "조선 산업 기반": "조선산업 기반",
        "전 미국 인력": "미국인 인력",
        "미국 전역의 인력": "미국인 인력",
        "모 조선소": "모(母)조선소",
        "교량 간격": "함정 인도 공백",
        "교량 격차": "함정 인도 공백",
        "빠른 처리": "신속 인도",
        "활성화된 미국 조선소": "재활성화된 미국 조선소",
        "다섯 번째 해군 조선소": "제5 해군 공창",
        "해군 해상 시스템 사령부": "해군 해상체계사령부(NAVSEA)",
        "해군 해상 체계 사령부": "해군 해상체계사령부(NAVSEA)",
        "전쟁부 (DoW)": "미 전쟁부(DoW)",
        "전쟁부(DoW)": "미 전쟁부(DoW)",
        "경제 국방부 (EDU)": "경제방위부(EDU)",
        "경제 방위부 (EDU)": "경제방위부(EDU)",
        "경제 국방부(EDU)": "경제방위부(EDU)",
        "산업 기반 정책 (IBP) 을 위한 전쟁 보좌관실": "산업기반정책 담당 차관보실(IBP)",
        "산업 기반 정책을 위한 전쟁 보좌관실": "산업기반정책 담당 차관보실(IBP)",
        "산업 기반 정책": "산업기반정책",
        "산업 기반 분석 및 유지": "산업기반 분석·유지",
        "화폐 중심 은행": "대형 상업은행",
        "머니 센터 은행": "대형 상업은행",
        "세르 베르데": "Serra Verde",
        "세르베르데": "Serra Verde",
        "Serre Verde": "Serra Verde",
        "희토류 요소": "희토류 원소",
        "혼합 희토류 탄산염": "혼합 희토류 탄산염(MREC)",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text.strip()


def split_candidates(blocks):
    out = []
    seen = set()
    for block in blocks:
        for part in re.split(r"(?<=[.!?])\s+|;\s+", block):
            part = " ".join(part.split()).strip()
            if not 28 <= len(part) <= 700:
                continue

            # 원문 페이지의 URL·검색·게시시각 같은 메타 정보는 요약 bullet에 넣지 않는다.
            low = part.lower()
            if (
                "http://" in low
                or "https://" in low
                or low.startswith("url source")
                or low.startswith("source url")
                or "site search" in low
                or low.startswith("search:")
                or low.startswith("posted time")
                or low.startswith("published time")
                or low.startswith("publish date")
                or low.startswith("title:")
                or low.startswith("date:")
                or low.startswith("source:")
                or low.startswith("byline:")
            ):
                continue

            key = re.sub(r"\W+", " ", part.lower()).strip()
            if key and key not in seen:
                seen.add(key)
                out.append(part)
    return out


def token_overlap(a: str, b: str) -> float:
    aa = set(re.findall(r"[a-z0-9]+", a.lower()))
    bb = set(re.findall(r"[a-z0-9]+", b.lower()))
    if not aa or not bb:
        return 0.0
    return len(aa & bb) / max(1, min(len(aa), len(bb)))


def score_candidate(text: str, idx: int) -> int:
    low = text.lower()
    weights = {
        "foreign shipbuilder": 12,
        "up to two ships": 14,
        "two ships": 10,
        "parent shipyard": 12,
        "additional ships will be built": 11,
        "naval shipyard": 9,
        "fifth": 7,
        "navsea": 10,
        "reorgan": 8,
        "reform": 7,
        "national security presidential memorandum": 9,
        "ship repair": 6,
        "shipbuilding": 5,
        "submarine": 5,
        "aircraft carrier": 5,
        "icebreaker": 5,
        "investment": 4,
        "contract": 4,
        "award": 4,
    }
    score = sum(weight for term, weight in weights.items() if term in low)
    if re.search(r"\$|\b\d+(?:\.\d+)?%?\b", text):
        score += 3
    if idx < 20:
        score += 2
    return score


def fallback_bullets(blocks, fx, limit=3):
    """정형 규칙으로 잡히지 않는 일반 자료만 짧게 보조 요약한다."""
    candidates = split_candidates(blocks)
    ranked = sorted(
        ((score_candidate(text, idx), idx, text) for idx, text in enumerate(candidates)),
        key=lambda x: (-x[0], x[1]),
    )
    chosen_en = []
    result = []
    for score, _, text in ranked:
        if score <= 0:
            continue
        if any(token_overlap(text, other) >= 0.55 for other in chosen_en):
            continue
        chosen_en.append(text)
        translated = polish_korean(monitor.translate_piece(text))
        translated = monitor.compact_korean(translated, 125)
        if translated and monitor.has_korean(translated) and not has_untranslated_general_english(translated):
            translated += fx_annotation(text, fx, max_items=2)
            if translated not in result:
                result.append(translated)
        if len(result) >= limit:
            break
    return result


def structured_bullets(blocks, fx):
    full = " ".join(blocks)
    low = full.lower()
    bullets = []

    if "serra verde" in low and "mixed rare-earth carbonates" in low and "$750 million" in low:
        usd = fx.get("per_krw", {}).get("USD")
        a750 = format_krw(750_000_000 * usd) if usd else "원화 환산 확인 불가"
        a1550 = format_krw(1_550_000_000 * usd) if usd else "원화 환산 확인 불가"
        a300 = format_krw(300_000_000 * usd) if usd else "원화 환산 확인 불가"
        a500 = format_krw(500_000_000 * usd) if usd else "원화 환산 확인 불가"
        bullets.append(f"미 전쟁부(DoW)가 Serra Verde의 브라질 Pela Ema 프로젝트 희토류 장기구매를 지원하기 위해 7억5,000만달러(약 {a750}) 투자")
        bullets.append(f"총 15억5,000만달러(약 {a1550}) 구조로, 국방군수국(DLA) 3억달러(약 {a300}) 구매약정과 대형 상업은행 5억달러(약 {a500}) 약정 포함")
        bullets.append("대상은 디스프로슘·터븀·네오디뮴·프라세오디뮴 등 핵심 희토류로, 중국 의존도를 낮추고 미 방산·자석 공급망을 확보하는 목적")
        return bullets

    if "andrew magliochetti" in low and "defense industrial base" in low:
        bullets.append("미 해군장관 대행 Hung Cao가 Andrew Magliochetti를 방위산업 기반 재활성화 신규 이니셔티브 책임자로 임명")
        if "25 years" in low and any(term in low for term in ["structured finance", "investment banking", "private equity"]):
            bullets.append("Magliochetti는 구조화금융·투자은행·사모펀드 등 민간 부문 경력 25년 보유")
        if "venture capital" in low and "national security" in low:
            bullets.append("최근에는 국가안보 기술 중심 벤처캐피털의 공동창업자·운용 파트너로 활동")

    if "national security presidential memorandum" in low and ("shipbuilding" in low or "ship repair" in low):
        bullets.append("미 해군 함정 건조·수리 프로그램의 지연·비용 문제 해결을 위한 국가안보 대통령 각서에 서명")

    if "foreign shipbuilders" in low and ("up to two ships" in low or "two ships" in low):
        bullets.append("미국 조선소에 지속 투자하고 미국인 인력을 훈련하는 외국 조선업체는 모(母)조선소에서 최대 2척까지 임시 건조 가능")

    if "additional ships will be built" in low and "american shipyards" in low:
        bullets.append("추가 함정은 재활성화된 미국 조선소에서 건조")

    if "fifth naval shipyard" in low or ("fifth" in low and "naval shipyard" in low):
        bullets.append("제5 해군 공창 신설로 잠수함·항공모함 수리 능력 확대")

    if "navsea" in low and any(term in low for term in ["reorgan", "reform", "overhaul", "review"]):
        bullets.append("해군 해상체계사령부(NAVSEA) 조직 개편 추진")

    return bullets[:5]


def build_message(item):
    fx = get_ecb_fx()
    raw_title = item.get("title", "")
    if raw_title.startswith(RARE_EARTH_TITLE_PREFIX):
        usd = fx.get("per_krw", {}).get("USD")
        if usd:
            title_ko = (
                "미 전쟁부, Serra Verde 희토류 공급망 확보에 "
                f"7억5,000만달러(약 {format_krw(750_000_000 * usd)}) 투자…"
                f"총 15억5,000만달러(약 {format_krw(1_550_000_000 * usd)}) 규모"
            )
        else:
            title_ko = "미 전쟁부, Serra Verde 희토류 공급망 확보에 7억5,000만달러 투자…총 15억5,000만달러 규모"
    elif raw_title == TEST_TITLE:
        title_ko = TEST_TITLE_KO
    elif raw_title in TITLE_OVERRIDES:
        title_ko = TITLE_OVERRIDES[raw_title]
    else:
        title_ko = polish_korean(monitor.translate_piece(raw_title))
        title_ko = monitor.compact_korean(title_ko, 90)
    if not monitor.has_korean(title_ko) or has_untranslated_general_english(title_ko):
        title_ko = "미국 조선·해군 관련 새 공식 발표"

    # 정형 제목이 아닌 일반 자료도 제목에 외화 금액이 있으면 원화 환산을 붙인다.
    if not raw_title.startswith(RARE_EARTH_TITLE_PREFIX):
        title_fx = fx_annotation(raw_title, fx, max_items=2)
        if title_fx and "원화" not in title_ko:
            title_ko = monitor.compact_korean(title_ko, 105) + title_fx

    blocks = monitor.extract_article_blocks(item["url"])
    bullets = structured_bullets(blocks, fx)

    if len(bullets) < 2:
        bullets = fallback_bullets(blocks, fx, limit=3)

    if not bullets and item.get("summary"):
        summary = polish_korean(monitor.translate_piece(item["summary"]))
        if summary and monitor.has_korean(summary) and not has_untranslated_general_english(summary):
            bullets = [monitor.compact_korean(summary, 125) + fx_annotation(item["summary"], fx, max_items=2)]

    if not bullets:
        bullets = ["새로운 공식자료가 감지되었습니다. 세부 내용은 원문에서 확인할 수 있습니다."]

    safe_title = html.escape(title_ko or "미국 조선·해군 관련 새 공식 발표")
    safe_source = html.escape(item.get("source", "공식자료"))
    safe_url = html.escape(item["url"], quote=True)
    bullet_text = "\n".join(f"• {html.escape(b)}" for b in bullets[:5])

    # 원문 전체에 외화가 있어도, 실제 알림 제목·핵심 bullet에 금액이 없으면 환율 줄을 붙이지 않는다.
    display_currency_text = " ".join([title_ko] + bullets[:5])
    codes = displayed_currency_codes(display_currency_text)
    rate_line = fx_rate_line(fx, codes)
    rate_html = f"\n\n{html.escape(rate_line)}" if rate_line else ""

    return (
        "🚨 <b>미국 조선·해군 정책 중요 변화</b>\n\n"
        f"<b>{safe_title}</b>\n"
        f"출처: <a href=\"{safe_url}\">{safe_source}</a>\n\n"
        f"{bullet_text}"
        f"{rate_html}\n\n"
        f"🔎 <a href=\"{safe_url}\"><b>원문</b></a>"
    )


monitor.build_message = build_message
monitor.main()
