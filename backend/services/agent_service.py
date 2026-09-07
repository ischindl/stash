"""Named, configurable agents — CRUD and the per-turn config a run needs.

An agent is a saved configuration: its model (a provider override), persona
(extra system prompt), run mode (chat vs scheduled), and channel bindings.
Every user has one auto-created default agent; chats and channels resolve to an
agent, whose config shapes the turn.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from fastapi import HTTPException

from ..database import get_pool
from . import agent_auth

logger = logging.getLogger(__name__)

_COLUMNS = (
    "id, user_id, name, model_provider, system_prompt, run_mode, "
    "schedule_cron, schedule_prompt, is_default, is_curator, slack_bound, "
    "telegram_bound, last_run_at, last_run_error, last_run_outcome, curated_through, "
    "curator_wiki, curator_folder_id, model_id, credential_id, digest_provider, digest_model_id, "
    "month_run_count, month_run_anchor, created_at"
)


# The curator runs nightly, inside a quiet window (08:00–11:59 UTC = midnight–4am
# Pacific): users are asleep so the wiki isn't chasing live edits, and no deploys
# are restarting the worker mid-run (the cron tick is consumed up front, so a
# killed run is lost until the next night). Staggered per user within the window
# so sprite wakes don't all fire at once.
CURATOR_WINDOW_START_HOUR_UTC = 8
CURATOR_WINDOW_HOURS = 4


def next_run_at(agent: dict) -> str | None:
    """When this scheduled agent next fires, by the same rule the beat uses:
    the first cron tick after its last run. None when it isn't scheduled or
    its cron is unusable."""
    from croniter import croniter

    if agent.get("run_mode") != "scheduled" or not agent.get("schedule_cron"):
        return None
    base = agent.get("last_run_at") or datetime.now(UTC)
    try:
        return croniter(agent["schedule_cron"], base).get_next(datetime).isoformat()
    except (ValueError, KeyError):
        return None


def _staggered_nightly_cron(user_id: UUID) -> str:
    n = int.from_bytes(user_id.bytes, "big")
    hour = CURATOR_WINDOW_START_HOUR_UTC + (n // 60) % CURATOR_WINDOW_HOURS
    return f"{n % 60} {hour} * * *"


_VALID_PROVIDERS = {"anthropic", "openai", "openrouter", "local"}
_VALID_RUN_MODES = {"chat", "scheduled"}


def _row(row) -> dict:
    d = dict(row)
    d["id"] = str(d["id"])
    return d


async def list_agents(user_id: UUID) -> list[dict]:
    rows = await get_pool().fetch(
        f"SELECT {_COLUMNS} FROM agents WHERE user_id = $1 ORDER BY is_default DESC, created_at",
        user_id,
    )
    return [_row(r) for r in rows]


async def get_agent(user_id: UUID, agent_id: UUID) -> dict:
    row = await get_pool().fetchrow(
        f"SELECT {_COLUMNS} FROM agents WHERE id = $1 AND user_id = $2", agent_id, user_id
    )
    if row is None:
        raise HTTPException(status_code=404, detail="agent not found")
    return _row(row)


async def get_agent_by_id(agent_id: UUID) -> dict:
    """Unscoped fetch for internal tasks — the enqueuing router already
    authorized the caller."""
    row = await get_pool().fetchrow(f"SELECT {_COLUMNS} FROM agents WHERE id = $1", agent_id)
    if row is None:
        raise ValueError(f"agent {agent_id} not found")
    return _row(row)


async def get_or_create_default(user_id: UUID) -> dict:
    """The user's default agent, created on first use."""
    pool = get_pool()
    row = await pool.fetchrow(
        f"SELECT {_COLUMNS} FROM agents WHERE user_id = $1 AND is_default", user_id
    )
    if row is not None:
        return _row(row)
    row = await pool.fetchrow(
        f"""
        INSERT INTO agents (user_id, name, is_default)
        VALUES ($1, 'Stash Agent', true)
        ON CONFLICT (user_id) WHERE is_default DO NOTHING
        RETURNING {_COLUMNS}
        """,
        user_id,
    )
    if row is None:  # lost the race — read the winner.
        row = await pool.fetchrow(
            f"SELECT {_COLUMNS} FROM agents WHERE user_id = $1 AND is_default", user_id
        )
    return _row(row)


