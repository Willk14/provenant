"""A Claude tool loop with limits enforced in code, and a usage record that never contains content (ADR 0014)."""
import json
import logging
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

log = logging.getLogger(__name__)

MODEL = "claude-opus-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_RETRIES = 2
BASE_BACKOFF_SECONDS = 2.0

# US cents per million tokens: input, output, cache read, five-minute cache write. Listed prices at 2026-11-03.
PRICES_VERSION = "2026-11-03"
PRICES: Mapping[str, tuple[int, int, int, int]] = {
    "claude-opus-5-5": (400, 2000, 20, 500),
    "claude-sonnet-5-5": (200, 1000, 10, 250),
    "claude-opus-5": (500, 2500, 50, 625),
    "claude-opus-4-8": (500, 2500, 50, 625),
}
# Used only for the spending limit, so a model with no listed price cannot run unchecked.
UNLISTED_PRICE = (1000, 5000, 100, 1250)


class Refused(RuntimeError):
    """The model declined the request."""


class DeadlineExceeded(TimeoutError):
    """The run reached its wall-clock limit."""


class BudgetExceeded(RuntimeError):
    """The run reached its spending limit."""


@dataclass(frozen=True)
class Limits:
    deadline_seconds: float = 8 * 60  # below the worker's ten-minute stale-job limit
    idle_seconds: float = 60  # longest silence on a stream; deadline + idle stays under that limit
    max_turns: int = 120
    max_cost_microusd: int = 5_000_000  # USD 5
    max_output_tokens: int = 32_000


