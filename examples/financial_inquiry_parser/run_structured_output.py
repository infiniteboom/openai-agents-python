from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import date

from agents import Agent, RunContextWrapper, Runner

from examples.financial_inquiry_parser.schema import InquiryQuote


@dataclass
class AppContext:
    current_date: date


def instructions(ctx: RunContextWrapper[AppContext], _agent: Agent[AppContext]) -> str:
    today = ctx.context.current_date.isoformat()
    return f"""You are a financial RFQ parser. Today is {today}.

Convert the user's message into a single JSON object that matches the output schema exactly.

Rules (must follow):
- No follow-up questions. If a field is missing or ambiguous, set it to null.
- contract_code: uppercase product + YYMM (e.g. HC2610). If user says only month (e.g. hc10), infer year:
  - if month >= current month: use current year; else use next year.
- call_put: 1=Call (看涨/认购/Call), 2=Put (看跌/认沽/Put).
- buy_sell is customer direction: 1=customer buys, -1=customer sells.
  - '我方卖/我们卖/卖给你/offer' => customer buys (1)
  - '我方买/我们买/从你买/bid' => customer sells (-1)
- strike vs strike_offset: if both appear, prefer strike (set strike_offset to null).
- strike_offset sign: 实值(ITM) positive, 虚值(OTM) negative, 平值(ATM)=0.
- expire_date precedence: absolute date > relative duration.
  - Absolute date examples: 2026-04-15, 2026/4/15, 4月15日 (infer year; if MM-DD is earlier than today, use next year)
  - Relative duration examples: 1个月, 20天(自然日), 20个交易日(跳过周末;节假日先不考虑)
"""


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--current-date", type=str, default=date.today().isoformat())
    parser.add_argument("text", type=str, help="RFQ text, e.g. 'hc10合约，我方卖一个月平值期权'")
    args = parser.parse_args()

    current_date = date.fromisoformat(args.current_date)
    context = AppContext(current_date=current_date)

    agent = Agent[AppContext](
        name="RFQStructuredOutputAgent",
        instructions=instructions,
        output_type=InquiryQuote,
    )

    result = await Runner.run(agent, args.text, context=context)
    quote: InquiryQuote = result.final_output
    print(quote.model_dump_json(indent=2))


if __name__ == "__main__":
    asyncio.run(main())
