"""An installation with no Stripe key must never be asked whether an account is Pro.

The defect: `billing_service.is_pro` had exactly one producer and every plan gate as
its callers, and only one caller first asked `billing_enabled()`. On a box that cannot
charge anybody, all the others therefore answered "no" forever — no Stripe row
can exist, so no subscription can be active. (The caller count is deliberately not
stated: gates consolidate over time, the fix at the source does not depend on it.) The chained curator catch-up drain was
dead and silent; a lane could be frozen as `skipped_credits` against a monthly
ceiling nobody was billing for; the managed-model lane was downgraded to
`NeedsAuth`; and two routes handed the client a denominator of ten. The operator's
workaround was to declare their own email domain internal, which makes an
account-level claim about a person to work around a missing env var.

Why this file is written the way it is: every test here leaves
`settings.STRIPE_SECRET_KEY` unset and NEVER patches `billing_service.is_pro`.
Patching the function would replace the very answer under test — a short-circuit
inside it would never run, and the test would pass on an install that still asks.
So the only lever is the installation itself, and each test pairs its no-billing
answer with the billed answer on the same row state, via `managed_billing`. Without
that pairing a test here could pass vacuously: on a self-host every account reads
unlimited, so "it was unlimited" proves nothing until the same fixture is shown to
refuse once billing exists.

Verified by mutation, not by assertion count: with Step 1's guard removed from
`is_pro`, tests 1, 3, 4, 5, and 6 all redden (the run is in the task log).
"""

import uuid
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from cryptography.fernet import Fernet
from httpx import AsyncClient

from backend.config import settings
from backend.database import get_pool
from backend.services import (
    agent_auth,
    agent_service,
    billing_service,
    sprite_agent_service,
)
from backend.services import harness as h
from backend.tasks import agent_schedules

from .conftest import FakeRedis, managed_billing
from .test_curator import _auth, _register
from .test_curator_credit_metering import ALLOWANCE, _connect_cloud, _curator_with_work, _row
from .test_curator_credit_metering import _make_due as _rewind_to_due
from .test_developer_platform import _developer

ALLOWANCE_SPENT = ALLOWANCE


@pytest.fixture(autouse=True)
def _credential_storage(monkeypatch: pytest.MonkeyPatch) -> None:
    """Credential rows are Fernet-encrypted and the suite carries no key."""
    monkeypatch.setattr(settings, "INTEGRATIONS_ENCRYPTION_KEY", Fernet.generate_key().decode())


@pytest.fixture(autouse=True)
def _run_lock_broker(monkeypatch: pytest.MonkeyPatch) -> None:
    """The beat takes a per-lane turn lock; CI provisions no broker."""
    monkeypatch.setattr(sprite_agent_service, "_get_redis", lambda: FakeRedis())


@pytest.fixture
def sprite_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """The cloud lane's environment, so a connected cloud key resolves."""
    monkeypatch.setattr(settings, "AGENT_EXEC_MODE", "sprites")


async def _yes(*args, **kwargs):
    return True


async def _capture_beat_dispatch(monkeypatch: pytest.MonkeyPatch) -> list[tuple]:
    """Stub the beat's celery hand-off, capturing arguments (the metering answer
    travels as a keyword, so `*args` alone would swallow it)."""
    dispatched: list[tuple] = []

    def _stub(*args, **kwargs):
        dispatched.append((args, kwargs))

    monkeypatch.setattr(agent_schedules.run_scheduled_agent, "delay", _stub)
    return dispatched


async def _put_at_the_month_ceiling(agent: dict) -> None:
    """Leave the row exactly as a spent calendar month leaves it: the counter at
    the ceiling with an anchor naming THIS month, which is the reading
    `free_allowance_spent` consults."""
    await get_pool().execute(
        "UPDATE agents SET month_run_count = $2, month_run_anchor = date_trunc('month', now())::date "
        "WHERE id = $1",
        agent["id"],
        ALLOWANCE_SPENT,
    )


