from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Any

from hz_connector import AsyncHZConnector


def _strip_wrapping_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = _strip_wrapping_quotes(value.strip())
        if key and key not in os.environ:
            os.environ[key] = value


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch {varietyCode: varietyName} map from HZ connector."
    )
    parser.add_argument("--username", default=os.getenv("HZ_USERNAME"), help="HZ username.")
    parser.add_argument("--password", default=os.getenv("HZ_PASSWORD"), help="HZ password.")
    parser.add_argument("--address", default=os.getenv("HZ_ADDRESS"), help="HZ base address.")
    parser.add_argument(
        "--max-connections",
        type=int,
        default=50,
        help="Max aiohttp connections.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=30,
        help="HTTP timeout seconds.",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optional output file path. If empty, only print to stdout.",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON output.",
    )
    return parser.parse_args()


def _required(value: str | None, name: str) -> str:
    if value:
        return value
    raise SystemExit(f"Missing required value: {name}")


async def _run(args: argparse.Namespace) -> dict[str, str]:
    username = _required(args.username, "username (or env HZ_USERNAME)")
    password = _required(args.password, "password (or env HZ_PASSWORD)")
    address = _required(args.address, "address (or env HZ_ADDRESS)")

    async with AsyncHZConnector(
        username=username,
        password=password,
        address=address,
        max_connections=args.max_connections,
        timeout_seconds=args.timeout_seconds,
    ) as connector:
        return await connector.get_variety_code_variety_name_map_async()


def _dump_json(data: dict[str, str], *, pretty: bool) -> str:
    options: dict[str, Any] = {"ensure_ascii": False, "sort_keys": True}
    if pretty:
        options["indent"] = 2
    return json.dumps(data, **options)


def main() -> None:
    _load_dotenv(Path(".env"))
    args = _parse_args()
    result = asyncio.run(_run(args))
    output = _dump_json(result, pretty=args.pretty)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output)
            f.write("\n")

    print(output)


if __name__ == "__main__":
    main()