# How far back the first curation looks (the wiki bootstraps from this window).
CURATOR_BACKFILL_DAYS = 90


async def get_or_create_curator(user_id: UUID, wiki: str = "internal") -> dict:
    """The scope's reserved curator for one wiki, created on first use.

    `wiki` is "internal" (the scope's own Memory wiki) or "external" (the
    workspace's cross-user anonymized wiki). They are separate agents: separate
    schedules, watermarks and run histories, because they write to different
    places under opposite privacy rules.

    Scheduled nightly (staggered). Both the cron baseline (last_run_at) and the
    delta watermark (curated_through) seed to a bounded backfill point, so the
    first run is due immediately and bootstraps from real history."""
    pool = get_pool()
    row = await pool.fetchrow(
        f"SELECT {_COLUMNS} FROM agents "
        "WHERE user_id = $1 AND is_curator AND curator_wiki = $2 AND curator_folder_id IS NULL",
        user_id,
        wiki,
    )
    if row is not None:
        return _row(row)
    name = "Memory curator" if wiki == "internal" else "External wiki curator"
    row = await pool.fetchrow(
        f"""
        INSERT INTO agents (user_id, name, run_mode, schedule_cron, is_curator,
                            curator_wiki, last_run_at, curated_through)
        SELECT $1, $4, 'scheduled', $2, true, $5, backfill, backfill
        FROM (SELECT greatest((SELECT created_at FROM users WHERE id = $1),
                              now() - make_interval(days => $3)) AS backfill) seed
        ON CONFLICT (
            user_id, curator_wiki,
            COALESCE(curator_folder_id, '00000000-0000-0000-0000-000000000000'::uuid)
        ) WHERE is_curator DO NOTHING
        RETURNING {_COLUMNS}
        """,
        user_id,
        _staggered_nightly_cron(user_id),
        CURATOR_BACKFILL_DAYS,
        name,
        wiki,
    )
    if row is None:  # lost the race — read the winner.
        row = await pool.fetchrow(
            f"SELECT {_COLUMNS} FROM agents "
            "WHERE user_id = $1 AND is_curator AND curator_wiki = $2 AND curator_folder_id IS NULL",
            user_id,
            wiki,
        )
    return _row(row)


async def list_curators(user_id: UUID) -> list[dict]:
    """Every curator of this scope: the two provisioned ones and folder-scoped ones."""
    pool = get_pool()
    rows = await pool.fetch(
        f"SELECT {_COLUMNS} FROM agents WHERE user_id = $1 AND is_curator ORDER BY created_at",
        user_id,
    )
    return [_row(r) for r in rows]


