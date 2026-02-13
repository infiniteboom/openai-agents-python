from __future__ import annotations

import argparse
import asyncio
import json
from datetime import date

import examples.env_setup  # noqa: F401
from agents import (
    Agent,
    FunctionToolResult,
    ModelSettings,
    RunContextWrapper,
    Runner,
    ToolsToFinalOutputResult,
)
from examples.financial_inquiry_parser.normalize import (
    InquiryContext,
    get_product_candidates,
    price_vanilla_option,
)
from examples.financial_inquiry_parser.schema import InquiryQuote

INSTRUCTIONS = """You are an expert derivatives trader focused on vanilla option RFQs.

Call tools with this policy:
1. Split the RFQ into one or more independent option legs.
2. For each leg, call `price_vanilla_option` exactly once.
3. Call `get_product_candidates` when `product_code` is uncertain.
4. If there are multiple legs, complete all legs in the same tool-calling turn.

General rules:
- Fill only supported tool arguments.
- Use high-confidence extraction only; if unsure, leave fields null.
- Do not ask follow-up questions.
- If you used `get_product_candidates`, `product_code` must come from its candidates.

Field rules:
- `product_code`: letters only, uppercase if possible (e.g. HC, RB, OI).
- `contract_month`: integer 1-12.
- `contract_year`: optional, use when explicitly available (YYYY, YY, or single-digit year for YMM-style products if clearly implied).
- `buy_sell`: client buy=1, client sell=-1.
- `call_put`: call=1, put=2.
- `strike` vs `strike_offset`: mutually exclusive; prefer `strike` if an absolute strike is explicitly given.
- `strike_offset`: ATM=0, ITM positive, OTM negative.
- `underlying_price`: fill only when an explicit reference price is provided.
- `quantity`: optional requested quantity; (e.g. `3万吨` -> `30000`).

Contract parsing:
- Parse combined tokens like `hc10`, `热卷05`, `rb2405`, `OI605` into `product_code` + month (+ optional year).
- Chinese mapping: `热卷` -> `HC`, `螺纹`/`螺纹钢` -> `RB`.
- If the user provides code directly, use it.

Expiry fields:
- Fill at most one of: `expire_date`, `expire_in_months`, `expire_in_trading_days`, `expire_in_natural_days`.
- If no clear expiry is provided, leave all null.
- For explicit date, keep the user's clear date expression (e.g. `9月15日` or `2026-09-15`).
"""


def _collect_pricing_results(
    context: RunContextWrapper[InquiryContext],
    results: list[FunctionToolResult],
) -> ToolsToFinalOutputResult:
    _ = context
    priced = [result.output for result in results if result.tool.name == "price_vanilla_option"]
    if not priced:
        return ToolsToFinalOutputResult(is_final_output=False, final_output=None)
    if len(priced) == 1:
        return ToolsToFinalOutputResult(is_final_output=True, final_output=priced[0])
    return ToolsToFinalOutputResult(is_final_output=True, final_output=priced)


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
        tool_use_behavior=_collect_pricing_results,
        model_settings=ModelSettings(tool_choice="required"),
    )

    result = await Runner.run(agent, args.text, context=context)
    quote = result.final_output
    if isinstance(quote, InquiryQuote):
        print(quote.model_dump_json(indent=2))
    elif isinstance(quote, list):
        payload = [item.model_dump() if isinstance(item, InquiryQuote) else item for item in quote]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        # Fallback: tooling might return a plain dict depending on serialization settings.
        print(quote)


if __name__ == "__main__":
    asyncio.run(main())
