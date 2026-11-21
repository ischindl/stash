"""What a curator's input character costs, and what one pass may be handed because of it.

The grant a curator pass receives is its run wall divided by this rate, so these tests
judge the arithmetic that decides how much feed a lane drains in a day. They are separate
from `test_scoped_curation.py` because the rate now has its own owner, its own table, and
one property the run tests cannot see: the grant feeds the number that sizes it.
"""

from datetime import UTC, datetime

import pytest

from backend.config import settings
from backend.services import curator_calibration
from backend.services import scoped_curation_inputs as inputs
from backend.services import scoped_curation_service as curation


async def _samples(pool, key: str, samples: list[tuple[int, float]]) -> None:
    for characters, seconds in samples:
        await curator_calibration.observe(key, characters, seconds)


def test_an_unmeasured_route_answers_its_seed():
    """A route class nobody has priced yet still gets priced, by the shipped seed."""
    assert curator_calibration.seed_for("local:internal") == 0.12
    assert curator_calibration.seed_for("anthropic:external") == 0.32646


def test_a_key_that_was_never_priced_raises_naming_itself():
    """A new route family or lane must be priced by a person, not defaulted at runtime.

    A silent default is the exact defect this module replaces: one number serving every
    box on earth, read by no measurement.
    """
    with pytest.raises(ValueError, match="local:mystery") as refused:
        curator_calibration.seed_for("local:mystery")
    assert "SEED_SECONDS_PER_CHARACTER" in str(refused.value)


@pytest.mark.asyncio
async def test_the_oldest_price_is_the_window_not_the_mean(pool):
    """Twelve samples answer their slowest, which is STAS-458's rule: price the arm that
    binds, not the average of arms."""
    key = "local:internal"
    await _samples(pool, key, [(4_000, 480.0), (4_000, 200.0), (4_000, 400.0)])
    priced, source = await curator_calibration.price(key)
    assert source == "measured"
    assert priced == pytest.approx(480.0 / 4_000)


@pytest.mark.asyncio
async def test_a_thirteenth_sample_ends_the_oldest_ones_effect(pool):
    """The window is what makes the pessimistic price correctable in both directions.

    A slow pass cannot be averaged away, but it does age out: after twelve cheaper
    passes the lane is granted what its box can actually carry. A maximum without a
    window is a ratchet that only winds one way, and a fast box would stay granted as
    though it were the slowest machine on the network.
    """
    key = "local:internal"
    slow = (1_000, 900.0)
    cheap = (4_000, 200.0)
    await _samples(pool, key, [slow])
    await _samples(pool, key, [cheap] * (curator_calibration.SAMPLE_WINDOW - 1))
    at_twelve, _source = await curator_calibration.price(key)
    assert at_twelve == pytest.approx(slow[1] / slow[0])

    await _samples(pool, key, [cheap])
    at_thirteen, _source = await curator_calibration.price(key)
    assert at_thirteen == pytest.approx(cheap[1] / cheap[0])


def test_both_ways_of_naming_the_lane_agree(monkeypatch):
    """`key_for` reads the route that answers; `key_from_configuration` reads settings.

    Only the second is available where a budget is decided without a network knock --
    `drain_budget_of` runs on the feed read path. The two therefore mirror
    `require_route`'s branch, and this is what keeps that mirror from becoming a second
    source of truth: both families, both lanes, the same key.
    """
    for hosted in (True, False):
        monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "sk-ant-test-key" if hosted else None)
        for wiki in ("internal", "external"):
            agent = {"curator_wiki": wiki}
            route = (
                curation.llm.anthropic_route()
                if hosted
                else curation.llm.local_route("http://box:8000/v1", "qwen", None)
            )
            assert curator_calibration.key_for(route, agent) == (
                curator_calibration.key_from_configuration(agent)
            ), f"{('anthropic' if hosted else 'local')}:{wiki} answers two ways"