async def create_folder_curator(
    user_id: UUID,
    folder_id: UUID,
    model_provider: str | None = None,
    model_id: str | None = None,
    credential_id: UUID | None = None,
    digest_provider: str | None = None,
    digest_model_id: str | None = None,
) -> dict:
    """A curator bound to one session folder: it reads that folder's feed and
    writes that folder's wiki.

    Idempotent like the workspace curators — the per-scope unique index absorbs
    the race. Its watermark seeds at the folder's first event; a folder with no
    events yet seeds NULL, which reads as "never curated" and bootstraps from
    whatever appears.

    With no model selection the curator stores NULL/NULL/NULL and resolves
    exactly like the workspace curator — the same resolver, the oldest connected
    credential, no folder-curator branch. An explicit selection is restricted to
    the local provider while folder scoping is dogfooded against a self-hosted
    endpoint, and may pin one of the user's endpoints by id.

    The project's wiki home is a file-tree folder (pages only hang off
    `folders`): the first curator a project gets opens it under the project's
    name and links it through session_folders.wiki_folder_id; every later one
    reuses it. Its id travels with the curator row so the run knows where to
    write.

    Folder curators sit on the internal wiki scope: the material they read is
    the owner's own project activity, and the external feed is restricted to
    end-user sessions by design."""
    pool = get_pool()
    folder = await pool.fetchrow(
        "SELECT name, wiki_folder_id FROM session_folders WHERE id = $1 AND owner_user_id = $2",
        folder_id,
        user_id,
    )
    if folder is None:
        raise HTTPException(status_code=404, detail="folder not found")
    if model_provider is not None and model_provider != "local":
        raise HTTPException(status_code=400, detail="folder curators run on the local provider")
    await _validate_pin(user_id, model_provider, model_id, credential_id)
    if digest_model_id is not None and digest_provider is None:
        raise HTTPException(status_code=400, detail="digest_model_id needs a digest_provider")
    if digest_provider is not None and digest_provider != "local":
        raise HTTPException(
            status_code=400, detail="folder curator digest models run on the local provider"
        )
    wiki_folder_id = folder["wiki_folder_id"]
    if wiki_folder_id is None:
        home = await pool.fetchrow(
            "INSERT INTO folders (owner_user_id, name, created_by) VALUES ($1, $2, $1) "
            "RETURNING id",
            user_id,
            folder["name"],
        )
        wiki_folder_id = home["id"]
        await pool.execute(
            "UPDATE session_folders SET wiki_folder_id = $2 WHERE id = $1",
            folder_id,
            home["id"],
        )
    row = await pool.fetchrow(
        f"""
        INSERT INTO agents (user_id, name, run_mode, schedule_cron, is_curator,
                            curator_wiki, curator_folder_id, model_provider, model_id,
                            credential_id, digest_provider, digest_model_id,
                            last_run_at, curated_through)
        SELECT $1, 'Wiki curator — ' || $4, 'scheduled', $2, true,
               'internal', $3, $5, $6,
               $7, $8, $9,
               now(),
               (SELECT min(he.created_at)
                FROM history_events he
                JOIN sessions s ON s.owner_user_id = he.owner_user_id
                               AND s.session_id = he.session_id
                WHERE s.owner_user_id = $1 AND s.session_folder_id = $3)
        ON CONFLICT (
            user_id, curator_wiki,
            COALESCE(curator_folder_id, '00000000-0000-0000-0000-000000000000'::uuid)
        ) WHERE is_curator DO NOTHING
        RETURNING {_COLUMNS}
        """,
        user_id,
        _staggered_nightly_cron(user_id),
        folder_id,
        folder["name"],
        model_provider,
        model_id,
        credential_id,
        digest_provider,
        digest_model_id,
    )
    if row is None:  # lost the race (or already existed) — read the winner.
        row = await pool.fetchrow(
            f"SELECT {_COLUMNS} FROM agents "
            "WHERE user_id = $1 AND is_curator AND curator_folder_id = $2",
            user_id,
            folder_id,
        )
    curator = _row(row)
    curator["wiki_folder_id"] = str(wiki_folder_id)
    return curator


async def disconnect_local_endpoint(user_id: UUID, credential_id: UUID) -> list[dict]:
    """Disconnect a local endpoint — unless one of the user's agents pins it.

    Deleting a pinned box would strand every run that points at it, so the
    referencing agents (id + name) are returned and NOTHING is deleted; the
    caller turns that list into a 409 that names them. An empty list means the
    endpoint is gone.
    """
    refs = await get_pool().fetch(
        "SELECT id, name FROM agents WHERE user_id = $1 AND credential_id = $2 ORDER BY name",
        user_id,
        credential_id,
    )
    if refs:
        return [{"id": str(r["id"]), "name": r["name"]} for r in refs]
    await agent_auth.delete_endpoint(user_id, credential_id)
    return []


async def get_curator_by_id(agent_id: UUID) -> dict | None:
    pool = get_pool()
    row = await pool.fetchrow(
        f"SELECT {_COLUMNS} FROM agents WHERE id = $1 AND is_curator", agent_id
    )
    return _row(row) if row else None


