"""The digest phase's session id must fit the column it is written into.

Two-phase curator runs (any agent with a digest model) mint a second session id
per run: `scheduled_session_prefix(agent)` + run stamp + the `-digest` suffix.
The prefix alone is 50 characters — `agent-curate-` plus the curator's UUID —
so a minute beat stamp lands at 69 and the manual dispatch's second-precision
stamp (`agent_schedules.py` calls `run_scheduled` with `%Y%m%d%H%M%S` so a
manual run never shares a session with the minute's scheduled run) lands at
71. `history_events.session_id` was VARCHAR(64), so every digest run died
inside `memory_service.push_event` with asyncpg's StringDataRightTruncationError
before the digest phase ever read the feed (founder stack, STAS-263).

These tests pin the invariant three ways, so each of the three ways it can
break again goes red on the break:

1. the production digest-id builder, run through both stamp shapes, must stay
   within the real column limit read from `information_schema` — adding a
   suffix or widening a stamp without widening the column reddens this;
2. the real production writer must store the 71-character id byte-for-byte —
   the original symptom, inserted through `push_event` and read back;
3. the public ingest model's `max_length` must equal the column limit — the
   API must never accept what the column would reject.
"""

from uuid import UUID, uuid4

import pytest
from annotated_types import MaxLen
from httpx import AsyncClient

import backend.models as models
from backend.database import get_pool
from backend.services import agent_service, memory_service
from backend.services.sprite_agent_service import build_digest_turn, scheduled_session_prefix

from .conftest import unique_name

# The two stamp shapes a run can carry: the nightly beat's minute precision and
# the manual dispatch's second precision. The second is the longer, and it is
# the one that killed every digest run on the founder stack.
BEAT_STAMP = "202609230102"
MANUAL_STAMP = "20260923010203"


async def _column_limit() -> int:
    limit = await get_pool().fetchval(
        "SELECT character_maximum_length FROM information_schema.columns "
        "WHERE table_name = 'history_events' AND column_name = 'session_id'"
    )
    assert limit is not None, (
        "session_id lost its VARCHAR limit — the width invariant is unenforced"
    )
    return int(limit)


def _synthetic_curator() -> dict:
    """An internal-curator row with the widest ids the scheme allows: a fresh
    UUID and no watermark, so `build_digest_turn` renders without touching the
    database — the subject here is the id the builder mints, not the feed."""
    return {
        "id": str(uuid4()),
        "is_curator": True,
        "curator_wiki": "internal",
        "curator_folder_id": None,
        "curated_through": None,
    }


@pytest.mark.parametrize("stamp", [BEAT_STAMP, MANUAL_STAMP])
async def test_longest_digest_id_fits_the_column(stamp: str, _db_pool):
    """Every digest session id the production builder can mint fits the column.

    The equality pin is the point: a future suffix, stamp format, or prefix
    change that overflows goes red on the arithmetic, not in production."""
    limit = await _column_limit()
    agent = _synthetic_curator()
    session_id, _prompt = await build_digest_turn(agent, stamp)
    assert session_id == f"agent-curate-{agent['id']}-{stamp}-digest"
    assert len(session_id) <= limit, (
        f"digest session id is {len(session_id)} chars, the column holds {limit}"
    )


async def test_history_events_stores_the_full_digest_session_id(client: AsyncClient, _db_pool):
    """The original symptom, through the real writer: the 71-char id the manual
    dispatch mints must insert and read back byte-for-byte (pre-fix this raised
    asyncpg StringDataRightTruncationError)."""
    name = unique_name("width")
    response = await client.post(
        "/api/v1/users/register",
        json={"name": name, "password": "securepassword1", "email": f"{name}@example.com"},
    )
    assert response.status_code == 201
    user_id = UUID(response.json()["id"])
    curator = await agent_service.get_or_create_curator(user_id)

    digest_id = f"{scheduled_session_prefix(curator)}{MANUAL_STAMP}-digest"
    # 13 prefix + 36 uuid + 1 dash + 14 stamp + 7 suffix: pinned so the test
    # cannot silently shrink toward vacuity if the scheme ever changes shape.
    assert len(digest_id) == 71

    event = await memory_service.push_event(
        user_id, "curator", "user_message", "digest probe", user_id, digest_id
    )
    stored = await get_pool().fetchval(
        "SELECT session_id FROM history_events WHERE id = $1", event["id"]
    )
    assert stored == digest_id


async def test_ingest_api_limit_matches_the_column(_db_pool):
    """`POST /events` must never accept a session id the column would reject:
    the shared model's max_length equals the real column width."""
    limit = await _column_limit()
    field = models.HistoryEventCreateRequest.model_fields["session_id"]
    constraint = next((c for c in field.metadata if isinstance(c, MaxLen)), None)
    assert constraint is not None, "the ingest model dropped its session_id length limit"
    assert constraint.max_length == limit
