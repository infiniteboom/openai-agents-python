from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Annotated, Literal, cast

from dateutil import parser as dateutil_parser  # type: ignore[import-untyped]
from pydantic import Field
from rapidfuzz import fuzz as rapidfuzz_fuzz, process as rapidfuzz_process

from agents import RunContextWrapper, function_tool

from .generated_contract_suffix_rules import PRODUCT_SUFFIX_FORMAT
from .generated_product_aliases import PRODUCT_ALIAS_TO_CODE
from .schema import InquiryQuote, ProductCandidate


@dataclass
class InquiryContext:
    """Runtime context injected by the caller."""

    current_date: date


def _normalize_product_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).upper()
    return re.sub(r"[^A-Z0-9\u4e00-\u9fff]+", "", normalized)


def _build_product_alias_entries() -> list[tuple[str, str, str]]:
    alias_to_code: dict[str, str] = {}

    for alias, mapped_code in PRODUCT_ALIAS_TO_CODE.items():
        canonical_code = mapped_code.strip().upper()
        alias_to_code[alias] = canonical_code

    return [(alias, _normalize_product_text(alias), code) for alias, code in alias_to_code.items()]


_PRODUCT_ALIAS_ENTRIES = _build_product_alias_entries()
_PRODUCT_ALIAS_TO_CODE = {alias: code for alias, _, code in _PRODUCT_ALIAS_ENTRIES}
_KNOWN_PRODUCT_CODES = {code for _, _, code in _PRODUCT_ALIAS_ENTRIES}


def _upsert_candidate(
    candidates: dict[str, ProductCandidate], *, product_code: str, matched_alias: str, score: float
) -> None:
    bounded_score = max(0.0, min(100.0, float(score)))
    existing = candidates.get(product_code)
    if existing is None or bounded_score > existing.score:
        candidates[product_code] = ProductCandidate(
            product_code=product_code,
            matched_alias=matched_alias,
            score=round(bounded_score, 2),
        )


def find_product_candidates(query: str, *, top_k: int = 5) -> list[ProductCandidate]:
    """Find likely product codes from free text using alias match + rapidfuzz."""

    if top_k <= 0:
        return []

    normalized_query = _normalize_product_text(query)
    if not normalized_query:
        return []

    candidates: dict[str, ProductCandidate] = {}

    # Deterministic direct and containment matches first.
    for alias, normalized_alias, code in _PRODUCT_ALIAS_ENTRIES:
        if normalized_alias and normalized_alias in normalized_query:
            _upsert_candidate(candidates, product_code=code, matched_alias=alias, score=100.0)

    # Explicit alpha token extraction, e.g. HC10 -> HC.
    for token in re.findall(r"[A-Z]{1,6}", normalized_query):
        if token in _KNOWN_PRODUCT_CODES:
            _upsert_candidate(candidates, product_code=token, matched_alias=token, score=100.0)

    # Fuzzy matching is always enabled.
    fuzzy_query = re.sub(r"[0-9]+", " ", unicodedata.normalize("NFKC", query)).strip().upper()
    if fuzzy_query:
        limit = min(len(_PRODUCT_ALIAS_TO_CODE), max(top_k * 5, 10))
        for alias, score, _ in rapidfuzz_process.extract(
            fuzzy_query,
            list(_PRODUCT_ALIAS_TO_CODE.keys()),
            scorer=rapidfuzz_fuzz.WRatio,
            limit=limit,
        ):
            if score < 60:
                continue
            code = _PRODUCT_ALIAS_TO_CODE[alias]
            _upsert_candidate(
                candidates,
                product_code=code,
                matched_alias=alias,
                score=float(score),
            )

    ranked = sorted(candidates.values(), key=lambda item: (-item.score, item.product_code))
    return ranked[:top_k]


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


def _uses_ymm_contract_suffix(product: str) -> bool:
    return PRODUCT_SUFFIX_FORMAT.get(product) == "YMM"