async def update_curator(
    agent_id: UUID,
    model_provider: str | None = ...,
    model_id: str | None = ...,
    credential_id: UUID | None = ...,
    schedule_cron: str | None = ...,
    curated_through: datetime | None = ...,
    digest_provider: str | None = ...,
    digest_model_id: str | None = ...,
) -> dict:
    """PATCH semantics: `...` means leave the field alone, None clears it."""
    fields: dict = {}
    if model_provider is not ...:
        if model_provider is not None and model_provider not in _VALID_PROVIDERS:
            raise HTTPException(status_code=400, detail=f"invalid model_provider: {model_provider}")
        fields["model_provider"] = model_provider
    if model_id is not ...:
        fields["model_id"] = model_id
    if credential_id is not ...:
        fields["credential_id"] = credential_id
    if digest_provider is not ...:
        if digest_provider is not None and digest_provider not in _VALID_PROVIDERS:
            raise HTTPException(
                status_code=400, detail=f"invalid digest_provider: {digest_provider}"
            )
        fields["digest_provider"] = digest_provider
    if digest_model_id is not ...:
        fields["digest_model_id"] = digest_model_id
    touches_pin = any(k in fields for k in ("model_provider", "model_id", "credential_id"))
    wants_digest_check = (
        fields.get("digest_provider") is not None or fields.get("digest_model_id") is not None
    )
    if touches_pin or wants_digest_check:
        row = await get_curator_by_id(agent_id) or _raise_missing(agent_id)
        if touches_pin:
            # The create-time folder rule applies to PATCH too: an API that
            # could move a folder curator onto Claude after the fact would make
            # the create-time refusal decorative.
            if (
                row["curator_folder_id"] is not None
                and fields.get("model_provider") is not None
                and fields.get("model_provider") != "local"
            ):
                raise HTTPException(
                    status_code=400, detail="folder curators run on the local provider"
                )
            await _validate_pin(
                UUID(str(row["user_id"])),
                fields.get("model_provider", row["model_provider"]),
                fields.get("model_id", row["model_id"]),
                fields.get("credential_id", row["credential_id"]),
            )
        if wants_digest_check:
            # A digest model reads the raw feed before the curator's own model —
            # on the external curator that feed is end-user material, and a
            # second model on it is a second processor of customer data. Not this
            # knob's call to make silently. Folder curators keep the create-time
            # rule: the self-hosted endpoint only.
            if row["curator_wiki"] == "external":
                raise HTTPException(
                    status_code=400,
                    detail="the external curator runs a single model (its feed is end-user material)",
                )
            effective_provider = fields.get("digest_provider", row["digest_provider"])
            if fields.get("digest_model_id") is not None and effective_provider is None:
                raise HTTPException(
                    status_code=400, detail="digest_model_id needs a digest_provider"
                )
            if (
                row["curator_folder_id"] is not None
                and effective_provider is not None
                and effective_provider != "local"
            ):
                raise HTTPException(
                    status_code=400,
                    detail="folder curator digest models run on the local provider",
                )
    if schedule_cron is not ...:
        if schedule_cron is None:
            # The runtime has no enabled column: a folder curator is idled by
            # clearing its schedule. A workspace curator without one silently
            # rots, so it must always carry a cadence.
            row = await get_curator_by_id(agent_id) or _raise_missing(agent_id)
            if row["curator_folder_id"] is None:
                raise HTTPException(
                    status_code=400, detail="workspace curators must keep a schedule"
                )
        elif not schedule_cron.strip():
            raise HTTPException(status_code=400, detail="invalid schedule_cron")
        fields["schedule_cron"] = schedule_cron
    if curated_through is not ...:
        fields["curated_through"] = curated_through
    if not fields:
        return await get_curator_by_id(agent_id) or _raise_missing(agent_id)
    sets = ", ".join(f"{k} = ${i + 2}" for i, k in enumerate(fields))
    pool = get_pool()
    row = await pool.fetchrow(
        f"UPDATE agents SET {sets} WHERE id = $1 AND is_curator RETURNING {_COLUMNS}",
        agent_id,
        *fields.values(),
    )
    if row is None:
        _raise_missing(agent_id)
    return _row(row)


def _raise_missing(agent_id: UUID):
    raise HTTPException(status_code=404, detail=f"curator {agent_id} not found")


async def delete_curator(agent_id: UUID) -> None:
    """Retire a curator. Folder-scoped ones only via the API; the workspace
    pair is permanent (their `is_curator` guard lives in the callers)."""
    pool = get_pool()
    await pool.execute("DELETE FROM agents WHERE id = $1 AND is_curator", agent_id)