@dataclass
class RunRecord:
    """What a run used. The usage ledger stores only these counts. `trace` holds what the model was given and did;
    it is stored separately, in the firm's own append-only trace table (ADR 0018), never in the usage ledger."""

    purpose: str
    status: str = "failed"  # completed | failed | refused | deadline | budget
    stop_reason: str | None = None
    error_type: str | None = None
    models: list[str] = field(default_factory=list)
    turns: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_microusd: int | None = 0
    limit_cost_microusd: int = 0
    duration_ms: int = 0
    trace: list[tuple[str, str]] = field(default_factory=list, repr=False)

    def add(self, response: Any, requested_model: str) -> None:
        model = getattr(response, "model", None) or requested_model
        if model not in self.models:
            self.models.append(model)
        usage = getattr(response, "usage", None)
        counts = tuple(int(getattr(usage, name, 0) or 0) for name in (
            "input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
        self.turns += 1
        self.stop_reason = getattr(response, "stop_reason", None)
        self.input_tokens += counts[0]
        self.output_tokens += counts[1]
        self.cache_read_tokens += counts[2]
        self.cache_write_tokens += counts[3]
        cost = cost_microusd(model, *counts)
        self.limit_cost_microusd += cost if cost is not None else _priced(UNLISTED_PRICE, *counts)
        self.cost_microusd = None if cost is None or self.cost_microusd is None else self.cost_microusd + cost


def _priced(price: tuple[int, int, int, int], *counts: int) -> int:
    # One cent per million tokens is 0.01 micro-dollars per token.
    total = sum(Decimal(n) * cents for n, cents in zip(counts, price, strict=True)) / 100
    return int(total.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def cost_microusd(model: str, input_tokens: int, output_tokens: int, cache_read: int = 0,
                  cache_write: int = 0) -> int | None:
    """Estimated cost in integer micro-dollars, or None when the model has no listed price."""
    price = PRICES.get(model)
    return None if price is None else _priced(price, input_tokens, output_tokens, cache_read, cache_write)


_sink: ContextVar[list[RunRecord] | None] = ContextVar("provenant_ai_runs", default=None)


@contextmanager
def capture() -> Iterator[list[RunRecord]]:
    """Collect the runs made inside this block, so the caller can store them for the right tenant."""
    runs: list[RunRecord] = []
    token = _sink.set(runs)
    try:
        yield runs
    finally:
        _sink.reset(token)


def _emit(record: RunRecord) -> None:
    runs = _sink.get()
    if runs is not None:
        runs.append(record)


def _block(block: Any) -> str | None:
    kind = getattr(block, "type", None)
    if kind == "text":
        return f"text: {block.text}"
    if kind == "tool_use":
        return f"tool_call {block.name}: {json.dumps(block.input, ensure_ascii=False, default=str)}"
    if kind == "thinking":
        return "thinking: (not returned by the API)"
    return None


def record_call(purpose: str, requested_model: str, response: Any, duration_seconds: float,
                trace: list[tuple[str, str]] | None = None) -> None:
    """Record a single model call made outside the tool loop."""
    record = RunRecord(purpose=purpose, trace=list(trace or []))
    record.add(response, requested_model)
    record.status = "refused" if record.stop_reason == "refusal" else "completed"
    record.duration_ms = max(int(duration_seconds * 1000), 0)
    _emit(record)


def _retry_delay(exc: Exception, attempt: int) -> float | None:
    """Seconds to wait before retrying, or None when this failure should not be retried."""
    import anthropic

    if isinstance(exc, anthropic.APIConnectionError):
        return BASE_BACKOFF_SECONDS * 2 ** attempt
    if not isinstance(exc, anthropic.APIStatusError) or not (exc.status_code == 429 or exc.status_code >= 500):
        return None
    if exc.response.headers.get("x-should-retry") == "false":
        return None
    try:
        return max(float(exc.response.headers["retry-after"]), 0.0)
    except (KeyError, ValueError):
        return BASE_BACKOFF_SECONDS * 2 ** attempt


def _stream_turn(client: Any, params: Mapping[str, Any], limits: Limits, deadline: float,
                 clock: Callable[[], float]) -> Any:
    import httpx2  # the HTTP library the installed Anthropic SDK is built on

    bounded = client.with_options(max_retries=0, timeout=httpx2.Timeout(limits.idle_seconds, connect=10.0))
    with bounded.beta.messages.stream(**params) as stream:
        for _ in stream:
            if clock() > deadline:
                raise DeadlineExceeded("the run reached its time limit")
        return stream.get_final_message()


def _turn(client: Any, params: Mapping[str, Any], limits: Limits, deadline: float, clock: Callable[[], float],
          sleep: Callable[[float], None]) -> Any:
    attempt = 0
    while True:
        try:
            return _stream_turn(client, params, limits, deadline, clock)
        except DeadlineExceeded:
            raise
        except Exception as exc:
            delay = _retry_delay(exc, attempt) if attempt < MAX_RETRIES else None
            if delay is None or clock() + delay >= deadline:
                raise
            log.warning("retrying a Claude request after %s", type(exc).__name__)
            sleep(delay)
            attempt += 1


def run_tools(client: Any, *, purpose: str, system: str, prompt: str, tools: Sequence[Mapping[str, Any]],
              execute: Callable[[str, Mapping[str, Any]], str], done: Callable[[], bool], model: str = MODEL,
              limits: Limits | None = None, clock: Callable[[], float] = time.monotonic,
              sleep: Callable[[float], None] = time.sleep) -> RunRecord:
    """Run until `done()` is true. Raises if the model stops first or a limit is reached; the run is recorded either way."""
    limits = limits or Limits()
    record = RunRecord(purpose=purpose)
    started = clock()
    deadline = started + limits.deadline_seconds
    messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
    record.trace += [("system", system), ("prompt", prompt)]
    try:
        for _ in range(limits.max_turns):
            if clock() > deadline:
                raise DeadlineExceeded("the run reached its time limit")
            response = _turn(client, {
                "model": model,
                "max_tokens": limits.max_output_tokens,
                "betas": [FALLBACK_BETA],
                "fallbacks": "default",  # on a safety decline, the API retries on a fallback model
                "thinking": {"type": "adaptive"},
                "output_config": {"effort": "high"},
                "cache_control": {"type": "ephemeral"},
                "system": system,
                "tools": list(tools),
                "messages": messages,
            }, limits, deadline, clock, sleep)
            record.add(response, model)
            record.trace += [("response", line) for line in filter(None, map(_block, response.content))]
            if response.stop_reason == "refusal":
                raise Refused(f"model declined: {getattr(getattr(response, 'stop_details', None), 'category', None)}")
            if response.stop_reason != "tool_use":  # includes max_tokens: a cut-off tool call is never run
                raise RuntimeError(f"the agent stopped before finishing ({response.stop_reason})")
            uses = [block for block in response.content if block.type == "tool_use"]
            if not uses:
                raise RuntimeError("the agent asked to use a tool but named none")
            results = []
            for use in uses:
                record.tool_calls += 1
                output = execute(use.name, use.input)
                record.trace.append(("tool_result", f"{use.name}: {output}"))
                result = {"type": "tool_result", "tool_use_id": use.id, "content": output}
                results.append({**result, "is_error": True} if output.startswith("ERROR:") else result)
            if done():
                record.status = "completed"
                return record
            if record.limit_cost_microusd >= limits.max_cost_microusd:
                raise BudgetExceeded("the run reached its spending limit")
            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": results})
        raise RuntimeError(f"the agent used all {limits.max_turns} turns")
    except Exception as exc:
        record.error_type = type(exc).__name__[:64]
        record.status = ("refused" if isinstance(exc, Refused) else "deadline" if isinstance(exc, DeadlineExceeded)
                         else "budget" if isinstance(exc, BudgetExceeded) else "failed")
        raise
    finally:
        record.duration_ms = max(int((clock() - started) * 1000), 0)
        _emit(record)
