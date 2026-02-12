from __future__ import annotations

import argparse
import asyncio
from datetime import date

from agents import Agent, ModelSettings, Runner

from examples.financial_inquiry_parser.normalize import InquiryContext, normalize_inquiry
from examples.financial_inquiry_parser.schema import InquiryQuote


INSTRUCTIONS = """You are a financial RFQ parser.

Call the tool `normalize_inquiry` exactly once.
- Pass the user's raw message as `text`.
- Extract and fill any other tool arguments you can infer with high confidence.
- Do not ask follow-up questions. If unsure, omit the argument and let the tool return nulls.
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
        tools=[normalize_inquiry],
        tool_use_behavior="stop_on_first_tool",
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