def _resolve_contract_year(
    month: int, *, current_date: date, contract_year: int | None
) -> int | None:
    if contract_year is not None:
        if contract_year < 0:
            return None
        if contract_year >= 100:
            return contract_year % 100
        if contract_year < 10:
            # Treat single-digit year as the year-ones digit in the nearest
            # not-yet-expired contract year.
            current_yy = current_date.year % 100
            candidate = (current_yy // 10) * 10 + contract_year
            if candidate < current_yy or (candidate == current_yy and month < current_date.month):
                candidate += 10
            return candidate
        return contract_year

    year = current_date.year % 100
    if month < current_date.month:
        year = (year + 1) % 100
    return year


def build_contract_code_from_parts(
    *,
    current_date: date,
    product_code: str | None = None,
    contract_month: int | None = None,
    contract_year: int | None = None,
) -> str | None:
    if not product_code or contract_month is None:
        return None

    product = product_code.strip().upper()
    if not re.fullmatch(r"[A-Z]{1,6}", product):
        return None
    if not (1 <= contract_month <= 12):
        return None

    year = _resolve_contract_year(
        contract_month, current_date=current_date, contract_year=contract_year
    )
    if year is None:
        return None

    if _uses_ymm_contract_suffix(product):
        return f"{product}{year % 10}{contract_month:02d}"

    return f"{product}{year:02d}{contract_month:02d}"


def normalize_contract_code(value: str | None, *, current_date: date) -> str | None:
    if not value:
        return None

    compact = re.sub(r"\s+", "", value)
    return infer_contract_code(compact, current_date=current_date)


def _infer_year_for_month_day(current: date, month: int, day: int) -> int:
    # If the target MM-DD is earlier than today, default to next year.
    if (month, day) < (current.month, current.day):
        return current.year + 1
    return current.year


def _parse_expire_date_str(s: str, *, current_date: date) -> date | None:
    s = s.strip()

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

    # Absolute date with explicit year. Use dateutil for flexible separators/forms.
    if re.search(r"[0-9]{4}", s):
        try:
            parsed = cast(
                datetime, dateutil_parser.parse(s, yearfirst=True, dayfirst=False, fuzzy=False)
            )
            return parsed.date()
        except (ValueError, OverflowError):
            return None

    return None


def infer_expire_date(
    *,
    current_date: date,
    explicit_expire_date: str | None = None,
    expire_in_months: float | None = None,
    expire_in_natural_days: int | None = None,
    expire_in_trading_days: int | None = None,
) -> str | None:
    """Resolve expire_date from explicit absolute or relative inputs."""

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

    return None


def normalize_quote(
    *,
    current_date: date,
    call_put: Literal[1, 2] | None = None,
    buy_sell: Literal[1, -1] | None = None,
    strike: float | None = None,
    strike_offset: float | None = None,
    underlying_price: float | None = None,
    expire_date: str | None = None,
    expire_in_months: float | None = None,
    expire_in_natural_days: int | None = None,
    expire_in_trading_days: int | None = None,
    product_code: str | None = None,
    contract_month: int | None = None,
    contract_year: int | None = None,
    quantity: float | None = None,
) -> InquiryQuote:
    # Contract code from explicit split fields only.
    contract = build_contract_code_from_parts(
        current_date=current_date,
        product_code=product_code,
        contract_month=contract_month,
        contract_year=contract_year,
    )

    # Call/put from explicit args only.
    cp = call_put

    # Direction from explicit args only.
    bs = buy_sell

    # Strike precedence: strike > strike_offset.
    s = strike
    so = strike_offset
    if s is not None:
        so = None

    # Expiry from explicit args only (absolute date and relative durations).
    exp = infer_expire_date(
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
        quantity=quantity,
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
def get_product_candidates(
    ctx: RunContextWrapper[InquiryContext],
    query: Annotated[
        str,
        Field(
            description="Raw RFQ text or product phrase. Example: '热卷05合约' or 'rb10'.",
            min_length=1,
        ),
    ],
    top_k: Annotated[
        int, Field(description="Maximum number of candidates to return.", ge=1, le=10)
    ] = 5,
) -> list[ProductCandidate]:
    """Return product-code candidates resolved from aliases and fuzzy matching."""

    _ = ctx
    return find_product_candidates(query, top_k=top_k)


@function_tool
def price_vanilla_option(
    ctx: RunContextWrapper[InquiryContext],
    call_put: Literal[1, 2] | None = None,
    buy_sell: Literal[1, -1] | None = None,
    strike: float | None = None,
    strike_offset: float | None = None,
    underlying_price: float | None = None,
    expire_date: str | None = None,
    expire_in_months: float | None = None,
    expire_in_natural_days: int | None = None,
    expire_in_trading_days: int | None = None,
    product_code: Annotated[
        str | None,
        Field(description="Contract product code only, letters only, e.g. HC."),
    ] = None,
    contract_month: Annotated[
        int | None,
        Field(description="Contract month as integer 1-12.", ge=1, le=12),
    ] = None,
    contract_year: Annotated[
        int | None,
        Field(description="Optional contract year as YYYY or YY (e.g. 2026 or 26).", ge=0),
    ] = None,
    quantity: Annotated[
        float | None,
        Field(
            description="Requested trade quantity. Prefer normalized tons when unit is explicit.",
            gt=0,
        ),
    ] = None,
) -> InquiryQuote:
    """Tool entrypoint for vanilla option RFQ pricing parameter normalization."""

    return normalize_quote(
        current_date=ctx.context.current_date,
        call_put=call_put,
        buy_sell=buy_sell,
        strike=strike,
        strike_offset=strike_offset,
        underlying_price=underlying_price,
        expire_date=expire_date,
        expire_in_months=expire_in_months,
        expire_in_natural_days=expire_in_natural_days,
        expire_in_trading_days=expire_in_trading_days,
        product_code=product_code,
        contract_month=contract_month,
        contract_year=contract_year,
        quantity=quantity,
    )
