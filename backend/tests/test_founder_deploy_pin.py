"""What the founder stack runs must be one tracked commit, not folklore in one checkout.

Which images the founder's own stack boots used to live in two untracked files at
the root of one checkout (`.stas270-cutover-override.yml`, `.founder-11e0644a-pin.yml`).
An untracked pin is invisible to every other worktree and unreadable by any test, so
"what is deployed" could not be answered from the repo at all, and the lanes could
drift apart without anything noticing. They had: measured 2026-11-12, the four running
backend lanes were `stash-backend:main-11e0644a-pi` while `stash-frontend-1` was
`stash-frontend:main-db223464`, and `docker inspect` showed the two containers had been
created from different `-f` lists entirely.

The cost of a pair that names two commits is measured in `.fusion/memory/2026-09-22.md`:
commit `3da5aecf` added `probeSavedEndpoint()` in `frontend/src/lib/api.ts` against
`POST /api/v1/me/agent-credentials/endpoints/{credential_id}/test`, so a frontend-only
deploy would have put a Test button in front of the founder that answers 404, because
the backend image then running predated the route.

Like the two sibling compose tests, everything here is read from the files as plain
text: no YAML library is a declared dependency and CI installs only
backend/requirements*.txt. The backend service set is DERIVED from
`docker-compose.prod.yml` rather than hardcoded, so a queue added upstream cannot be
stranded on a different commit than the lanes that share its code and its queue.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PROD_COMPOSE = REPO_ROOT / "docker-compose.prod.yml"
FOUNDER_COMPOSE = REPO_ROOT / "docker-compose.founder.yml"

# The published self-host contract. Neither the founder file nor a local commit tag
# may appear in any of them: third parties follow the README's pull-only command.
PUBLISHED_COMPOSE = (
    PROD_COMPOSE,
    REPO_ROOT / "docker-compose.local.yml",
    REPO_ROOT / "docker-compose.dogfood.yml",
)

# A service key is exactly two spaces of indent under the `services:` mapping;
# nested properties are indented deeper and never match.
SERVICE_KEY = re.compile(r"^  ([^\s#][^:]*):")
BACKEND_PIN = re.compile(r"image:\s*ghcr\.io/fergana-labs/stash-backend:\S+")
IMAGE_LINE = re.compile(r"image:\s*(\S+)")
BACKEND_TAG = re.compile(r"^stash-backend:main-([0-9a-f]{8})-pi$")
FRONTEND_TAG = re.compile(r"^stash-frontend:main-([0-9a-f]{8})$")


def compose_text(path: Path) -> str:
    """Read a shipped compose file, failing with the contract it carries."""
    assert path.exists(), f"{path.name} must exist for the compose contract to hold"
    return path.read_text()


def service_blocks(path: Path) -> dict[str, list[str]]:
    """Each service key mapped to its body lines, read as plain text."""
    lines = compose_text(path).splitlines()
    if "services:" not in lines:
        return {}
    blocks: dict[str, list[str]] = {}
    body: list[str] | None = None
    for line in lines[lines.index("services:") + 1 :]:
        if line.strip() and not line.startswith(" "):
            break  # left the services: mapping (e.g. `volumes:`)
        key = SERVICE_KEY.match(line)
        if key:
            body = []
            blocks[key.group(1)] = body
            continue
        if body is not None:
            body.append(line)
    return blocks


def founder_blocks() -> dict[str, list[str]]:
    assert FOUNDER_COMPOSE.exists(), (
        "docker-compose.founder.yml must exist and be tracked: it is the only record"
        " of which commit the founder stack is pinned to, and while that record lived"
        " in untracked root files no other worktree could see it and no test could read it"
    )
    return service_blocks(FOUNDER_COMPOSE)


def backend_pinned_services() -> set[str]:
    """Services whose self-host image is the published backend build."""
    pinned = {
        name
        for name, body in service_blocks(PROD_COMPOSE).items()
        if any(BACKEND_PIN.search(line) for line in body)
    }
    assert pinned, "docker-compose.prod.yml must pin the backend image somewhere"
    return pinned


def single_image(block: list[str], service: str) -> str:
    images = IMAGE_LINE.findall("\n".join(block))
    assert len(images) == 1, f"{service} must carry exactly one image line, found {len(images)}"
    return images[0]


def test_founder_overlay_declares_no_service_the_self_host_base_lacks():
    """Compose CREATES a service named by an override alone, with no image and no

    build context, so `up` rejects the whole project. The prod file already lost a
    stack to an orphan service once (`collab`), and a fourth `-f` file is the same
    hazard: it may retag services the shipped contract defines and nothing else.
    """
    base = set(service_blocks(PROD_COMPOSE))
    assert base, "docker-compose.prod.yml must declare services"
    orphans = set(founder_blocks()) - base
    assert orphans == set(), f"founder-pin-only services: {sorted(orphans)}"


def test_founder_pin_covers_every_backend_lane_plus_the_frontend():
    """The pin must name every consumer of the backend image, and only those.

    The running stack today starts four of these lanes, not six, but a lane started
    later still has to come up on the pinned commit: a queue left out of the pin
    silently boots the published GHCR tag, which is a different build of the same
    code reading the same queue. The set is derived, so a queue added upstream
    reddens this test instead of being stranded. The frontend belongs in the pin
    because the dogfood overlay does not name it, and an unpinned frontend falls
    back to the published tag mid-deploy.
    """
    derived = backend_pinned_services()
    assert "backend" in derived, "the prod file must pin backend's image"
    expected = derived | {"frontend"}
    assert set(founder_blocks()) == expected, (
        "the founder pin must name exactly the backend-pinned services"
        f" {sorted(derived)} plus frontend, not {sorted(set(founder_blocks()))}"
    )


def test_every_pinned_lane_names_a_local_commit_tag():
    """Every pin is a local build of one commit, never a pull and never a default.

    The tags carry the commit they were built from because the images are built by
    the runbook from a detached worktree, not published to a registry: a `ghcr.io`
    tag here would mix the founder's own stack into the hosted release line, and a
    `${VAR:-default}` interpolation would let a lane boot a build nobody chose,
    which is the failure `docker-compose.dogfood.yml` deliberately refuses by
    carrying `BASE_IMAGE` with no default.
    """
    blocks = founder_blocks()
    text = FOUNDER_COMPOSE.read_text()
    assert "ghcr.io" not in text, "the founder pin builds locally and must pull no published image"
    assert "${" not in text and ":-" not in text, (
        "the founder pin must be literal: an interpolation or default would let a lane boot an image nobody pinned"
    )
    assert "build:" not in text, "the founder pin selects tags; the runbook builds them"

    for name, body in blocks.items():
        tag = single_image(body, name)
        pattern = FRONTEND_TAG if name == "frontend" else BACKEND_TAG
        assert pattern.match(tag), (
            f"{name} is pinned to {tag!r}, which does not name a commit as"
            f" {'stash-frontend:main-<8hex>' if name == 'frontend' else 'stash-backend:main-<8hex>-pi'}"
        )


def test_all_backend_lanes_share_one_tag():
    """backend, worker, heavy-worker, sync-worker, reward-worker and beat run the same

    code against the same queues and the same database. Splitting them across builds
    is the bug the dogfood overlay's own test guards: the missed queue keeps
    consuming live work with stale rules and nothing errors anywhere.
    """
    tags = {
        single_image(body, name) for name, body in founder_blocks().items() if name != "frontend"
    }
    assert len(tags) == 1, f"all backend lanes must share one tag, got {sorted(tags)}"


def test_the_backend_tag_and_the_frontend_tag_name_the_same_commit():
    """The skew tripwire: one deploy is one commit for the whole pair.

    This is the assertion that would have stopped the incident recorded in
    `.fusion/memory/2026-09-22.md`. A frontend built ahead of the backend ships UI
    that calls routes the running API does not have, and the founder sees a button
    that fails; a frontend behind it hides work that is already live. Both images
    carry the same 8-hex commit precisely so this comparison is possible, and the
    only thing that can legitimately differ is the `-pi` suffix the pi harness adds
    to the backend build.
    """
    blocks = founder_blocks()
    backend_hexes = {
        m.group(1)
        for name, body in blocks.items()
        if name != "frontend" and (m := BACKEND_TAG.match(single_image(body, name)))
    }
    assert backend_hexes, "every backend lane must carry a parseable commit tag"
    assert len(backend_hexes) == 1, f"backend lanes disagree on the commit: {sorted(backend_hexes)}"
    backend_hex = backend_hexes.pop()

    frontend_tag = single_image(blocks["frontend"], "frontend")
    match = FRONTEND_TAG.match(frontend_tag)
    assert match, f"frontend is pinned to {frontend_tag!r}, which names no commit"
    assert match.group(1) == backend_hex, (
        f"the deployed pair names two commits: backend {backend_hex}, frontend"
        f" {match.group(1)}. One deploy is one commit; see the Test-button 404"
        " recorded in .fusion/memory/2026-09-22.md"
    )


def test_published_self_host_files_never_mention_the_founder_pin_or_a_commit_tag():
    """The public self-host path stays pull-only and free of our own stack's folklore.

    Third parties copy the two-file command from the README against GHCR images.
    A reference to the founder pin, or a `main-<sha>` tag, in any published file
    would make the documented path demand a locally built image that no self-hoster
    has, exactly as the dogfood overlay's own pull-only test protects against.
    """
    for path in PUBLISHED_COMPOSE:
        text = compose_text(path)
        assert "founder" not in text, f"{path.name} must not reference the founder pin"
        assert not re.search(r"main-[0-9a-f]{7,}", text), (
            f"{path.name} carries a commit-built tag; the published path ships version tags only"
        )
