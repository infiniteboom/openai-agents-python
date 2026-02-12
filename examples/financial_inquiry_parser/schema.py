from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class InquiryQuote(BaseModel):
    """Normalized RFQ payload for downstream pricing systems.

    This is intentionally strict (no extra keys) so downstream integrations can rely on
    a stable shape. Missing/ambiguous values must be represented as null.
    """

    model_config = ConfigDict(extra="forbid")

    contract_code: str | None = Field(
        default=None,
        description="Uppercase product code + YYMM, e.g. HC2610.",
        pattern=r"^[A-Z]+[0-9]{4}$",
    )
    call_put: Literal[1, 2] | None = Field(
        default=None, description="1=Call (看涨/认购), 2=Put (看跌/认沽)."
    )
    buy_sell: Literal[1, -1] | None = Field(
        default=None, description="Customer direction: 1=buy, -1=sell."
    )
    strike: float | None = Field(default=None, gt=0, description="Absolute strike price.")
    strike_offset: float | None = Field(
        default=None, description="Relative moneyness offset: ITM positive, OTM negative."
    )
    underlying_price: float | None = Field(
        default=None, description="Optional underlying/entry reference price."
    )
    expire_date: str | None = Field(
        default=None,
        description="Expiration date (YYYY-MM-DD).",
        pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$",
    )
