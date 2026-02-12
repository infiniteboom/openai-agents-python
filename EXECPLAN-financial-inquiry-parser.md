# Financial Inquiry Parsing Agent (JSON Output + Tool-Based Prototype)

This ExecPlan is a living document. The sections Progress, Surprises & Discoveries, Decision Log, and Outcomes & Retrospective must stay up to date as work proceeds.

This plan follows `PLANS.md`.

## Purpose / Big Picture

Add a runnable example that turns messy option RFQ text into a strict, downstream-friendly JSON object (per `plan.md`) without any human-in-the-loop. Provide two experimentally comparable paths:

1) Structured output (model directly produces the JSON schema).
2) Tool-first (model fills tool parameters; tool deterministically emits the JSON).

Also add unit tests for the deterministic normalization logic (dates, contract code inference, direction keyword mapping).

## Progress

- [x] (2026-02-12) Align spec with constraints: no follow-up questions; conflict precedence; weekend-only trading calendar; add missing synonym rules.
- [x] (2026-02-12) Implement deterministic normalizer + date utilities under `examples/financial_inquiry_parser/`.
- [x] (2026-02-12) Add two runnable example scripts (structured-output vs tool-based) under `examples/financial_inquiry_parser/`.
- [x] (2026-02-12) Add unit tests for deterministic utilities under `tests/`.
- [ ] (2026-02-12) Run `$code-change-verification` equivalent: `make format`, `make lint`, `make mypy`, `make tests` (blocked: `uv` is not installed in the current environment).

## Surprises & Discoveries

- Observation: The environment does not have `uv`, `pytest`, `ruff`, or `pydantic` installed, so the standard repo verification commands cannot run yet.
  Evidence: `make format` fails with `make: uv: No such file or directory`.

## Decision Log

- Decision: Support both “model emits JSON” and “tool emits JSON” approaches as parallel prototypes.
  Rationale: User explicitly wants to experiment; tool-based path provides stronger output determinism.
  Date/Author: 2026-02-12 / Codex
- Decision: First version of trading-day math skips weekends only (no holiday calendar).
  Rationale: User requirement for v1.
  Date/Author: 2026-02-12 / Codex
- Decision: Conflicts resolve as `absolute expire_date > relative expiry`, and `strike > strike_offset`.
  Rationale: User requirement; deterministic and testable.
  Date/Author: 2026-02-12 / Codex

## Outcomes & Retrospective

- Pending implementation.

## Context and Orientation

- Repository root: `/home/gaojianyuan/openai-agents-python`.
- Domain spec draft: `plan.md` (financial RFQ parsing to JSON).
- We must not rely on follow-up questions; missing/ambiguous fields must be set to `null` and handled by downstream post-processing.
- We will implement as an example (not core library behavior) under `examples/`.

## Plan of Work

We will:

1) Extend `plan.md` where needed to reflect “no follow-up questions” and conflict precedence (already partially done) and add synonym coverage for direction.
2) Add `examples/financial_inquiry_parser/` with:
   - `schema.py`: Pydantic model matching the strict output template.
   - `normalize.py`: Deterministic helpers for:
     - contract code inference (including year completion from current date),
     - buy/sell perspective mapping with common synonyms,
     - call/put keyword mapping,
     - expiry resolution (absolute date vs months vs natural days vs trading days),
     - strike vs strike_offset precedence and basic moneyness parsing (“平值/ATM/实/虚”).
   - `run_structured_output.py`: uses `Agent(output_type=...)` to have the model output the schema directly.
   - `run_tool_based.py`: forces a single tool call to a `normalize_inquiry` tool and uses its output as final output.
3) Add unit tests for the deterministic helpers (no OpenAI calls).

## Concrete Steps

Run from repo root:

    make format
    make lint
    make mypy
    make tests

## Validation and Acceptance

- `examples/financial_inquiry_parser/run_structured_output.py` runs (with an API key configured) and prints a JSON object with the exact keys from `plan.md`.
- `examples/financial_inquiry_parser/run_tool_based.py` runs (with an API key configured) and prints a JSON object with the exact keys from `plan.md`, computed deterministically from tool args + local date math.
- `pytest` covers:
  - contract code year completion,
  - weekend-only trading-day addition,
  - conflict precedence (`strike > strike_offset`, `absolute date > relative`),
  - direction synonym mapping (“我方买/我方卖/客户买/客户卖”等).

## Idempotence and Recovery

- All changes are additive (new example files + tests + small `plan.md` clarifications).
- If tests fail, revert only the new example/test files or adjust helpers; no existing runtime behavior should change.

## Artifacts and Notes

- `plan.md` remains the domain spec; this ExecPlan adds implementation guidance and acceptance checks.

## Interfaces and Dependencies

- Output schema (Pydantic BaseModel) must include exactly these keys:
  - `contract_code: str | null`
  - `call_put: int | null` (1=Call, 2=Put)
  - `buy_sell: int | null` (1=customer buys, -1=customer sells)
  - `strike: float | null`
  - `strike_offset: float | null`
  - `underlying_price: float | null`
  - `expire_date: str | null` (YYYY-MM-DD)
