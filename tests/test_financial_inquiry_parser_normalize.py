from __future__ import annotations

from datetime import date

from examples.financial_inquiry_parser.normalize import (
    add_months,
    add_trading_days_weekends_only,
    build_contract_code_from_parts,
    find_product_candidates,
    infer_contract_code,
    infer_expire_date,
    normalize_contract_code,
    normalize_quote,
)


def test_infer_contract_code_month_only_year_completion() -> None:
    assert infer_contract_code("hc10合约", current_date=date(2026, 2, 12)) == "HC2610"
    assert infer_contract_code("hc01", current_date=date(2026, 11, 30)) == "HC2701"


def test_infer_contract_code_explicit_yymm() -> None:
    assert infer_contract_code("HC2610", current_date=date(2026, 2, 12)) == "HC2610"
    assert infer_contract_code("hc2610合约", current_date=date(2026, 2, 12)) == "HC2610"


def test_find_product_candidates_from_chinese_alias() -> None:
    candidates = find_product_candidates("热卷05合约，报价", top_k=3)
    assert candidates
    assert candidates[0].product_code == "HC"


def test_find_product_candidates_from_generated_full_name_alias() -> None:
    candidates = find_product_candidates("热轧卷板05合约", top_k=3)
    assert candidates
    assert candidates[0].product_code == "HC"


def test_find_product_candidates_from_code_token() -> None:
    candidates = find_product_candidates("rb10 看跌报价", top_k=3)
    assert any(candidate.product_code == "RB" for candidate in candidates)


def test_build_contract_code_from_parts() -> None:
    assert (
        build_contract_code_from_parts(
            current_date=date(2026, 2, 12), product_code="hc", contract_month=10
        )
        == "HC2610"
    )
    assert (
        build_contract_code_from_parts(
            current_date=date(2026, 11, 30), product_code="hc", contract_month=1
        )
        == "HC2701"
    )
    assert (
        build_contract_code_from_parts(
            current_date=date(2026, 11, 30),
            product_code="hc",
            contract_month=1,
            contract_year=2028,
        )
        == "HC2801"
    )
    assert (
        build_contract_code_from_parts(
            current_date=date(2026, 2, 12), product_code="oi", contract_month=5
        )
        == "OI605"
    )
    assert (
        build_contract_code_from_parts(
            current_date=date(2026, 2, 12), product_code="oi", contract_month=5, contract_year=6
        )
        == "OI605"
    )


def test_normalize_contract_code_from_short_input() -> None:
    assert normalize_contract_code("hc10", current_date=date(2026, 2, 12)) == "HC2610"
    assert normalize_contract_code("hc2610", current_date=date(2026, 2, 12)) == "HC2610"


def test_normalize_quote_contract_from_parts_only() -> None:
    quote = normalize_quote(
        current_date=date(2026, 2, 12),
        product_code="hc",
        contract_month=10,
    )
    assert quote.contract_code == "HC2610"


def test_add_trading_days_weekends_only() -> None:
    # 2026-02-12 is Thursday.
    assert add_trading_days_weekends_only(date(2026, 2, 12), 1) == date(2026, 2, 13)
    assert add_trading_days_weekends_only(date(2026, 2, 12), 2) == date(2026, 2, 16)


def test_add_months_clamps_month_end() -> None:
    assert add_months(date(2026, 2, 12), 1) == date(2026, 3, 12)
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)


def test_expire_date_precedence_absolute_over_relative() -> None:
    out = infer_expire_date(
        current_date=date(2026, 2, 12),
        explicit_expire_date="2026-04-15",
        expire_in_months=1,
    )
    assert out == "2026-04-15"


def test_infer_expire_date_absolute_month_day_cross_year() -> None:
    assert (
        infer_expire_date(current_date=date(2026, 2, 12), explicit_expire_date="4月15日")
        == "2026-04-15"
    )
    assert (
        infer_expire_date(current_date=date(2026, 11, 20), explicit_expire_date="1月5日")
        == "2027-01-05"
    )


def test_infer_expire_date_absolute_varied_separators() -> None:
    assert (
        infer_expire_date(current_date=date(2026, 2, 12), explicit_expire_date="2026/4/15")
        == "2026-04-15"
    )
    assert (
        infer_expire_date(current_date=date(2026, 2, 12), explicit_expire_date="2026.4.15")
        == "2026-04-15"
    )


def test_infer_expire_date_relative_from_explicit_args() -> None:
    assert infer_expire_date(current_date=date(2026, 2, 12), expire_in_months=1) == "2026-03-12"
    assert (
        infer_expire_date(current_date=date(2026, 2, 12), expire_in_natural_days=20) == "2026-03-04"
    )
    assert (
        infer_expire_date(current_date=date(2026, 2, 12), expire_in_trading_days=2) == "2026-02-16"
    )


def test_strike_precedence_over_strike_offset() -> None:
    quote = normalize_quote(
        current_date=date(2026, 2, 12),
        strike=3500.0,
        strike_offset=-30.0,
    )
    assert quote.strike == 3500.0
    assert quote.strike_offset is None


def test_normalize_quote_has_no_text_fallback_parsing() -> None:
    quote = normalize_quote(
        current_date=date(2026, 2, 12),
    )
    assert quote.contract_code is None
    assert quote.call_put is None
    assert quote.buy_sell is None
    assert quote.strike_offset is None
    assert quote.expire_date is None