async def _catchup_row(user_id: UUID) -> dict:
    """A curator mid-chain: scheduled, its last pass resolved as `ran`, and its
    watermark ahead of the start time the chain was handed — so the only thing
    left that can stop the next pass is a permission answer."""
    agent = await agent_service.get_agent_by_id(
        (await agent_service.get_or_create_curator(user_id))["id"]
    )
    started = datetime.now(UTC)
    await get_pool().execute(
        "UPDATE agents SET curated_through = $2, last_run_outcome = 'ran' WHERE id = $1",
        agent["id"],
        started + timedelta(milliseconds=1),
    )
    return agent, started


def _capture_catchup_dispatch(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Capture the follow-on pass rather than let it reach a broker."""
    queued: list[dict] = []
    monkeypatch.setattr(
        agent_schedules.run_curator_now, "apply_async", lambda **kw: queued.append(kw)
    )
    return queued


# --- 1 and 2: the chained catch-up pass -------------------------------------------------


@pytest.mark.asyncio
async def test_the_chained_catchup_pass_runs_with_no_stripe_key(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, sprite_mode
):
    """The drain that was permanently dead on a self-host, and stays alive.

    Copied shape from `test_agent_schedule_alerts.test_catchup_uses_pending_input_not_
    outer_task_start_time`, with its `is_pro` patch deliberately dropped: that patch is
    what let the chain look green on an install that could never run it.
    """
    assert not billing_service.billing_enabled()

    key, uid = await _register(client)
    agent, started = await _catchup_row(uid)
    monkeypatch.setattr(agent_schedules, "_pending_input", _yes)
    # The chain resolves the credential before it asks about the plan, and this lane is
    # scoped, so the backend key is what makes the pass platform-funded. Keeping it
    # funded is the point: an unfunded pass would chain whatever the plan answer said,
    # and the pairing below would prove nothing.
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "sk-ant-test-key")
    queued = _capture_catchup_dispatch(monkeypatch)

    await agent_schedules._queue_curator_catchup(agent, started, 2, workspace={"id": uuid.uuid4()})
    assert len(queued) == 1, "the chain declined without saying so, which is the defect"

    with managed_billing(monkeypatch):
        assert billing_service.billing_enabled()
        queued.clear()
        await agent_schedules._queue_curator_catchup(
            agent, started, 2, workspace={"id": uuid.uuid4()}
        )

    assert queued == [], "the same row on a billed install is a free account, and it declines"


@pytest.mark.asyncio
async def test_the_plan_question_is_never_asked_not_merely_answered(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, sprite_mode
):
    """Distinguishes "short-circuited" from "queried and happened to get a good answer".

    The plan answer is severed at its own read, and the same severed read is run from
    both installations: on a billed one it blows up, which is what makes the patch a
    live seam rather than decoration; on this one the chain completes anyway, which is
    only possible because the read is never reached.
    """

    def no_database():
        raise AssertionError("the plan answer tried to read the database")

    monkeypatch.setattr(billing_service, "get_pool", no_database)
    _, uid = await _register(client)

    # The patch bites: on an installation that CAN charge, the plan answer is a
    # database read, and this one blows up.
    with managed_billing(monkeypatch):
        with pytest.raises(AssertionError, match="tried to read the database"):
            await billing_service.is_pro(uid)

    # On this installation the same severed read costs nothing, because the
    # question is never reached.
    assert not billing_service.billing_enabled()
    agent, started = await _catchup_row(uid)
    monkeypatch.setattr(agent_schedules, "_pending_input", _yes)
    # Funded like its sibling test above: the credential is resolved before the plan,
    # so the key is what carries the run to the question under test.
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "sk-ant-test-key")
    queued = _capture_catchup_dispatch(monkeypatch)

    await agent_schedules._queue_curator_catchup(agent, started, 2, workspace={"id": uuid.uuid4()})

    assert len(queued) == 1


# --- 3: the monthly allowance -----------------------------------------------------------


@pytest.mark.asyncio
async def test_a_spent_allowance_does_not_freeze_a_self_host_curator(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, sprite_mode
):
    """`skipped_credits` is unwritable where nobody is billing.

    A metered lane (a connected cloud key, so the platform pays tokens) at the month
    ceiling, driven through the real beat. Without Stripe the tick dispatches; with it,
    the identical row is stamped `skipped_credits` — the outcome the founder island's
    schedule page showed for a ceiling it was never charged for.
    """
    assert not billing_service.billing_enabled()
    key, uid = await _register(client)
    await _connect_cloud(client, key)
    agent = await _curator_with_work(uid)
    await _put_at_the_month_ceiling(agent)
    dispatched = await _capture_beat_dispatch(monkeypatch)

    assert await agent_schedules._run_due() == 1
    assert len(dispatched) == 1
    assert (await _row(agent["id"]))["last_run_outcome"] != "skipped_credits"

    await _rewind_to_due(agent)
    with managed_billing(monkeypatch):
        assert await agent_schedules._run_due() == 0

    assert (await _row(agent["id"]))["last_run_outcome"] == "skipped_credits"


# --- 4: the managed model lane ----------------------------------------------------------


@pytest.mark.asyncio
async def test_no_tool_path_is_downgraded_on_the_plan(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, sprite_mode
):
    """Mirrors `test_agent_auth.test_no_credential_pro_gets_managed_openrouter_glm`
    without patching `is_pro`: on a self-host there is no Pro to be missing, so the
    managed harness is what an unauthenticated account resolves to, and `NeedsAuth`
    there would be a refusal to run a model the box already has a key for.
    """
    assert not billing_service.billing_enabled()
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "sk-managed-or")
    _, uid = await _register(client)

    auth = await agent_auth.resolve(uid)

    assert auth.harness is h.OPENCODE
    assert auth.harness.default_model == "z-ai/glm-5.2"
    assert auth.env == {"OPENROUTER_API_KEY": "sk-managed-or"}

    with managed_billing(monkeypatch):
        with pytest.raises(agent_auth.NeedsAuth):
            await agent_auth.resolve(uid)


# --- 5: no denominator is displayed -----------------------------------------------------


@pytest.mark.asyncio
async def test_the_curators_route_shows_no_cap_it_cannot_charge(
    client: AsyncClient, _db_pool, monkeypatch: pytest.MonkeyPatch
):
    """`free_runs_per_month` is a plan answer, so on a box with no plans it is NULL —
    the same value a Pro account is shown, and the same reason: there is no ceiling
    for the client to render. The row's `plan` is still `'free'`, which is the point:
    the numeral used to come from a column this install never reads.
    """
    assert not billing_service.billing_enabled()
    key, uid = await _register(client)
    await agent_service.get_or_create_curator(uid)
    assert (await _db_pool.fetchrow("SELECT plan FROM users WHERE id = $1", uid))["plan"] == "free"

    body = (await client.get("/api/v1/me/curators", headers=_auth(key))).json()
    assert body["free_runs_per_month"] is None

    with managed_billing(monkeypatch):
        billed = (await client.get("/api/v1/me/curators", headers=_auth(key))).json()

    assert billed["free_runs_per_month"] == settings.FREE_CURATOR_RUNS_PER_MONTH


@pytest.mark.asyncio
async def test_the_developer_curator_route_shows_no_cap_it_cannot_charge(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    """The second surface, which the intake missed and the census caught: the same
    numeral on the developer curator route, so it is pinned here too.
    """
    assert not billing_service.billing_enabled()
    api_key, _, workspace = await _developer(client)
    scope_uid = UUID(workspace["scope_user_id"])
    # A developer workspace's scope user is enterprise-granted by activation, so the
    # account the allowance could constrain has to be spelled out or the two halves
    # are not the same account.
    await get_pool().execute("UPDATE users SET plan = 'free' WHERE id = $1", scope_uid)
    scope = {**_auth(api_key), "X-Stash-Scope": workspace["scope_user_id"]}

    body = (await client.get("/api/v1/me/developer/curator", headers=scope)).json()
    assert body["free_runs_per_month"] is None

    with managed_billing(monkeypatch):
        billed = (await client.get("/api/v1/me/developer/curator", headers=scope)).json()

    assert billed["free_runs_per_month"] == settings.FREE_CURATOR_RUNS_PER_MONTH


# --- 6: the manual run ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_manual_run_is_not_refused_on_a_spent_allowance_nobody_charges(
    client: AsyncClient, _db_pool, monkeypatch: pytest.MonkeyPatch, sprite_mode
):
    """The 402 a self-hosted user used to get for pressing "recompute" after run ten.

    Nothing is stubbed but the broker: the credential connects through the real route,
    the allowance is spent by the real update the gate reads, and the run is captured
    at its own dispatch.
    """
    assert not billing_service.billing_enabled()
    key, uid = await _register(client)
    await _connect_cloud(client, key)
    curator = await agent_service.get_or_create_curator(uid)
    await client.post(
        "/api/v1/me/pages/new", json={"name": "N", "content": "x"}, headers=_auth(key)
    )
    await _put_at_the_month_ceiling(curator)
    dispatched: list[tuple] = []
    monkeypatch.setattr(
        agent_schedules.run_curator_now,
        "delay",
        lambda *a, **kw: dispatched.append((a, kw)),
    )

    r = await client.post("/api/v1/me/memory/recompute", headers=_auth(key))

    assert r.status_code == 202, r.text
    assert len(dispatched) == 1

    with managed_billing(monkeypatch):
        refused = await client.post("/api/v1/me/memory/recompute", headers=_auth(key))

    assert refused.status_code == 402, "the paywall is real where billing is configured"


# --- 7: the entitlement ladder (AC d) ---------------------------------------------------


@pytest.mark.asyncio
async def test_the_ladder_still_climbs_where_billing_is_configured(
    client: AsyncClient, _db_pool, billing_on, monkeypatch: pytest.MonkeyPatch
):
    """The ladder is consulted in exactly one installation state, so this test runs in
    it: an `enterprise` grant and an internal-domain address still answer Pro with no
    Stripe row at all, a plain account still does not, and the operator's kill switch
    still turns the domain grant off.
    """
    assert billing_service.billing_enabled()

    _, enterprise = await _register(client)
    await _db_pool.execute("UPDATE users SET plan = 'enterprise' WHERE id = $1", enterprise)
    assert await billing_service.is_pro(enterprise)

    _, internal = await _register(client)
    await _db_pool.execute("UPDATE users SET email = 'boss@joinstash.ai' WHERE id = $1", internal)
    assert await billing_service.is_pro(internal)

    _, plain = await _register(client)
    assert not await billing_service.is_pro(plain)

    monkeypatch.setattr(settings, "INTERNAL_DOMAINS_FREE_PRO", False)
    assert not await billing_service.is_pro(internal), "the kill switch still bites"


# --- the refusal that stays reachable (Step 4) ------------------------------------------


@pytest.mark.asyncio
async def test_the_last_decline_names_the_setting_it_needs(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, sprite_mode
):
    """With the plan answer gone, the only refusal left on a self-host is "no runnable
    model" — and it must say which setting is missing rather than repeat the generic
    sentence an operator cannot act on.
    """
    assert not billing_service.billing_enabled()
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", None)
    key, uid = await _register(client)
    await agent_service.get_or_create_curator(uid)
    await client.post(
        "/api/v1/me/pages/new", json={"name": "N", "content": "x"}, headers=_auth(key)
    )

    r = await client.post("/api/v1/me/memory/recompute", headers=_auth(key))

    assert r.status_code == 503
    assert "OPENROUTER_API_KEY" in r.json()["detail"]


@pytest.mark.asyncio
async def test_the_failure_a_scheduled_lane_records_names_the_setting(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, sprite_mode
):
    """The recorded half of the rule. A scheduled lane's console is its agent row,
    and the row carries `str(exception)` — `agent_schedules` hands the run's failure
    straight to `mark_run_failed`. So the exception a run dies with has to name the
    missing setting, or the operator's only record says "not configured".
    """
    assert not billing_service.billing_enabled()
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", None)
    _, uid = await _register(client)

    # run_chat refuses a lane that names no agent row before it touches
    # credentials, so this test supplies the real default-agent id and the
    # OPENROUTER_API_KEY refusal from `agent_auth.resolve` stays the failure.
    default_agent = await agent_service.get_or_create_default(uid)
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        await sprite_agent_service.run_chat(
            uid, "Owner", uid, f"sess-{uuid.uuid4()}", "hi", agent_id=default_agent["id"]
        )
