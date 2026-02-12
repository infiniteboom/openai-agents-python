from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from agents import RunContextWrapper, function_tool

from .schema import InquiryQuote


@dataclass
class InquiryContext:
    """Runtime context injected by the caller."""

    current_date: date


def _last_day_of_month(year: int, month: int) -> int:
    # Local helper to avoid bringing in additional dependencies.
    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)
    return (next_month - timedelta(days=1)).day


def add_months(start: date, months: float) -> date:
    """Add (possibly fractional) months to a date.

    Whole months are calendar months; fractional months are approximated as 30 days.
    """

    whole = int(months)
    frac = months - whole

    year = start.year
    month = start.month + whole
    year += (month - 1) // 12
    month = (month - 1) % 12 + 1

    day = min(start.day, _last_day_of_month(year, month))
    out = date(year, month, day)

    if abs(frac) > 1e-9:
        out = out + timedelta(days=int(round(frac * 30)))
    return out


def add_natural_days(start: date, days: int) -> date:
    return start + timedelta(days=days)


def add_trading_days_weekends_only(start: date, days: int) -> date:
    """Add trading days skipping weekends only (no holiday calendar)."""

    if days <= 0:
        return start

    d = start
    remaining = days
    while remaining > 0:
        d = d + timedelta(days=1)
        if d.weekday() >= 5:  # 5=Sat, 6=Sun
            continue
        remaining -= 1
    return d


def infer_contract_code(text: str, *, current_date: date) -> str | None:
    """Infer contract_code from text like 'hc10' or 'HC2610'."""

    t = text.strip()
    # Prefer explicit YYMM forms (e.g. hc2610).
    m = re.search(r"(?i)(?<![a-z])([a-z]{1,6})\s*([0-9]{4})(?![0-9])", t)
    if m:
        product = m.group(1).upper()
        yymm = m.group(2)
        mm = int(yymm[2:])
        if 1 <= mm <= 12:
            return f"{product}{yymm}"
        return None

    # Next: product + month (e.g. hc10).
    m = re.search(r"(?i)(?<![a-z])([a-z]{1,6})\s*([0-9]{1,2})(?![0-9])", t)
    if not m:
        return None

    product = m.group(1).upper()
    month = int(m.group(2))
    if not (1 <= month <= 12):
        return None

    year = current_date.year % 100
    if month < current_date.month:
        year = (year + 1) % 100

    return f"{product}{year:02d}{month:02d}"


def infer_call_put(text: str) -> Literal[1, 2] | None:
    t = text.lower()
    if any(k in text for k in ("看涨", "认购")) or "call" in t:
        return 1
    if any(k in text for k in ("看跌", "认沽")) or "put" in t:
        return 2
    return None


def infer_buy_sell(text: str) -> Literal[1, -1] | None:
    """Infer customer buy/sell direction from common phrasing.

    Returns:
      1 for customer buys, -1 for customer sells, or None if ambiguous.
    """

    candidates: set[int] = set()

    # Most explicit first.
    if any(k in text for k in ("客户买", "客户买入")):
        candidates.add(1)
    if any(k in text for k in ("客户卖", "客户卖出")):
        candidates.add(-1)

    # Perspective flip: "we sell" -> customer buys; "we buy" -> customer sells.
    if any(k in text for k in ("我方卖", "我们卖", "卖给你")) or "offer" in text.lower():
        candidates.add(1)
    if any(k in text for k in ("我方买", "我们买", "从你买")) or "bid" in text.lower():
        candidates.add(-1)

    # Generic verbs (lowest confidence).
    if "买入" in text and "我方买入" not in text and "客户买入" not in text:
        candidates.add(1)
    if "卖出" in text and "我方卖出" not in text and "客户卖出" not in text:
        candidates.add(-1)

    if len(candidates) == 1:
        return 1 if 1 in candidates else -1
    return None


def _infer_moneyness_offset(text: str) -> float | None:
    if any(k in text for k in ("平值", "ATM", "at-the-money", "at the money")):
        return 0.0

    m = re.search(r"(实|虚)\s*([0-9]+(?:\.[0-9]+)?)", text)
    if not m:
        return None
    sign = 1.0 if m.group(1) == "实" else -1.0
    return sign * float(m.group(2))


def _infer_year_for_month_day(current: date, month: int, day: int) -> int:
    # If the target MM-DD is earlier than today, default to next year.
    if (month, day) < (current.month, current.day):
        return current.year + 1
    return current.year