def test_the_founder_wall_buys_four_thousand_five_hundred_characters():
    """The case this card exists for, at the founder's own numbers.

    A 570 s run wall priced at the `local:internal` seed grants 4,500 characters. Before
    this change the same wall granted 1,455, because the price was one constant pair
    measured once on one box -- and 1,455 characters is what a lane carrying 4,669,736
    unread documents drained per pass for five days.
    """
    rate = curator_calibration.seed_for("local:internal")
    assert curation.input_budget(570, rate) == 4_500


def test_a_cheap_measured_route_cannot_grant_an_unbounded_pass():
    """The grant feeds the price: a sample is `seconds / characters` and every pass pays
    a fixed cost of model turns, so a bigger grant measures a cheaper character, which
    grants more. A declared ceiling is the rung that stops the ladder.
    """
    bounded = curation.input_budget(570, 0.0367)
    assert bounded == curation.MAX_PASS_INPUT_CHARS
    assert bounded * 0.0367 <= 570
    assert curation.input_budget(570, 0.0001) == curation.MAX_PASS_INPUT_CHARS
    # A wall that cannot buy the ceiling is still granted by the wall, not by the cap.
    assert curation.input_budget(570, 0.12) < curation.MAX_PASS_INPUT_CHARS


def test_a_wall_that_cannot_pay_for_a_commit_refuses_naming_the_rate():
    with pytest.raises(ValueError, match="cannot buy the cheapest pass") as refused:
        curation.input_budget(100, 0.12)
    message = str(refused.value)
    assert "0.12000s per character" in message
    assert "1024 characters" in message


@pytest.mark.asyncio
async def test_a_budget_below_the_smallest_document_names_both_numbers():
    """A caller that hands the loader a budget smaller than one slice gets an error, not
    an empty batch -- an empty batch is read as "nothing pending" and puts a lane to
    sleep, and as "raise the run ceiling" by a run that never reached the model.

    The guard is the function's first statement, so nothing here touches a database.
    """
    from uuid import UUID

    with pytest.raises(ValueError, match="cannot start a document") as refused:
        await inputs.load_batch(
            UUID(int=0),
            None,
            datetime.now(UTC),
            internal=True,
            generation=1,
            max_chars=256,
        )
    message = str(refused.value)
    assert "256" in message
    assert str(inputs.MINIMUM_DOCUMENT_CHARS) in message


def test_the_fill_floor_is_the_envelope_of_an_empty_event():
    """512 is not taste: it is the widest JSON envelope a schema-maximal `event_type`
    and `tool_name` render with no content inside. A slice below that bound
    binary-searches to zero characters, the offset never moves, and the cursor cannot
    advance -- which is the failure `run` reports as "cannot advance the curation cursor".
    """
    row = {
        "kind": "event",
        "event_type": "t" * 64,
        "tool_name": "u" * 128,
        "stamp": datetime(2026, 11, 20, 12, 0, tzinfo=UTC),
        "characters": 0,
    }
    empty_envelope = len(inputs.render_event(row, "", 0))
    assert empty_envelope < inputs.MINIMUM_DOCUMENT_CHARS
    assert inputs.MINIMUM_BATCH_CHARS >= inputs.MINIMUM_DOCUMENT_CHARS


@pytest.mark.asyncio
async def test_a_cut_pass_records_a_bound_that_shrinks_the_next_grant(pool):
    """A pass the wall cut committed nothing, so its only durable teaching is a lower
    bound: `wall / grant` over a pass that died is never cheaper than the truth.

    The bound's whole purpose is that the next run is granted *less*. A sample that
    could not lower a grant is how the founder lane died at the same wall every pass for
    five days, because nothing it learned was ever written down.
    """
    key = "local:internal"
    rate = curator_calibration.seed_for(key)
    granted = curation.input_budget(570, rate)
    assert granted == 4_500

    await curator_calibration.observe(key, characters=granted, seconds=570.0)

    corrected, source = await curator_calibration.price(key)
    assert source == "measured"
    assert corrected == pytest.approx(570 / 4_500)
    assert curation.input_budget(570, corrected) < granted


@pytest.mark.asyncio
async def test_a_sample_without_characters_is_refused(pool):
    with pytest.raises(ValueError, match="needs characters"):
        await curator_calibration.observe("local:internal", characters=0, seconds=12.0)