async def rewind_folder_curator_for_sessions(
    owner_user_id: UUID, folder_id: UUID, session_row_ids: list[UUID]
) -> None:
    """Filing sessions into a curated project reopens the folder curator's
    position when the sessions carry events older than it. The curator's promise
    is that everything filed into its project gets read, and the ingest-side
    rewind (memory_service) cannot keep that promise: at ingest a session has
    no folder yet, and rewinding every folder curator for every event would
    hand any unrelated push a leash on every project's position."""
    pool = get_pool()
    oldest = await pool.fetchval(
        "SELECT min(he.created_at) FROM history_events he "
        "JOIN sessions s ON s.owner_user_id = he.owner_user_id AND s.session_id = he.session_id "
        "WHERE s.owner_user_id = $1 AND s.id = ANY($2::uuid[])",
        owner_user_id,
        session_row_ids,
    )
    if oldest is None:
        return
    target = oldest - timedelta(microseconds=1)
    moved = await pool.fetch(
        "UPDATE agents a SET curated_through = $2 "
        "FROM (SELECT id, curated_through AS was FROM agents "
        "      WHERE curator_folder_id = $1 AND is_curator AND curated_through > $2) o "
        "WHERE a.id = o.id RETURNING a.id, o.was",
        folder_id,
        target,
    )
    for row in moved:
        logger.info(
            "folder curator %s watermark rewound: %s -> %s (sessions filed in "
            "carry events older than the watermark)",
            row["id"],
            row["was"],
            target,
        )


def _validate(model_provider, run_mode, schedule_cron) -> None:
    if model_provider is not None and model_provider not in _VALID_PROVIDERS:
        raise HTTPException(status_code=400, detail=f"invalid model_provider: {model_provider}")
    if run_mode not in _VALID_RUN_MODES:
        raise HTTPException(status_code=400, detail=f"invalid run_mode: {run_mode}")
    if run_mode == "scheduled" and not schedule_cron:
        raise HTTPException(status_code=400, detail="scheduled agents need a schedule_cron")


async def _validate_pin(
    user_id: UUID,
    model_provider: str | None,
    model_id: str | None,
    credential_id: UUID | None,
) -> None:
    """A pin must be coherent and must point at something the user owns.

    model_id and credential_id are selections *within* a provider, so an
    inherited row (no model_provider) cannot carry either — a dangling model
    name would silently decide nothing and a dangling endpoint id would read as
    an accidental inheritance change. And credential_id must be one of this
    user's local endpoint rows: a row whose pin cannot resolve would only fail
    at the next turn, when the cause is hardest to see.
    """
    if model_provider is None and (model_id is not None or credential_id is not None):
        raise HTTPException(
            status_code=400,
            detail="model_id/credential_id require a model_provider; without one the agent inherits",
        )
    if credential_id is not None:
        if await agent_auth.get_local_endpoint(user_id, credential_id) is None:
            raise HTTPException(
                status_code=400, detail="credential_id is not one of your local endpoints"
            )


async def create_agent(user_id: UUID, fields: dict) -> dict:
    run_mode = fields.get("run_mode", "chat")
    _validate(fields.get("model_provider"), run_mode, fields.get("schedule_cron"))
    row = await get_pool().fetchrow(
        f"""
        INSERT INTO agents (user_id, name, model_provider, system_prompt,
                            run_mode, schedule_cron, schedule_prompt, last_run_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7,
                CASE WHEN $5 = 'scheduled' THEN now() ELSE NULL END)
        RETURNING {_COLUMNS}
        """,
        user_id,
        (fields.get("name") or "Agent").strip()[:80],
        fields.get("model_provider"),
        fields.get("system_prompt"),
        run_mode,
        fields.get("schedule_cron"),
        fields.get("schedule_prompt"),
    )
    return _row(row)