def _parse_expire_date_str(s: str, *, current_date: date) -> date | None:
    s = s.strip()
    # YYYY-MM-DD / YYYY/M/D / YYYY.MM.DD
    m = re.match(r"^([0-9]{4})\D([0-9]{1,2})\D([0-9]{1,2})$", s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None

    # M月D日 (year inferred)
    m = re.match(r"^([0-9]{1,2})\s*月\s*([0-9]{1,2})\s*[日号]?$", s)
    if m:
        month = int(m.group(1))
        day = int(m.group(2))
        year = _infer_year_for_month_day(current_date, month, day)
        try:
            return date(year, month, day)
        except ValueError:
            return None

    return None


_CN_NUM = {
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}


def _infer_relative_expiry_from_text(text: str) -> tuple[str, float | int] | None:
    """Return (kind, value) where kind in {'months','natural_days','trading_days'}."""

    # Trading days.
    m = re.search(r"([0-9]+)\s*个?\s*交易日", text)
    if m:
        return ("trading_days", int(m.group(1)))

    # Natural days: avoid matching "交易日".
    m = re.search(r"([0-9]+)\s*(天|日)", text)
    if m:
        return ("natural_days", int(m.group(1)))

    # Months (digits).
    m = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*个?\s*月", text)
    if m:
        return ("months", float(m.group(1)))

    # Months (Chinese numerals).
    m = re.search(r"(半|一|二|两|三|四|五|六|七|八|九|十)\s*个?\s*月", text)
    if m:
        if m.group(1) == "半":
            return ("months", 0.5)
        return ("months", float(_CN_NUM[m.group(1)]))

    return None


def infer_expire_date(
    text: str,
    *,
    current_date: date,
    explicit_expire_date: str | None = None,
    expire_in_months: float | None = None,
    expire_in_natural_days: int | None = None,
    expire_in_trading_days: int | None = None,
) -> str | None:
    """Resolve expire_date with precedence: absolute date > relative."""

    if explicit_expire_date:
        d = _parse_expire_date_str(explicit_expire_date, current_date=current_date)
        if d:
            return d.isoformat()

    if expire_in_months is not None:
        return add_months(current_date, expire_in_months).isoformat()
    if expire_in_trading_days is not None:
        return add_trading_days_weekends_only(current_date, expire_in_trading_days).isoformat()
    if expire_in_natural_days is not None:
        return add_natural_days(current_date, expire_in_natural_days).isoformat()

    # Infer from text (absolute first, then relative).
    m = re.search(r"([0-9]{4}\D[0-9]{1,2}\D[0-9]{1,2})", text)
    if m:
        d = _parse_expire_date_str(m.group(1), current_date=current_date)
        if d:
            return d.isoformat()

    m = re.search(r"([0-9]{1,2})\s*月\s*([0-9]{1,2})\s*[日号]?", text)
    if m:
        month = int(m.group(1))
        day = int(m.group(2))
        year = _infer_year_for_month_day(current_date, month, day)
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            pass

    rel = _infer_relative_expiry_from_text(text)
    if rel:
        kind, value = rel
        if kind == "months":
            return add_months(current_date, float(value)).isoformat()
        if kind == "trading_days":
            return add_trading_days_weekends_only(current_date, int(value)).isoformat()
        if kind == "natural_days":
            return add_natural_days(current_date, int(value)).isoformat()

    return None


def normalize_quote(
    text: str,
    *,
    current_date: date,
    contract_code: str | None = None,
    call_put: Literal[1, 2] | None = None,
    buy_sell: Literal[1, -1] | None = None,
    strike: float | None = None,
    strike_offset: float | None = None,
    underlying_price: float | None = None,
    expire_date: str | None = None,
    expire_in_months: float | None = None,
    expire_in_natural_days: int | None = None,
    expire_in_trading_days: int | None = None,
) -> InquiryQuote:
    # Contract code.
    contract = contract_code or infer_contract_code(text, current_date=current_date)

    # Call/put.
    cp = call_put or infer_call_put(text)

    # Direction.
    bs = buy_sell or infer_buy_sell(text)

    # Strike precedence: strike > strike_offset.
    s = strike
    so = strike_offset
    if s is not None:
        so = None
    elif so is None:
        so = _infer_moneyness_offset(text)

    exp = infer_expire_date(
        text,
        current_date=current_date,
        explicit_expire_date=expire_date,
        expire_in_months=expire_in_months,
        expire_in_natural_days=expire_in_natural_days,
        expire_in_trading_days=expire_in_trading_days,
    )

    return InquiryQuote(
        contract_code=contract,
        call_put=cp,
        buy_sell=bs,
        strike=s,
        strike_offset=so,
        underlying_price=underlying_price,
        expire_date=exp,
    )


@function_tool
def get_expire_date_by_months(ctx: RunContextWrapper[InquiryContext], months: float) -> str:
    """Compute an expiration date by adding calendar months to the current date."""

    return add_months(ctx.context.current_date, months).isoformat()


@function_tool
def get_expire_date_by_natural_date(ctx: RunContextWrapper[InquiryContext], days: int) -> str:
    """Compute an expiration date by adding natural days (no weekend/holiday skipping)."""

    return add_natural_days(ctx.context.current_date, days).isoformat()


@function_tool
def get_expire_date_by_trading_date(ctx: RunContextWrapper[InquiryContext], days: int) -> str:
    """Compute an expiration date by adding trading days (weekends skipped; holidays ignored)."""

    return add_trading_days_weekends_only(ctx.context.current_date, days).isoformat()


@function_tool
def normalize_inquiry(
    ctx: RunContextWrapper[InquiryContext],
    text: str,
    contract_code: str | None = None,
    call_put: Literal[1, 2] | None = None,
    buy_sell: Literal[1, -1] | None = None,
    strike: float | None = None,
    strike_offset: float | None = None,
    underlying_price: float | None = None,
    expire_date: str | None = None,
    expire_in_months: float | None = None,
    expire_in_natural_days: int | None = None,
    expire_in_trading_days: int | None = None,
) -> InquiryQuote:
    """Tool entrypoint: return a strict `InquiryQuote` object without follow-up questions."""

    return normalize_quote(
        text,
        current_date=ctx.context.current_date,
        contract_code=contract_code,
        call_put=call_put,
        buy_sell=buy_sell,
        strike=strike,
        strike_offset=strike_offset,
        underlying_price=underlying_price,
        expire_date=expire_date,
        expire_in_months=expire_in_months,
        expire_in_natural_days=expire_in_natural_days,
        expire_in_trading_days=expire_in_trading_days,
    )
