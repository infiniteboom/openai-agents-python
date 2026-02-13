from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import examples.env_setup  # noqa: F401, E402
from examples.financial_inquiry_parser.normalize import find_product_candidates  # noqa: E402

DEFAULT_QUERIES = [
    "热卷05合约，我方卖一个月平值期权",
    "rb10 看涨",
    "豆一2609 做一个认购",
    "想做郑棉CF",
]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Test find_product_candidates with sample RFQ text."
    )
    parser.add_argument(
        "query",
        nargs="*",
        help="RFQ text to test. If omitted, built-in sample queries will be used.",
    )
    parser.add_argument("--top-k", type=int, default=5, help="Max candidates per query.")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON.")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    queries = [" ".join(args.query).strip()] if args.query else DEFAULT_QUERIES
    top_k = max(1, args.top_k)

    for idx, query in enumerate(queries, start=1):
        results = find_product_candidates(query, top_k=top_k)
        payload = [item.model_dump() for item in results]

        print(f"[{idx}] query: {query}")
        if args.pretty:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(json.dumps(payload, ensure_ascii=False))
        print()


if __name__ == "__main__":
    main()
