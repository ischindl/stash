"""`sessions.finished_at` is honest: it is only ever an explicit close signal.

Every session row on prod currently reads as in-progress because the only thing
that ever wrote the column was the demo router, while the plugin already sends a
named close event (`session_end`) through the ordinary ingestion endpoints — the
server just dropped it. Closing on "nothing else arrived for a while" is not the
fix: the transcript importer back-fills months of conversations in one batch, so
an idle rule or a last-event-as-close rule stamps a close on a session that is
merely imported, and a session with no close event at all must keep the column
NULL (the card's Rule 3). Ingestion may therefore close a session only when a
close-named event arrives, and the close travels forward-only: it can be
advanced by a later close, never rewound by a replayed one, and never moved by
ordinary activity that follows it.
"""

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from httpx import AsyncClient

from .conftest import unique_name

CLOSE = "session_end"

BACKEND = Path(__file__).resolve().parents[1]
_T0 = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


def _auth(api_key: str) -> dict:
    return {"Authorization": f"Bearer {api_key}"}


async def _register(client: AsyncClient) -> str:
    name = unique_name("close")
    resp = await client.post(
        "/api/v1/users/register",
        json={"name": name, "password": "securepassword1", "email": f"{name}@test.local"},
    )
    assert resp.status_code == 201
    return resp.json()["api_key"]


def _event(session_id: str, event_type: str, ts: datetime | None = None, **extra) -> dict:
    event = {
        "agent_name": "claude",
        "event_type": event_type,
        "content": f"{event_type} payload",
        "session_id": session_id,
        **extra,
    }
    if ts is not None:
        event["created_at"] = ts.isoformat()
    return event


async def _push_single(client: AsyncClient, key: str, event: dict) -> None:
    resp = await client.post("/api/v1/me/sessions/events", json=event, headers=_auth(key))
    assert resp.status_code == 201, resp.text


async def _push_batch(client: AsyncClient, key: str, events: list[dict]) -> None:
    resp = await client.post(
        "/api/v1/me/sessions/events/batch", json={"events": events}, headers=_auth(key)
    )
    assert resp.status_code == 201, resp.text


async def _close_fields(pool, session_id: str) -> tuple[datetime | None, datetime]:
    row = await pool.fetchrow(
        "SELECT finished_at, last_event_at FROM sessions WHERE session_id = $1", session_id
    )
    assert row is not None, "the ingestion path must have upserted the session row"
    return row["finished_at"], row["last_event_at"]


# --- AC1: no close signal, no close -------------------------------------------------------


@pytest.mark.asyncio
async def test_activity_without_a_close_keeps_the_session_open(client: AsyncClient, pool):
    """A live session that simply has no close event yet is NOT finished — this
    is the session shape the whole product is full of (1,287 of them on founder
    prod), and reading it as closed is the corruption this task exists to stop."""
    key = await _register(client)
    await _push_single(client, key, _event("open-single", "user_message", _T0))
    await _push_batch(
        client,
        key,
        [_event("open-single", "assistant_message", _T0 + timedelta(minutes=2))],
    )

    finished_at, last_event_at = await _close_fields(pool, "open-single")
    assert finished_at is None
    assert last_event_at == _T0 + timedelta(minutes=2)


@pytest.mark.asyncio
async def test_transcript_import_shaped_batch_keeps_the_session_open(client: AsyncClient, pool):
    """The batch the importer sends — months of back-dated turns, no close row —
    must leave the session open. This is the exact Rule 3 case, and the reason
    the backfill may not fall back to the last event when no close exists."""
    key = await _register(client)
    back_dated = [
        _event("imported-batch", "user_message", datetime(2026, 3, 1, tzinfo=UTC)),
        _event("imported-batch", "assistant_message", datetime(2026, 3, 1, 0, 5, tzinfo=UTC)),
    ]
    await _push_batch(client, key, back_dated)

    finished_at, last_event_at = await _close_fields(pool, "imported-batch")
    assert finished_at is None
    assert last_event_at == datetime(2026, 3, 1, 0, 5, tzinfo=UTC)


