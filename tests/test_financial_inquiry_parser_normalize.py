from __future__ import annotations

from datetime import date

from examples.financial_inquiry_parser.normalize import (
    add_months,
    add_trading_days_weekends_only,
    infer_buy_sell,
    infer_call_put,
    infer_contract_code,
    infer_expire_date,
    normalize_quote,
)


def test_infer_contract_code_month_only_year_completion() -> None:
    assert infer_contract_code("hc10合约", current_date=date(2026, 2, 12)) == "HC2610"
    assert infer_contract_code("hc01", current_date=date(2026, 11, 30)) == "HC2701"


def test_infer_contract_code_explicit_yymm() -> None:
    assert infer_contract_code("HC2610", current_date=date(2026, 2, 12)) == "HC2610"
    assert infer_contract_code("hc2610合约", current_date=date(2026, 2, 12)) == "HC2610"


def test_infer_buy_sell_synonyms_and_ambiguity() -> None:
    assert infer_buy_sell("我方卖一个月平值期权") == 1
    assert infer_buy_sell("我方买一个月平值期权") == -1
    assert infer_buy_sell("客户买入") == 1
    assert infer_buy_sell("客户卖出") == -1
    assert infer_buy_sell("我方买 客户买") is None


def test_infer_call_put_keywords() -> None:
    assert infer_call_put("认购") == 1
    assert infer_call_put("认沽") == 2
    assert infer_call_put("call option") == 1
    assert infer_call_put("put option") == 2


def test_add_trading_days_weekends_only() -> None:
    # 2026-02-12 is Thursday.
    assert add_trading_days_weekends_only(date(2026, 2, 12), 1) == date(2026, 2, 13)
    assert add_trading_days_weekends_only(date(2026, 2, 12), 2) == date(2026, 2, 16)


def test_add_months_clamps_month_end() -> None:
    assert add_months(date(2026, 2, 12), 1) == date(2026, 3, 12)
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)


def test_expire_date_precedence_absolute_over_relative() -> None:
    out = infer_expire_date(
        "一个月",
        current_date=date(2026, 2, 12),
        explicit_expire_date="2026-04-15",
        expire_in_months=1,
    )
    assert out == "2026-04-15"


def test_infer_expire_date_absolute_month_day_cross_year() -> None:
    assert (
        infer_expire_date("4月15到期", current_date=date(2026, 2, 12)) == "2026-04-15"
    )
    assert (
        infer_expire_date("1月5到期", current_date=date(2026, 11, 20)) == "2027-01-05"
    )


def test_infer_expire_date_relative_from_text() -> None:
    assert infer_expire_date("一个月", current_date=date(2026, 2, 12)) == "2026-03-12"
    assert infer_expire_date("20天", current_date=date(2026, 2, 12)) == "2026-03-04"
    assert infer_expire_date("2个交易日", current_date=date(2026, 2, 12)) == "2026-02-16"


def test_strike_precedence_over_strike_offset() -> None:
    quote = normalize_quote(
        "hc10",
        current_date=date(2026, 2, 12),
        strike=3500.0,
        strike_offset=-30.0,
    )
    assert quote.strike == 3500.0
    assert quote.strike_offset is None


def test_strike_offset_moneyness_parsing() -> None:
    assert (
        normalize_quote("平值", current_date=date(2026, 2, 12)).strike_offset == 0.0
    )
    assert (
        normalize_quote("实30", current_date=date(2026, 2, 12)).strike_offset == 30.0
    )
    assert (
        normalize_quote("虚30", current_date=date(2026, 2, 12)).strike_offset == -30.0
    )

