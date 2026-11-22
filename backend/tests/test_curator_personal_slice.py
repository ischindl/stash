"""A personal curator pass is handed one priced part, and commits exactly what it read.

The failure these tests hold shut was measured on the founder stack on 2026-11-21: the
nightly personal tick was handed its ENTIRE unread delta (4,396,654 events for one lane),
spent its 570s wall reading, raised `RunCeilingExceeded` and committed nothing — while its
own wiki writer had already edited pages from that partial read, so the wiki moved and the
watermark did not, and the next night read the same characters again.

Every test here is about the agreement between the read and the commit. A change that only
shortens the feed, or only moves the commit, fails one of them.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from httpx import AsyncClient

from backend.services import agent_service, curation_service
from backend.services.curation_service import Position

from .conftest import unique_name

pytestmark = pytest.mark.asyncio


async def _owner(client: AsyncClient) -> UUID:
    r = await client.post(
        "/api/v1/users/register",
        json={"name": unique_name("slice"), "password": "securepassword1"},
    )
    return UUID(r.json()["id"])


async def _feed(pool, owner: UUID, sizes: list[int]) -> list[datetime]:
    """One event per size, a minute apart, all ahead of any watermark a test holds."""
    start = datetime.now(UTC) - timedelta(days=2)
    stamps = []
    for index, size in enumerate(sizes):
        at = start + timedelta(minutes=index)
        stamps.append(at)
        await pool.execute(
            "INSERT INTO history_events (owner_user_id, created_by, agent_name, event_type,"
            " content, session_id, created_at) VALUES ($1, $1, 'cli', 'user_message', $2, $3, $4)",
            owner,
            "x" * size,
            f"sess-slice-{index}",
            at,
        )
    return stamps


@pytest.mark.asyncio
async def test_the_window_ends_at_the_last_instant_the_wall_buys(client, _db_pool):
    """The edge is an event's own instant, and the characters are that prefix's own size.

    The budget buys two of the three events, so the window closes on the second one's
    instant: `until_bound` reads `<= until`, which is what lets the whole timestamp group
    at the seam belong to this pass instead of being shown and not committed.
    """
    owner = await _owner(client)
    stamps = await _feed(_db_pool, owner, [100, 100, 100])
    before = stamps[0] - timedelta(minutes=1)
    now = datetime.now(UTC) + timedelta(hours=1)

    window = await curation_service.read_window(
        owner, Position(before), "internal", None, 250, now, agent_id=owner
    )

    assert window.until == stamps[1]
    assert window.characters == 200


@pytest.mark.asyncio
async def test_the_commit_lands_on_the_edge_the_turn_was_shown(client, _db_pool):
    """The boundary a run earns IS the edge it was shown — never one event further.

    This is the whole defect in one assertion: the feed and the commit used to be cut
    apart, and a commit ahead of the read claims characters no model ever saw.
    """
    owner = await _owner(client)
    stamps = await _feed(_db_pool, owner, [100, 100, 100])
    before = stamps[0] - timedelta(minutes=1)
    now = datetime.now(UTC) + timedelta(hours=1)
    window = await curation_service.read_window(
        owner, Position(before), "internal", None, 250, now, agent_id=owner
    )

    through = await curation_service.complete_through(
        owner, Position(before), window.until, "internal", None, max_event_chars=250
    )

    assert through == Position(at=stamps[1])


@pytest.mark.asyncio
async def test_a_feed_the_budget_covers_is_still_read_whole(client, _db_pool):
    """Nothing was here before a backlog outgrew the wall, and that shape survives.

    A lane whose unread delta fits closes at the tick's own `now` — the unchanged run that
    reads everything and commits through the tick. Without this the fix could quietly
    window every run, including the ones that were never broken.
    """
    owner = await _owner(client)
    stamps = await _feed(_db_pool, owner, [100, 100])
    before = stamps[0] - timedelta(minutes=1)
    now = datetime.now(UTC) + timedelta(hours=1)

    window = await curation_service.read_window(
        owner, Position(before), "internal", None, 10_000, now, agent_id=owner
    )

    assert window.until == now
    assert window.characters == 200


async def _feed_groups(pool, owner: UUID, groups: list[list[int]], start: datetime | None = None):
    """One timestamp group per inner list: every size in it shares that group's instant.

    A budget walks whole instants, so the tests that argue about a forfeit need rows that
    TIE — `_feed` only ever makes one event per instant. Rows inside a group get distinct
    sessions, because the feed collapses an identity of (session, type, instant, content)
    and two identical rows at one instant would be ONE event, silently halving the group a
    test is measuring.

    Returns the feed's own rows in the feed's own order, so a test asserts on the event ids
    the pointers are made of rather than on stamps it invented.
    """
    first = start or datetime.now(UTC) - timedelta(days=2)
    for group, sizes in enumerate(groups):
        at = first + timedelta(minutes=group)
        for index, size in enumerate(sizes):
            await pool.execute(
                "INSERT INTO history_events (owner_user_id, created_by, agent_name, event_type,"
                " content, session_id, created_at) VALUES ($1, $1, 'cli', 'user_message', $2, $3, $4)",
                owner,
                "x" * size,
                f"sess-group-{group}-{index}",
                at,
            )
    return await pool.fetch(
        "SELECT id AS event_id, created_at, length(content) AS chars FROM history_events"
        " WHERE owner_user_id = $1 AND session_id LIKE 'sess-group-%'"
        " ORDER BY created_at, id",
        owner,
    )


@pytest.mark.asyncio
async def test_an_instant_bigger_than_the_grant_is_forfeited_and_the_pass_moves_past_it(
    client, _db_pool, caplog
):
    """A front no pass can carry is written off ONCE, named, and the pass still runs.

    This is the invariant STAS-497 inverted: #1272 refused to refuse an oversized first
    group, because a drain that refused one would re-read the same characters forever. The
    plateau cannot come back through this door — the commit stands on `kept_through`, the
    last event actually handed, which is at a LATER instant than the forfeited one, so what
    was skipped is behind the watermark and outside every future feed. The intake's own
    founder numbers are the reason the inversion was needed: the largest same-instant group
    ahead of that watermark is 390,825 characters, about 35 hours of read at the priced
    0.326s a character, inside a read share of 270 seconds.

    The budget here is 250 characters and the front instant is 600 of them, so the pass must
    be handed the 200 characters behind it, the watermark must land on that group's last
    event, and the 600 characters must be said out loud exactly once — `complete_through`
    stands on the same decision over the same rows and therefore prints nothing itself.
    """
    import logging

    owner = await _owner(client)
    rows = await _feed_groups(_db_pool, owner, [[600], [100, 100], [100]])
    front, first_kept, last_kept, _unread = rows[0], rows[1], rows[2], rows[3]
    before = front["created_at"] - timedelta(minutes=1)
    now = datetime.now(UTC) + timedelta(hours=1)

    with caplog.at_level(logging.INFO, logger="backend.services.curation_service"):
        window = await curation_service.read_window(
            owner, Position(before), "internal", None, 250, now, agent_id=owner
        )
        through = await curation_service.complete_through(
            owner, Position(before), window.until, "internal", None, max_event_chars=250
        )

    assert window.since == Position(at=front["created_at"], event_id=front["event_id"]), (
        "the read must START behind the forfeited instant, or the model re-reads it"
    )
    assert window.until == first_kept["created_at"]
    assert (window.characters, window.events, window.skipped_chars) == (200, 2, 600)
    assert through == Position(at=last_kept["created_at"], event_id=last_kept["event_id"])
    assert through.at > window.since.at, "the forfeited instant must end up BEHIND the watermark"

    forfeits = [
        r.getMessage() for r in caplog.records if "forfeits unread instant" in r.getMessage()
    ]
    assert len(forfeits) == 1, forfeits
    assert front["created_at"].isoformat() in forfeits[0]
    assert "600 characters" in forfeits[0]


@pytest.mark.asyncio
async def test_a_feed_that_can_only_lose_twice_hands_out_nothing_and_forfeits_nothing(
    client, _db_pool, caplog
):
    """One forfeit per pass is the cap, and the second overflow is a refusal, not a skip.

    Two oversized instants in a row is the shape that would otherwise let a nightly run write
    off its whole backlog one group at a time. The answer is `no part`: no window, no
    forfeited characters, and a watermark that does not move. It is a loud state and it stays
    loud every night until a human decides whether unread material may be written off — the
    per-pass pointer cannot express it and a stored one is a retention decision (STAS-497
    puts that question to the operator instead of answering it in code).
    """
    import logging

    owner = await _owner(client)
    rows = await _feed_groups(_db_pool, owner, [[600], [600]])
    before = rows[0]["created_at"] - timedelta(minutes=1)
    now = datetime.now(UTC) + timedelta(hours=1)

    with caplog.at_level(logging.INFO, logger="backend.services.curation_service"):
        window = await curation_service.read_window(
            owner, Position(before), "internal", None, 250, now, agent_id=owner
        )
        through = await curation_service.complete_through(
            owner, Position(before), now, "internal", None, max_event_chars=250
        )

    assert window is None
    assert through == Position(before), "a pass handed nothing records nothing"

    lines = [r.getMessage() for r in caplog.records]
    refusals = [m for m in lines if "no passable part" in m]
    assert len(refusals) == 1, refusals
    assert not [m for m in lines if "forfeits unread" in m], (
        "a refusal forfeits nothing; the two sentences must not be interchangeable"
    )


@pytest.mark.asyncio
async def test_only_the_leading_instant_is_forfeited_and_a_second_overflow_only_stops(
    client, _db_pool, caplog
):
    """A skip is a rule about the FRONT of a feed, not about every group too big to carry.

    The walk stops at the first instant the budget cannot buy, even though a smaller instant
    sits behind it: skipping a SECOND oversized group would stand the commit past material
    that was never shown, which is the silent-unread path this card exists to close. The
    budget is 250 characters, the front instant forfeits 600 of them, one 100-character
    instant is kept, and the next 600-character instant ends the pass - the 100 characters
    behind it stay ahead of the watermark for a later pass to read.
    """
    import logging

    owner = await _owner(client)
    rows = await _feed_groups(_db_pool, owner, [[600], [100], [600], [100]])
    front, kept, second_overflow, _unread = rows[0], rows[1], rows[2], rows[3]
    before = front["created_at"] - timedelta(minutes=1)
    now = datetime.now(UTC) + timedelta(hours=1)

    with caplog.at_level(logging.INFO, logger="backend.services.curation_service"):
        window = await curation_service.read_window(
            owner, Position(before), "internal", None, 250, now, agent_id=owner
        )
        through = await curation_service.complete_through(
            owner, Position(before), window.until, "internal", None, max_event_chars=250
        )

    assert (window.characters, window.events, window.skipped_chars) == (100, 1, 600), (
        "a second oversized group must stop the walk, not be skipped"
    )
    assert window.since == Position(at=front["created_at"], event_id=front["event_id"])
    assert window.until == kept["created_at"]
    assert through == Position(at=kept["created_at"], event_id=kept["event_id"])
    assert through.at < second_overflow["created_at"], (
        "the commit stood past an instant the model was never handed"
    )
    forfeits = [
        r.getMessage() for r in caplog.records if "forfeits unread instant" in r.getMessage()
    ]
    assert len(forfeits) == 1, forfeits


@pytest.mark.asyncio
async def test_the_prompts_a_pass_is_handed_read_from_the_window_edge_and_not_the_watermark(
    client, _db_pool
):
    """A skip the prompt never names is a group the model reads again and dies on.

    The pointer is worthless unless it reaches the words: `curator_changes_cmd` is the command
    the model actually runs, in BOTH phases of a personal run, so each must open at the pass'
    `read_from` — the forfeited instant's last row — while the ROW's `curated_through` stays
    where it was. This is the same argument STAS-491 made about `--until`, on the other edge
    of the window.
    """
    from backend.services import sprite_agent_service

    owner = await _owner(client)
    curator = await agent_service.get_or_create_curator(owner)
    rows = await _feed_groups(_db_pool, owner, [[600], [100, 100]])
    front = rows[0]
    before = front["created_at"] - timedelta(minutes=1)
    await _db_pool.execute(
        "UPDATE agents SET curated_through = $2 WHERE id = $1", curator["id"], before
    )
    row = await agent_service.get_agent_by_id(UUID(curator["id"]))
    assert curation_service.position_of(row) == Position(before)

    window = await curation_service.read_window(
        owner,
        curation_service.position_of(row),
        "internal",
        None,
        250,
        datetime.now(UTC) + timedelta(hours=1),
        agent_id=row["id"],
    )
    _session, prompt = await sprite_agent_service.build_digest_turn(
        row, "20260101000000", until=window.until.isoformat(), since=window.since
    )

    assert f"--since {front['created_at'].isoformat()}" in prompt
    assert f"--since-event {front['event_id']}" in prompt
    assert f"--since {before.isoformat()}" not in prompt, (
        "the command still ordered the row's watermark: the forfeited group comes back"
    )
    assert f"--until {window.until.isoformat()}" in prompt

    # The writer turn runs the same command when it was handed no digest report, so the pass'
    # opening edge has to reach it too — otherwise phase two is told to read what phase one
    # was just written off for having read.
    _session, writer_prompt = await sprite_agent_service.build_scheduled_turn(
        row, "20260101000000", until=window.until.isoformat(), since=window.since
    )
    assert f"--since {front['created_at'].isoformat()}" in writer_prompt
    assert f"--since-event {front['event_id']}" in writer_prompt
    assert f"--since {before.isoformat()}" not in writer_prompt


@pytest.mark.asyncio
async def test_a_forfeited_front_still_leaves_the_feed_and_the_commit_on_one_event(
    client, _db_pool
):
    """Read and commit still cut the same way when the cut began with a skip.

    The read/commit agreement STAS-491 built is what makes a forfeit safe rather than
    reckless, so it has to survive the new leading edge: with one budget and one set of rows,
    the last event `/changes` shows IS the event `complete_through` stands on, and the
    forfeiture is printed once — by the feed, before the commit, as a real pass does.
    """
    owner = await _owner(client)
    rows = await _feed_groups(_db_pool, owner, [[600], [100, 100], [100]])
    last_kept = rows[2]
    start = Position(rows[0]["created_at"] - timedelta(minutes=1))

    feed = await curation_service.changes_since(
        owner, owner, start, "internal", max_event_chars=250
    )
    through = await curation_service.complete_through(
        owner, start, rows[-1]["created_at"], "internal", max_event_chars=250
    )

    assert [event["content"] for event in feed["history"]] == ["x" * 100, "x" * 100]
    assert feed["history_has_more"] is True
    assert feed["history"][-1]["created_at"] == curation_service._iso(through.at)
    assert through == Position(at=last_kept["created_at"], event_id=last_kept["event_id"])


@pytest.mark.asyncio
async def test_the_write_phase_is_paid_before_the_read_is_priced(client, _db_pool, monkeypatch):
    """One wall, two model turns: the digest is priced on what the writer leaves behind.

    570s is the founder lane's measured wall, 0.10s a character a priced route, 270s the
    write phase's measured share (the worst of four `saneca` runs) and the commit's own
    reserve comes off too. The drain keeps the whole wall because its parts checkpoint one
    by one — a personal run gets no such chance.
    """
    from backend.services import curator_calibration

    owner = await _owner(client)
    curator = await agent_service.get_or_create_curator(owner)
    row = await agent_service.get_agent_by_id(UUID(curator["id"]))

    async def priced(_key):
        return 0.10, "test"

    monkeypatch.setattr(curator_calibration, "price", priced)
    monkeypatch.setattr(agent_service, "run_wall_seconds", lambda *_a: 570)
    reserve = agent_service.RUN_COMMIT_RESERVE_SECONDS

    grant = await curation_service.personal_budget_of(row)
    drain_grant = await curation_service.drain_budget_of(row)

    assert grant == min(max(int((570 - 270 - reserve) / 0.10), 384), 8192)
    assert drain_grant == min(max(int((570 - reserve) / 0.10), 1024), 8192)


@pytest.mark.asyncio
async def test_the_nightly_tick_hands_the_turn_its_window_and_books_it(
    client, _db_pool, sprite_exec, monkeypatch, caplog
):
    """The beat's own tick is the run that cures a founder lane, so it is the one pinned.

    Four events of 1000 characters, a wall that pays for two of them: the turn must be
    told where to stop, the watermark must stop exactly there, the lane must report `ran`
    rather than the nightly `failed` it has reported since 2026-09-01, and the meter must
    say once how much was read.
    """
    import logging

    from backend.services import curator_calibration, sprite_agent_service
    from backend.tasks.agent_schedules import _run_due, _run_scheduled_agent, run_scheduled_agent

    owner = await _owner(client)
    curator = await agent_service.get_or_create_curator(owner)
    curator_id = UUID(curator["id"])
    stamps = await _feed(_db_pool, owner, [1000] * 4)
    await _db_pool.execute(
        "UPDATE agents SET schedule_cron = '* * * * *',"
        " last_run_at = now() - interval '5 minutes', curated_through = $2 WHERE id = $1",
        curator_id,
        stamps[0] - timedelta(minutes=1),
    )

    async def priced(_key):
        return 0.10, "test"

    seen: list[dict] = []

    async def fake_turn(*_args, **kwargs):
        seen.append(kwargs)
        return "curated"

    monkeypatch.setattr(curator_calibration, "price", priced)
    monkeypatch.setattr(agent_service, "run_wall_seconds", lambda *_a: 570)
    monkeypatch.setattr(sprite_agent_service, "run_scheduled", fake_turn)
    monkeypatch.setattr(run_scheduled_agent, "delay", lambda *_a, **_k: dispatched.append(_a))
    dispatched: list[tuple] = []

    with caplog.at_level(logging.INFO, logger="backend.tasks.agent_schedules"):
        assert await _run_due() == 1
        await _run_scheduled_agent(dispatched[0][0], dispatched[0][1])

    row = await _db_pool.fetchrow(
        "SELECT curated_through, last_run_outcome FROM agents WHERE id = $1", curator_id
    )
    assert seen[0]["until"] == stamps[1].isoformat()
    # The window's OPENING edge travels on the same call: no instant was forfeited here, so
    # it names the row's watermark — and a pass that forfeited one would name its own
    # `read_from` instead, which is the only way the skip ever reaches the model (STAS-497).
    assert seen[0]["since"].at == stamps[0] - timedelta(minutes=1)
    assert row["curated_through"] == stamps[1]
    assert row["last_run_outcome"] == "ran"
    meters = [r.message for r in caplog.records if "characters=" in r.message]
    assert len(meters) == 1
    assert "characters=2000" in meters[0] and "budget=2700" in meters[0]


@pytest.mark.asyncio
async def test_a_wall_that_cannot_buy_the_smallest_part_runs_as_it_always_did(
    client, _db_pool, monkeypatch, caplog
):
    """Pricing does not invent a part a wall cannot pay, and does not crash on one either.

    The tiny-wall lane's problem is its ceiling, and its honest report is the cut the wall
    already raises — not a `ValueError` from the pricing helper, and not a slice the run
    cannot finish. The refusal is said out loud, because a lane that can never catch up is
    the thing an operator has to be able to find.
    """
    import logging

    from backend.services import curator_calibration

    owner = await _owner(client)
    curator = await agent_service.get_or_create_curator(owner)
    row = await agent_service.get_agent_by_id(UUID(curator["id"]))

    async def priced(_key):
        return 0.32646, "shipped-constant"

    monkeypatch.setattr(curator_calibration, "price", priced)
    monkeypatch.setattr(agent_service, "run_wall_seconds", lambda *_a: 20)

    with caplog.at_level(logging.INFO, logger="backend.services.curation_service"):
        assert await curation_service.personal_budget_of(row) is None
        assert await curation_service.personal_window(row, datetime.now(UTC)) is None

    refusals = [r.message for r in caplog.records if "no priced part" in r.message]
    assert len(refusals) == 2
    assert "smallest pass needs" in refusals[0]


async def _tick_once(client, _db_pool, monkeypatch, turn):
    """Run the beat's own tick over four events with a wall that pays for two of them.

    Shared by the two calibration tests, which differ ONLY in what the turn does at the
    end: finish, or get cut. Everything else about the pass must be identical, or the
    two rows would not be comparable.
    """
    from backend.services import curator_calibration, sprite_agent_service
    from backend.tasks import agent_schedules
    from backend.tasks.agent_schedules import _run_due, _run_scheduled_agent, run_scheduled_agent

    owner = await _owner(client)
    curator = await agent_service.get_or_create_curator(owner)
    curator_id = UUID(curator["id"])
    stamps = await _feed(_db_pool, owner, [1000] * 4)
    await _db_pool.execute(
        "UPDATE agents SET schedule_cron = '* * * * *',"
        " last_run_at = now() - interval '5 minutes', curated_through = $2 WHERE id = $1",
        curator_id,
        stamps[0] - timedelta(minutes=1),
    )

    async def priced(_key):
        return 0.10, "test"

    seen: list[dict] = []

    async def fake_turn(*_args, **kwargs):
        seen.append(kwargs)
        return await turn(kwargs)

    async def no_catchup(*_args, **_kwargs):
        return None

    dispatched: list[tuple] = []

    monkeypatch.setattr(curator_calibration, "price", priced)
    monkeypatch.setattr(agent_service, "run_wall_seconds", lambda *_a: 570)
    monkeypatch.setattr(sprite_agent_service, "run_scheduled", fake_turn)
    monkeypatch.setattr(agent_schedules, "_dispatch_curator_catchup", no_catchup)
    monkeypatch.setattr(run_scheduled_agent, "delay", lambda *a, **_k: dispatched.append(a))

    assert await _run_due() == 1
    await _run_scheduled_agent(dispatched[0][0], dispatched[0][1])
    key = curator_calibration.key_from_configuration(
        await agent_service.get_agent_by_id(curator_id)
    )
    return curator_id, key, seen, stamps


async def _samples(pool, key):
    return await pool.fetch(
        "SELECT characters, seconds FROM curator_pass_samples WHERE calibration_key = $1"
        " ORDER BY id",
        key,
    )


async def _dispatch_once(client, pool, monkeypatch, turn, sizes: list[int], catchup=None):
    """Drive the dispatcher's own manual run over `sizes` events with a wall paying 2 700.

    The sibling of `_tick_once` for `_run_curator_now`, the path a user triggers: same
    priced route, same stubbed turn, same catch-up stub, but the other body - the one
    whose failure branch re-raises and therefore shields its sample write. A turn that
    returned instantly would measure zero seconds, and `observe` refuses a zero-second
    row, so the fake sleeps.
    """
    from backend.services import curator_calibration, sprite_agent_service
    from backend.tasks import agent_schedules

    owner = await _owner(client)
    curator = await agent_service.get_or_create_curator(owner)
    curator_id = UUID(curator["id"])
    stamps = await _feed(pool, owner, sizes)
    # A quiet night still needs a watermark to move: the run reads from here whether or
    # not anything sits ahead of it.
    await pool.execute(
        "UPDATE agents SET curated_through = $2 WHERE id = $1",
        curator_id,
        (stamps[0] if stamps else datetime.now(UTC) - timedelta(days=3)) - timedelta(minutes=1),
    )

    async def priced(_key):
        return 0.10, "test"

    async def sleeping_turn(*_args, **_kwargs):
        await asyncio.sleep(0.05)
        return await turn()

    async def no_catchup(*_args, **_kwargs):
        return None

    monkeypatch.setattr(curator_calibration, "price", priced)
    monkeypatch.setattr(agent_service, "run_wall_seconds", lambda *_a: 570)
    monkeypatch.setattr(sprite_agent_service, "run_scheduled", sleeping_turn)
    # A caller that wants to see the moment the NEXT pass would be priced passes its own
    # stub; the ordering of the cut's sample write against that moment is a claim.
    monkeypatch.setattr(
        agent_schedules,
        "_dispatch_curator_catchup",
        no_catchup if catchup is None else catchup,
    )

    await agent_schedules._run_curator_now(curator_id, metered=False)
    key = curator_calibration.key_from_configuration(
        await agent_service.get_agent_by_id(curator_id)
    )
    return curator_id, key, stamps


@pytest.mark.asyncio
async def test_a_committed_personal_pass_teaches_the_price_it_paid(
    client, _db_pool, sprite_exec, monkeypatch
):
    """A finished personal pass files its OWN characters, so the next grant is measured.

    Before this the call existed only on the scoped lane, `curator_pass_samples` held
    zero rows on the founder stack, and `personal_budget_of` answered from the seed for
    every personal run ever run - a price no pass could correct. The row must carry what
    the pass read (2 000), never the grant it was cut from (2 700): a meter that files
    the grant would teach the route that 2 700 characters cost what 2 000 of them did.
    """
    from backend.services.curator_calibration import price as read_price

    async def finish(_kwargs):
        return "curated"

    _curator_id, key, _seen, _stamps = await _tick_once(client, _db_pool, monkeypatch, finish)

    rows = await _samples(_db_pool, key)
    assert len(rows) == 1
    assert rows[0]["characters"] == 2000
    assert rows[0]["seconds"] > 0
    rate, source = await read_price(key)
    assert source == "measured", "the pass taught the route, and a later grant reads it"
    assert rate == rows[0]["seconds"] / 2000


@pytest.mark.asyncio
async def test_a_personal_pass_the_wall_cuts_teaches_a_lower_bound_on_the_grant(
    client, _db_pool, sprite_exec, monkeypatch
):
    """A run the wall cut committed nothing but still knows its grant, and files it.

    The cut is this lane's dominant outcome — the founder's lane ended on its ceiling on
    nearly every night of the past three weeks — so a sample written only on success
    would teach the price from the lane's rarest pass and hand the next tick the same
    grant that just died. The row carries the GRANT (2 700) over the wall spent, and such
    a pass read at most its grant in those seconds, so that ratio is a LOWER bound on what
    a character cost: `price` answers the maximum of its window, so a bound can be ignored
    outright and can never raise a price. What it does is stop a cut night from teaching
    that the run cost nothing.
    """
    from backend.services import sprite_agent_service

    async def cut(_kwargs):
        # `by_user=False`: the wall's own cut is the one outcome a filed sample is honest
        # about, and this test is the witness that it still files.
        raise sprite_agent_service.RunCeilingExceeded(
            "Digest turn exceeded its wall of 570s", by_user=False
        )

    curator_id, key, _seen, _stamps = await _tick_once(client, _db_pool, monkeypatch, cut)

    rows = await _samples(_db_pool, key)
    assert len(rows) == 1
    assert rows[0]["characters"] == 2700, "the grant it was handed, not what it managed to read"
    row = await _db_pool.fetchrow(
        "SELECT curated_through, last_run_outcome FROM agents WHERE id = $1", curator_id
    )
    assert row["last_run_outcome"] == "failed"
    assert row["curated_through"] == _stamps[0] - timedelta(minutes=1), "a cut run commits nothing"
    assert len(rows) == await _db_pool.fetchval(
        "SELECT count(*) FROM curator_pass_samples WHERE calibration_key = $1", key
    ), "a cut run teaches once, and only on the grant"


@pytest.mark.asyncio
async def test_every_model_phase_of_a_personal_run_reports_its_own_cost(
    client, _db_pool, sprite_exec, monkeypatch, caplog
):
    """One line per phase, naming its seconds and what it was handed, from the turn itself.

    The measurement this card had to make (what does a digest cost before it reads a
    character?) was reconstructed by hand out of `history_events` rows, and the stored
    shape actively misleads for it: a run's `session_end` row is flushed when the WHOLE
    turn ends, so both sessions of one run carry their last row at the same instant and a
    span computed from them prices the digest at the entire wall. Reading the row count of
    the phase meter line is the one grep that answers the question, so this test holds
    that the line exists, once per phase, and carries the numbers.

    `_turn_bracket`'s filter is asserted alongside: the phase lines are new neighbours of
    the dispatcher's bracket and must not join it.
    """
    import logging
    import re

    from backend.services import curator_calibration
    from backend.tasks import agent_schedules
    from backend.tasks.agent_schedules import _run_curator_now
    from backend.tasks.session_titles import generate_session_title

    from .test_curator import _turn_bracket

    owner = await _owner(client)
    curator = await agent_service.get_or_create_curator(owner)
    curator_id = UUID(curator["id"])
    stamps = await _feed(_db_pool, owner, [1000] * 4)
    await _db_pool.execute(
        "UPDATE agents SET curated_through = $2, digest_provider = 'anthropic',"
        " system_prompt = 'You maintain the wiki.' WHERE id = $1",
        curator_id,
        stamps[0] - timedelta(minutes=1),
    )

    async def priced(_key):
        return 0.10, "test"

    async def no_catchup(*_args, **_kwargs):
        return None

    monkeypatch.setattr(curator_calibration, "price", priced)
    monkeypatch.setattr(agent_service, "run_wall_seconds", lambda *_a: 570)
    monkeypatch.setattr(agent_schedules, "_dispatch_curator_catchup", no_catchup)
    monkeypatch.setattr(generate_session_title, "delay", lambda *a, **_k: None)

    with caplog.at_level(logging.INFO):
        await _run_curator_now(curator_id, metered=False)

    lines = [r.getMessage() for r in caplog.records]
    digest = [m for m in lines if "curation phase=digest" in m]
    write = [m for m in lines if "curation phase=write" in m]
    assert len(digest) == 1, digest
    assert len(write) == 1, write

    # A positive cost and a non-empty prompt, per phase; the digest also names the feed
    # characters its window handed it, which is the pair every lever argument is made from.
    for line in (*digest, *write):
        assert re.search(r"seconds=\d+\.\d prompt_chars=[1-9]\d* persona_chars=[1-9]\d*", line)
    assert re.search(r"feed_chars=2000\b", digest[0]), digest[0]
    assert re.search(r"feed_chars=[1-9]\d*\b", write[0]), write[0]

    # The window decision is said where it is made: priced, and priced from one call.
    windows = [m for m in lines if "personal window agent=" in m]
    assert len(windows) == 1, windows
    assert re.search(
        r"rate=0\.10000 source=test wall=570s priced_share=300\.0s grant=2700 "
        r"characters=2000 events=2 skipped_chars=0 since=\S+ until=",
        windows[0],
    ), windows[0]

    bracket = _turn_bracket(caplog.records, str(curator_id))
    assert len(bracket) == 2, bracket


@pytest.mark.asyncio
async def test_a_turn_a_human_stopped_teaches_the_price_nothing(
    client, _db_pool, sprite_exec, monkeypatch
):
    """A stopped turn is not a wall cut, and the loop must not price a run it never finished.

    The row this gate refuses would have said: a 2 700-character grant over the five
    seconds a human took to hit Stop is 0,0011 seconds per character, and `price` takes
    the MAX of its window, so that one row would price the lane at a fortieth of its seed
    and hand the next pass the 8 192-character ceiling. A stopped turn spent its seconds
    on a feed it abandoned, so its rate measures nothing; STAS-495 owns the same class of
    fabricated-cheap sample on the scoped lane.

    The gate reads the exception's own `by_user` rather than an elapsed-time threshold:
    which wall fired is the fact `_wall_deadline` exists to hand back, and a threshold
    here would re-implement the wall's law inside a caller.
    """
    from backend.services import sprite_agent_service

    async def stopped(_kwargs):
        raise sprite_agent_service.RunCeilingExceeded(
            "Digest turn was stopped before finishing the window it was reading (570s wall)",
            by_user=True,
        )

    curator_id, key, _seen, stamps = await _tick_once(client, _db_pool, monkeypatch, stopped)

    assert await _samples(_db_pool, key) == [], "a stop files nothing"
    row = await _db_pool.fetchrow(
        "SELECT curated_through, last_run_outcome FROM agents WHERE id = $1", curator_id
    )
    assert row["last_run_outcome"] == "failed", "refusing the sample does not soften the report"
    assert row["curated_through"] == stamps[0] - timedelta(minutes=1)


@pytest.mark.asyncio
async def test_the_manual_dispatch_files_the_row_its_own_two_lines_print(
    client, _db_pool, sprite_exec, monkeypatch, caplog
):
    """On the manual path one reading of the clock feeds both the log line and the row.

    The dispatcher is the body that prints the committed meter AND the bracket's `wall=`,
    so it is the one place a reader can check a row against the worker log. Both numbers
    are parsed out of the captured records rather than restated, because the claim is that
    they cannot disagree - a second copy of either figure in this test would prove
    nothing. The landed calibration tests drove the beat's tick only, so this row was
    wired without a witness.
    """
    import logging
    import re

    async def finish():
        return "curated"

    with caplog.at_level(logging.INFO, logger="backend.tasks.agent_schedules"):
        _curator_id, key, _stamps = await _dispatch_once(
            client, _db_pool, monkeypatch, finish, [1000] * 4
        )

    meters = [
        r.message
        for r in caplog.records
        if "personal curator" in r.message and "characters=" in r.message
    ]
    brackets = [
        r.message for r in caplog.records if "turn finished lane=personal wall=" in r.message
    ]
    assert len(meters) == 1 and len(brackets) == 1, "one committed pass, one bracket"

    rows = await _samples(_db_pool, key)
    assert len(rows) == 1
    assert rows[0]["characters"] == int(re.search(r"characters=(\d+)", meters[0]).group(1)), (
        "the row's characters ARE the meter's"
    )
    # The bracket line prints its wall through `%.1f`, so the printed text is the rounded
    # twin of the row's float. Compared at the printing's own precision -- the one the two
    # consumers share -- rather than against a half-open bound that could flake on an
    # exact half-tenth of a second.
    printed_wall = re.search(r"wall=([0-9.]+)s", brackets[0]).group(1)
    assert f"{rows[0]['seconds']:.1f}" == printed_wall, "one reading, printed twice"


@pytest.mark.asyncio
async def test_the_manual_cut_files_its_bound_before_the_next_pass_is_priced(
    client, _db_pool, sprite_exec, monkeypatch
):
    """The dispatcher's cut branch files its bound, and files it before the catch-up runs.

    This is the branch that re-raises, which is why its write is shielded: an awaited
    write on a task the wall is abandoning would never reach the pool, and a row landing
    AFTER the catch-up dispatch has priced the next pass prices nothing. The beat's tick
    has its own witness above; this one drives `_run_curator_now`, the other body.

    The catch-up stub is the probe rather than an assertion about source order: it reads
    the table at the exact moment the next pass's pricing begins, so the ordering is
    measured. The characters are the GRANT, because a cut personal run committed nothing.
    """
    from backend.services import curator_calibration, sprite_agent_service

    rows_when_priced: list[int] = []
    run_agent: list[UUID] = []

    async def cut():
        raise sprite_agent_service.RunCeilingExceeded(
            "Curator curation exceeded its run time limit of 570s and left the feed unread",
            by_user=False,
        )

    async def peek(agent, *_args, **_kwargs):
        # The stub is handed the very agent dict the run owns, which is the only way to
        # name this run afterwards: `_owner` registers a fresh user per call, so a lookup
        # made here would build a second curator and read its empty row.
        run_agent.append(UUID(agent["id"]))
        rows_when_priced.append(
            await _db_pool.fetchval("SELECT count(*) FROM curator_pass_samples")
        )

    with pytest.raises(sprite_agent_service.RunCeilingExceeded):
        await _dispatch_once(client, _db_pool, monkeypatch, cut, [1000], catchup=peek)

    assert rows_when_priced == [1], "the bound is durable before the next pass is priced"

    agent = await agent_service.get_agent_by_id(run_agent[0])
    key = curator_calibration.key_from_configuration(agent)
    rows = await _samples(_db_pool, key)
    assert [r["characters"] for r in rows] == [2700], "the grant it was handed, not a read"
    outcome = await _db_pool.fetchval(
        "SELECT last_run_outcome FROM agents WHERE id = $1", run_agent[0]
    )
    assert outcome == "failed", "the cut still reports itself failed while teaching once"


@pytest.mark.asyncio
async def test_a_personal_pass_that_read_nothing_leaves_the_calibration_untaught(
    client, _db_pool, sprite_exec, monkeypatch, caplog
):
    """A quiet night commits, prints `characters=0`, and teaches the price nothing.

    `_budget_kept` answers None for an empty feed and `read_window` sums nothing, so the
    window's characters are 0 while the run still succeeds. Without the landed guard this
    shape raises `ValueError` out of `observe` INSIDE the run's own `try`, and a night with
    nothing to curate would resolve `failed` and send an alert.

    The reachable shape is the manual dispatch: the beat's tick refuses a curator with no
    pending input earlier, at `_pending_input` and `mark_run_skipped("no_changes")`, so a
    quiet night never reaches the sampler through `_tick_once` and this test does not
    pretend it did.
    """
    import logging

    async def finish():
        return "curated"

    with caplog.at_level(logging.INFO, logger="backend.tasks.agent_schedules"):
        curator_id, key, _stamps = await _dispatch_once(client, _db_pool, monkeypatch, finish, [])

    assert await _samples(_db_pool, key) == [], "nothing was read, so nothing was learned"
    untaught = [r.message for r in caplog.records if "left the calibration untaught" in r.message]
    assert len(untaught) == 1
    row = await _db_pool.fetchrow(
        "SELECT curated_through, last_run_outcome FROM agents WHERE id = $1", curator_id
    )
    assert row["last_run_outcome"] == "ran", "an untaught night is still a successful one"
    assert row["curated_through"] is not None
