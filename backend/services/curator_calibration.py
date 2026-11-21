"""What one input character costs a curator, per route class, learned from real passes.

This rate is what sizes a curator's input slice: `scoped_curation_service.input_budget`
divides a run wall by it, so the number is not a statistic but a budget. It was one
shipped constant pair (95 s per 291 characters, STAS-458) that no measurement ever read
back, while the very log line quoting it reported `seconds_per_character=` between
0.0367 and 0.4198 on the same box. A price 3-9x too slow is a batch 3-9x too small,
which is how a founder's lane drained 43,160 characters in a day behind a backlog of
4,669,736 documents.

The key is `route_class:curator_wiki`, not the model id. A pass's wall is the sum of its
scopes' tool loops, and the scope set is a property of the lane (`internal` reads one
scope, `external` runs one loop per end user in the batch), while a new model id on the
same box must not become an unpriced key that hard-fails a running lane.

`price` answers the most expensive of the last `SAMPLE_WINDOW` samples rather than their
mean, which is STAS-458's rule -- price the slowest arm, not the average. The window is
what makes that correction two-directional: a slow pass cannot be forgotten by being
averaged away, but it does age out, so a box that got faster is granted more after
twelve cheaper passes rather than never.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..config import settings
from ..database import get_pool

if TYPE_CHECKING:
    from . import llm

SAMPLE_WINDOW = 12

# One number per key, each with the measurement it came from. An unknown key raises
# rather than defaulting: a silently unpriced route is the defect this module replaces.
SEED_SECONDS_PER_CHARACTER = {
    # STAS-458's sample on the hosted route: 95 s of model turn over 291 characters.
    "anthropic:internal": 0.32646,
    "anthropic:external": 0.32646,
    # The founder's vLLM box on 2026-11-20 completed passes at 0.0367-0.12
    # seconds per character; the pessimistic end of that band is priced, per the
    # rule above.
    "local:internal": 0.12,
    # An external pass runs one tool loop per end user whose input is in the batch
    # and has never been priced on its own, so it keeps the hosted figure rather
    # than inheriting a single-scope lane's measurement.
    "local:external": 0.32646,
}


def key_for(route: llm.Route, agent: dict) -> str:
    """The calibration key of a run, from the route that will answer it."""
    return f"{route.route_class}:{agent['curator_wiki']}"


def key_from_configuration(agent: dict) -> str:
    """The same key answered without a route, for a caller that must not knock.

    `scoped_curation_service.require_route` chooses the family by
    `settings.ANTHROPIC_API_KEY` alone and probes a local endpoint only to prove it
    answers. A budget question must not pay for that probe, so this mirrors the
    branch instead of reusing it, and `test_curator_pass_calibration` pins the two
    answers equal for both families and both lanes -- the one test that keeps the
    mirror from becoming two sources of truth.
    """
    family = "anthropic" if settings.ANTHROPIC_API_KEY else "local"
    return f"{family}:{agent['curator_wiki']}"


def seed_for(key: str) -> float:
    if key not in SEED_SECONDS_PER_CHARACTER:
        raise ValueError(
            f"no seed price for curator route {key!r}: a new route class or curator "
            "lane must be priced in SEED_SECONDS_PER_CHARACTER before it can run"
        )
    return SEED_SECONDS_PER_CHARACTER[key]


async def price(key: str) -> tuple[float, str]:
    """Seconds one character costs this route, and whether a pass or the seed said so."""
    seed = seed_for(key)
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT seconds_per_character FROM curator_pass_samples "
            "WHERE calibration_key=$1 ORDER BY observed_at DESC, id DESC LIMIT $2",
            key,
            SAMPLE_WINDOW,
        )
    if not rows:
        return seed, "seed"
    return max(float(row["seconds_per_character"]) for row in rows), "measured"


async def observe(key: str, characters: int, seconds: float) -> None:
    """Record what one pass cost, as one sample `price` may later read.

    Callers that did not finish the pass pass a lower bound instead of a measurement:
    `wall / grant` over a pass the wall cut is never cheaper than the truth, so the
    sample can only make the next grant smaller. Each caller says which it is where it
    calls, because the row itself carries only the arithmetic.
    """
    if characters <= 0:
        raise ValueError(f"a calibration sample needs characters, got {characters}")
    if seconds <= 0:
        raise ValueError(f"a calibration sample needs wall-seconds, got {seconds}")
    seed_for(key)
    async with get_pool().acquire() as conn:
        await conn.execute(
            "INSERT INTO curator_pass_samples "
            "(calibration_key, characters, seconds, seconds_per_character) "
            "VALUES ($1, $2, $3, $4)",
            key,
            characters,
            seconds,
            seconds / characters,
        )