@pytest.mark.asyncio
async def test_real_transcript_upload_keeps_the_session_open(client: AsyncClient, pool):
    """Same guard on the real importer rather than a hand-rolled lookalike:
    `POST /me/transcripts` mints event-shaped rows with historical times, and
    importing someone's history says nothing about the session having ended."""
    import json

    key = await _register(client)
    body = "\n".join(
        json.dumps(
            {
                "type": kind,
                "message": {
                    "content": text if kind == "user" else [{"type": "text", "text": text}]
                },
                "timestamp": ts,
            }
        )
        for kind, text, ts in (
            ("user", "earlier question", "2026-02-02T08:00:00Z"),
            ("assistant", "earlier answer", "2026-02-02T08:00:30Z"),
        )
    ).encode()

    resp = await client.post(
        "/api/v1/me/transcripts",
        files={"file": ("history.jsonl", body, "application/x-ndjson")},
        data={"session_id": "uploaded-history", "agent_name": "claude"},
        headers=_auth(key),
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["imported"] == 2

    finished_at, last_event_at = await _close_fields(pool, "uploaded-history")
    assert finished_at is None
    assert last_event_at == datetime(2026, 2, 2, 8, 0, 30, tzinfo=UTC)


# --- AC2: the named close closes, on both ingestion endpoints ------------------------------


@pytest.mark.asyncio
async def test_close_event_on_the_single_endpoint_stamps_the_close_instant(
    client: AsyncClient, pool
):
    """`finished_at` is the close event's own instant — not arrival time, not the
    last event's time. `created_at` is the event's own time (both paths default to
    now only when the caller omits it), so the plugin's close is the evidence."""
    key = await _register(client)
    await _push_single(client, key, _event("closed-single", "user_message", _T0))
    await _push_single(client, key, _event("closed-single", CLOSE, _T0 + timedelta(minutes=9)))

    finished_at, last_event_at = await _close_fields(pool, "closed-single")
    assert finished_at == _T0 + timedelta(minutes=9)
    assert last_event_at == _T0 + timedelta(minutes=9)


@pytest.mark.asyncio
async def test_close_event_on_the_batch_endpoint_stamps_the_close_instant(
    client: AsyncClient, pool
):
    """The two endpoints share one ingestion path, so a close must close on both
    — the plugin streams its close as a single event, importers push batches."""
    key = await _register(client)
    await _push_batch(
        client,
        key,
        [
            _event("closed-batch", "user_message", _T0),
            _event("closed-batch", CLOSE, _T0 + timedelta(minutes=4)),
        ],
    )

    finished_at, _ = await _close_fields(pool, "closed-batch")
    assert finished_at == _T0 + timedelta(minutes=4)


@pytest.mark.asyncio
async def test_close_is_found_wherever_it_sits_in_the_batch(client: AsyncClient, pool):
    """A batch is not ordered by event time, so the close has to be derived from
    every event in it. Taking the last processed event instead misses a close
    that arrives before a trailing turn."""
    key = await _register(client)
    await _push_batch(
        client,
        key,
        [
            _event("close-first", CLOSE, _T0 + timedelta(minutes=1)),
            _event("close-first", "user_message", _T0 + timedelta(minutes=7)),
        ],
    )

    finished_at, last_event_at = await _close_fields(pool, "close-first")
    assert finished_at == _T0 + timedelta(minutes=1)
    assert last_event_at == _T0 + timedelta(minutes=7)


@pytest.mark.asyncio
async def test_the_plugin_shape_close_without_an_event_time_still_closes(client: AsyncClient, pool):
    """This is the close the product actually sends: `StashClient.push_event`
    (stashai/plugin/stash_client.py) has no per-event timestamp field at all, so
    every real `session_end` arrives undated. The close is therefore the instant
    the close row itself was stored at — the same basis `last_event_at` rests on,
    and the difference from the agent's own clock is the hook's own delivery lag.

    Reading the optional `created_at` field instead would leave the column NULL
    for every session in the product, which is the bug this task exists to kill;
    that is why this case is pinned on the plugin's real payload rather than a
    hand-stamped one."""
    key = await _register(client)
    await _push_single(client, key, _event("close-undated", "user_message", _T0))
    await _push_single(client, key, _event("close-undated", CLOSE))

    stored_close_at = await pool.fetchval(
        "SELECT created_at FROM history_events WHERE session_id = $1 AND event_type = $2",
        "close-undated",
        CLOSE,
    )
    finished_at, last_event_at = await _close_fields(pool, "close-undated")
    assert finished_at == stored_close_at
    assert finished_at >= datetime.now(UTC) - timedelta(seconds=30)
    assert last_event_at == stored_close_at


@pytest.mark.asyncio
async def test_a_close_that_arrives_first_still_closes(client: AsyncClient, pool):
    """A close can be the first event the server ever stores for a session id —
    the row is created by the close itself, through the INSERT branch rather than
    the update. Losing the close there would leave exactly the rows this task is
    about open forever, so the close must hold on that branch too.

    Such a row's `started_at` is the instant it was created, which can sit after
    its own close: nothing in the product computes a duration from that pair, and
    inventing a start for a session nobody recorded starting is the inference
    Rule 3 forbids."""
    key = await _register(client)
    await _push_single(client, key, _event("close-only", CLOSE, _T0))

    row = await pool.fetchrow(
        "SELECT finished_at, started_at FROM sessions WHERE session_id = $1", "close-only"
    )
    assert row is not None, "the close alone must create the session row"
    assert row["finished_at"] == _T0


# --- D4: forward-only ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replayed_older_close_does_not_rewind_the_close(client: AsyncClient, pool):
    """Transcript re-uploads and retried deliveries replay old events at will. A
    close derived from a stale replay would rewrite history and silently undo a
    close that really happened."""
    key = await _register(client)
    late = _T0 + timedelta(minutes=20)
    early = _T0 + timedelta(minutes=3)
    await _push_single(client, key, _event("replayed", CLOSE, late))
    await _push_single(client, key, _event("replayed", CLOSE, early))

    finished_at, last_event_at = await _close_fields(pool, "replayed")
    assert finished_at == late
    assert last_event_at == late


@pytest.mark.asyncio
async def test_a_later_close_advances_the_close(client: AsyncClient, pool):
    """Forward-only means forward: when a genuinely later close arrives the close
    follows it, so the value is always the latest evidence rather than the first."""
    key = await _register(client)
    await _push_batch(client, key, [_event("reopened", CLOSE, _T0)])
    await _push_batch(client, key, [_event("reopened", CLOSE, _T0 + timedelta(hours=2))])

    finished_at, _ = await _close_fields(pool, "reopened")
    assert finished_at == _T0 + timedelta(hours=2)


@pytest.mark.asyncio
async def test_activity_after_a_close_moves_recency_not_the_close(client: AsyncClient, pool):
    """The two columns answer different questions: recency keeps moving when a
    resumed session emits more events, while the close stays where the last
    piece of close evidence put it."""
    key = await _register(client)
    await _push_single(client, key, _event("resumed", CLOSE, _T0))
    later = _T0 + timedelta(hours=1)
    await _push_single(client, key, _event("resumed", "user_message", later))

    finished_at, last_event_at = await _close_fields(pool, "resumed")
    assert finished_at == _T0
    assert last_event_at == later


# --- D2: one shared definition, one write site --------------------------------------------


def test_close_signal_name_is_one_shared_definition():
    """The event_type that names the close is defined once and consumed by both
    ingestion paths and the backfill — and it must stay the value the plugin
    already puts on the wire, or the server stops recognizing real closes."""
    from backend.services import session_service

    assert session_service.CLOSE_EVENT_TYPE == CLOSE

    hooks = (BACKEND.parent / "stashai" / "plugin" / "hooks.py").read_text()
    assert f'"{CLOSE}"' in hooks, "the plugin must send the event_type the server closes on"


def test_the_close_column_has_exactly_one_sql_write_site():
    """Two SQL sites for one invariant drift apart — that is how the demo router
    became the column's only writer while the real ingestion path never stamped
    it. Every running write must therefore go through the one forward-only
    assignment in `upsert_session`, and no caller may update the column beside
    it. Migrations are excluded on purpose: a dated backfill is a deliberate
    one-time write with its own reviewed evidence, not a second live path."""
    out_of_band, stamp_sites = [], []
    for sub in ("services", "routers"):
        for path in (BACKEND / sub).rglob("*.py"):
            text = path.read_text()
            if re.search(r"\bSET finished_at\b", text):
                out_of_band.append(path.name)
            stamps = len(re.findall(r"finished_at = GREATEST", text))
            if stamps:
                stamp_sites.append(f"{path.name}:{stamps}")

    assert out_of_band == [], out_of_band
    assert stamp_sites == ["session_service.py:1"], stamp_sites