async def update_agent(user_id: UUID, agent_id: UUID, fields: dict) -> dict:
    current = await get_agent(user_id, agent_id)
    # The curator is a reserved system agent — its name, prompt, and staggered
    # cron are managed by Stash. The one user decision is whether the nightly
    # cloud run happens at all: run_mode 'scheduled' (on) or 'chat' (off, e.g.
    # when curating locally). Off keeps on-demand runs working.
    if current["is_curator"] and set(fields) - {"run_mode"}:
        raise HTTPException(
            status_code=400,
            detail="only run_mode can change on the Memory curator (its schedule on/off switch)",
        )
    merged = {**current, **fields}
    _validate(
        merged.get("model_provider"), merged.get("run_mode", "chat"), merged.get("schedule_cron")
    )

    async with get_pool().acquire() as conn, conn.transaction():
        # Channel bindings are unique per user; clear others in the same tx so
        # the partial unique index can't transiently see two bound agents.
        if merged.get("slack_bound"):
            await conn.execute(
                "UPDATE agents SET slack_bound = false WHERE user_id = $1 AND id <> $2",
                user_id,
                agent_id,
            )
        if merged.get("telegram_bound"):
            await conn.execute(
                "UPDATE agents SET telegram_bound = false WHERE user_id = $1 AND id <> $2",
                user_id,
                agent_id,
            )
        # Seed last_run_at when the agent first becomes scheduled, so the cron
        # has a baseline (a NULL baseline never becomes due).
        row = await conn.fetchrow(
            f"""
            UPDATE agents SET
                name = $3, model_provider = $4, system_prompt = $5, run_mode = $6,
                schedule_cron = $7, schedule_prompt = $8, slack_bound = $9, telegram_bound = $10,
                last_run_at = CASE
                    WHEN $6 = 'scheduled' AND last_run_at IS NULL THEN now()
                    WHEN $6 <> 'scheduled' THEN NULL
                    ELSE last_run_at END
            WHERE id = $1 AND user_id = $2
            RETURNING {_COLUMNS}
            """,
            agent_id,
            user_id,
            (merged.get("name") or "Agent").strip()[:80],
            merged.get("model_provider"),
            merged.get("system_prompt"),
            merged.get("run_mode", "chat"),
            merged.get("schedule_cron"),
            merged.get("schedule_prompt"),
            bool(merged.get("slack_bound")),
            bool(merged.get("telegram_bound")),
        )
    return _row(row)


async def delete_agent(user_id: UUID, agent_id: UUID) -> None:
    agent = await get_agent(user_id, agent_id)
    if agent["is_default"]:
        raise HTTPException(status_code=400, detail="cannot delete the default agent")
    if agent["is_curator"]:
        raise HTTPException(
            status_code=400, detail="cannot delete the Memory curator (turn it off instead)"
        )
    await get_pool().execute("DELETE FROM agents WHERE id = $1 AND user_id = $2", agent_id, user_id)


async def list_scheduled() -> list[dict]:
    """All scheduled agents due for the beat task's check. The curator runs the
    curation prompt (no schedule_prompt); other scheduled agents need one."""
    rows = await get_pool().fetch(
        f"SELECT {_COLUMNS} FROM agents "
        "WHERE run_mode = 'scheduled' AND schedule_cron IS NOT NULL "
        "AND (is_curator OR schedule_prompt IS NOT NULL)"
    )
    return [_row(r) for r in rows]


def month_runs_used(agent: dict) -> int:
    """Scheduled runs consumed in the current calendar month. An anchor from a
    prior month means the counter is stale; mark_run resets it on the next run."""
    anchor = agent.get("month_run_anchor")
    today = date.today()
    if anchor is None or (anchor.year, anchor.month) != (today.year, today.month):
        return 0
    return agent["month_run_count"]


