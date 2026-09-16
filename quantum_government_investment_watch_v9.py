import quantum_government_investment_watch as base
import quantum_government_investment_watch_v2 as v2
import quantum_government_investment_watch_v7 as v7
import quantum_government_investment_watch_v8  # strict policy scope patches base.relevant


# v7의 한국어 통화 정규식은 숫자부가 모두 선택형이라 '달러'라는 단어만 있어도
# 미환산 외화로 오인할 수 있었다. 실제 금액 숫자가 있는 경우만 환산 검증 대상으로 본다.
def _all_foreign_money_converted_fixed(text: str) -> bool:
    patterns = [v7.v6.KOREAN_MONEY_RE, v7.v6.SYMBOL_MONEY_RE, v7.v6.CODE_MONEY_RE, v7.HYBRID_SYMBOL_KOREAN_RE]
    for pat in patterns:
        for m in pat.finditer(text):
            if pat is v7.v6.KOREAN_MONEY_RE:
                amount = v7.v6.parse_korean_number(m.groups()[:4])
                if amount is None:
                    continue
            elif pat is v7.HYBRID_SYMBOL_KOREAN_RE:
                if v7._hybrid_amount(m.groups()[1:4]) is None:
                    continue
            if not v7._has_krw_immediately_after(text, m.end()):
                return False
    return True


v7._all_foreign_money_converted = _all_foreign_money_converted_fixed

# v7이 v6의 변환 함수로 연결해 둔 strict converter는 런타임에 위 전역 함수를 참조한다.
base.build_message = v7.v6.build_message_with_krw


if __name__ == "__main__":
    v2.main()
