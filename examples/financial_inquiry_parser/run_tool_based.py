from __future__ import annotations

import argparse
import asyncio
from datetime import date

import examples.env_setup  # noqa: F401
from agents import Agent, ModelSettings, Runner
from examples.financial_inquiry_parser.normalize import (
    InquiryContext,
    get_product_candidates,
    price_vanilla_option,
)
from examples.financial_inquiry_parser.schema import InquiryQuote

INSTRUCTIONS = """You are an expert derivatives trader focused on vanilla option RFQs.

Call tools in this exact sequence:
1. Call `get_product_candidates` exactly once, with `query` set to the raw user RFQ text.
2. Then call `price_vanilla_option` exactly once.

General rules:
- Fill only supported tool arguments.
- Use high-confidence extraction only; if unsure, leave fields null.
- Do not ask follow-up questions.
- If you provide `product_code`, it must come from `get_product_candidates`.

Field rules:
- `product_code`: letters only, uppercase if possible (e.g. HC, RB, OI).
- `contract_month`: integer 1-12.
- `contract_year`: optional, use when explicitly available (YYYY, YY, or single-digit year for YMM-style products if clearly implied).
- `buy_sell`: client buy=1, client sell=-1.
- `call_put`: call=1, put=2.
- `strike` vs `strike_offset`: mutually exclusive; prefer `strike` if an absolute strike is explicitly given.
- `strike_offset`: ATM=0, ITM positive, OTM negative.
- `underlying_price`: fill only when an explicit reference price is provided.

Contract parsing:
- Parse combined tokens like `hc10`, `热卷05`, `rb2405`, `OI605` into `product_code` + month (+ optional year).
- Chinese mapping: `热卷` -> `HC`, `螺纹`/`螺纹钢` -> `RB`.
- If the user provides code directly, use it.

Expiry fields:
- Fill at most one of: `expire_date`, `expire_in_months`, `expire_in_trading_days`, `expire_in_natural_days`.
- If no clear expiry is provided, leave all null.
- For explicit date, keep the user's clear date expression (e.g. `9月15日` or `2026-09-15`).
"""


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--current-date", type=str, default=date.today().isoformat())
    parser.add_argument("text", type=str, help="RFQ text, e.g. 'hc10合约，我方卖一个月平值期权'")
    args = parser.parse_args()

    current_date = date.fromisoformat(args.current_date)
    context = InquiryContext(current_date=current_date)

    agent = Agent[InquiryContext](
        name="RFQToolBasedAgent",
        instructions=INSTRUCTIONS,
        tools=[get_product_candidates, price_vanilla_option],
        tool_use_behavior={"stop_at_tool_names": ["price_vanilla_option"]},
        model_settings=ModelSettings(tool_choice="required"),
    )

    result = await Runner.run(agent, args.text, context=context)
    quote = result.final_output
    if isinstance(quote, InquiryQuote):
        print(quote.model_dump_json(indent=2))
    else:
        # Fallback: tooling might return a plain dict depending on serialization settings.
        print(quote)


if __name__ == "__main__":
    asyncio.run(main())