async def mark_run(agent_id: UUID, metered: bool = True) -> int:
    """Consume the cron tick and meter the run against the calendar month.
    Returns the run count within the current month (including this one) —
    the free-tier curator credit gate reads it.

    `metered=False` consumes the tick without touching the month counter —
    for runs the platform initiates on its own (the first-day curator), which
    must not eat the user's free allowance.

    Also clears last_run_error and stamps the outcome as started. Every path
    after this call must resolve the outcome as ran, failed, or skipped."""
    if not metered:
        return await get_pool().fetchval(
            """
            UPDATE agents SET
                last_run_at = now(),
                last_run_error = NULL,
                last_run_outcome = 'started'
            WHERE id = $1
            RETURNING month_run_count
            """,
            agent_id,
        )
    return await get_pool().fetchval(
        """
        UPDATE agents SET
            last_run_at = now(),
            last_run_error = NULL,
            last_run_outcome = 'started',
            month_run_count = CASE
                WHEN month_run_anchor = date_trunc('month', now())::date
                THEN month_run_count + 1 ELSE 1 END,
            month_run_anchor = date_trunc('month', now())::date
        WHERE id = $1
        RETURNING month_run_count
        """,
        agent_id,
    )


async def mark_run_failed(agent_id: UUID, error: str, metered: bool = True) -> None:
    """Stamp the failure where the API can surface it, and refund the month
    credit — an outage shouldn't eat the free allowance. An unmetered run
    (`metered=False`) never charged one, so it has nothing to refund. The tick
    itself stays consumed (last_run_at), so the beat won't re-fire the same
    window."""
    await get_pool().execute(
        """
        UPDATE agents SET
            last_run_error = left($2, 500),
            last_run_outcome = 'failed',
            month_run_count = CASE WHEN $3
                THEN greatest(month_run_count - 1, 0) ELSE month_run_count END
        WHERE id = $1
        """,
        agent_id,
        error,
        metered,
    )


async def mark_run_skipped(agent_id: UUID, reason: str) -> None:
    """Resolve a consumed tick that stopped at a designed scheduler gate.

    The outcome is stored as `skipped_{reason}`, and `agents_last_run_outcome`
    is a CHECK-constrained set (created in migration 0184, widened by 0204) — a
    new gate has to add its value there or the write is rejected."""
    await get_pool().execute(
        "UPDATE agents SET last_run_outcome = $2 WHERE id = $1",
        agent_id,
        f"skipped_{reason}",
    )


async def mark_run_succeeded(agent_id: UUID) -> None:
    """Resolve a run after execution and all post-run bookkeeping complete."""
    await get_pool().execute(
        "UPDATE agents SET last_run_outcome = 'ran' WHERE id = $1",
        agent_id,
    )


async def mark_curated(agent_id: UUID, through: datetime) -> datetime:
    """Advance the curator's delta watermark — only after a successful run, so
    a failed run's window is re-covered next time. Returns the stored position.

    The advance is monotonic: GREATEST means a run that computed its position
    from a snapshot taken before an overlapping run finished (or a backfill
    that read from the oldest) can never walk the watermark backwards. A
    refused position is logged naming the curator and the retained position —
    a run's number vanishing in silence is exactly what read as "completed
    curation discarded". The one writer allowed to move it backwards is the
    ingest rewind in memory_service: a deliberate re-read of imported history,
    not a run's bookkeeping."""
    stored = await get_pool().fetchval(
        "UPDATE agents SET curated_through = greatest(curated_through, $2) WHERE id = $1 "
        "RETURNING curated_through",
        agent_id,
        through,
    )
    if stored > through:
        logger.info(
            "curator %s watermark not moved: run proposed %s, stored position kept at %s "
            "(advance is monotonic; see mark_curated)",
            agent_id,
            through,
            stored,
        )
    return stored


async def set_system_prompt(agent_id: UUID, text: str | None) -> dict:
    """The agent's persona — appended to its system prompt on every run.
    Empty string clears it back to the default."""
    row = await get_pool().fetchrow(
        f"UPDATE agents SET system_prompt = $2 WHERE id = $1 RETURNING {_COLUMNS}",
        agent_id,
        text or None,
    )
    if row is None:
        raise ValueError(f"agent {agent_id} not found")
    return _row(row)


async def channel_agent(user_id: UUID, channel: str) -> dict:
    """The agent bound to a channel ('slack'|'telegram'), or the default."""
    col = "slack_bound" if channel == "slack" else "telegram_bound"
    row = await get_pool().fetchrow(
        f"SELECT {_COLUMNS} FROM agents WHERE user_id = $1 AND {col}", user_id
    )
    return _row(row) if row is not None else await get_or_create_default(user_id)
