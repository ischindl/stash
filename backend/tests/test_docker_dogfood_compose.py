"""The founder's dogfood stack must actually select the dogfood image overlay.

STAS-198 moved the node runtime + pi coding agent out of the shipping image and
into `backend/Dockerfile.dogfood`. Moving it is only half the job: nothing in
the compose layer used to name that overlay, so the dogfood stack built the
pi-free image and `AGENT_EXEC_MODE=local` harness turns shell-ed out to a `pi`
binary that was no longer on PATH. The bug class is a moved artifact whose only
consumer was never repointed — green Dockerfiles and green compose config both
say nothing about it.

Everything is read from the files as plain text, like the sibling release
contract test: no YAML library is a declared dependency and CI installs only
backend/requirements*.txt.

The backend-image service set is DERIVED from `docker-compose.prod.yml` rather
than hardcoded, because `sync-worker` was added upstream after the original plan
was written and a hardcoded list would have silently left that queue on the
pi-free published image while the other four services ran the dogfood tag.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PROD_COMPOSE = REPO_ROOT / "docker-compose.prod.yml"
LOCAL_COMPOSE = REPO_ROOT / "docker-compose.local.yml"
DOGFOOD_COMPOSE = REPO_ROOT / "docker-compose.dogfood.yml"

# A service key is exactly two spaces of indent under the `services:` mapping;
# nested properties are indented deeper and never match.
SERVICE_KEY = re.compile(r"^  ([^\s#][^:]*):")
BACKEND_PIN = re.compile(r"image:\s*ghcr\.io/fergana-labs/stash-backend:\S+")
IMAGE_LINE = re.compile(r"image:\s*(\S+)")


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


def dogfood_blocks() -> dict[str, list[str]]:
    assert DOGFOOD_COMPOSE.exists(), (
        "docker-compose.dogfood.yml must exist: it is the only file that makes the"
        " dogfood stack build backend/Dockerfile.dogfood, and without it the stack"
        " builds the pi-free image with no `pi` on PATH"
    )
    return service_blocks(DOGFOOD_COMPOSE)


def backend_pinned_services() -> set[str]:
    """Services whose self-host image is the published backend build."""
    pinned = {
        name
        for name, body in service_blocks(PROD_COMPOSE).items()
        if any(BACKEND_PIN.search(line) for line in body)
    }
    assert pinned, "docker-compose.prod.yml must pin the backend image somewhere"
    return pinned


def test_dogfood_overlay_declares_no_service_the_self_host_base_lacks():
    """An override may only change services the base file already declares.

    Compose CREATES a service named by an override alone, and a created service
    has neither an image nor a build context, so `up` rejects the whole project.
    The laptop override already carried one such orphan once; a third `-f` file
    is the same hazard and needs the same rule.
    """
    base = set(service_blocks(PROD_COMPOSE))
    assert base, "docker-compose.prod.yml must declare services"
    orphans = set(dogfood_blocks()) - base
    assert orphans == set(), f"dogfood-overlay-only services: {sorted(orphans)}"


def test_backend_builds_the_overlay_with_a_required_base():
    """The dogfood build must name the overlay and refuse an unset base.

    `FROM ${BASE_IMAGE}` in `backend/Dockerfile.dogfood` deliberately has no
    default — an empty default would let the overlay silently build against the
    wrong image — so compose has to supply the value and fail loud when the
    operator did not build the base first. A `:-` default anywhere would
    reintroduce exactly the silent-wrong-base failure the Dockerfile avoids.
    """
    blocks = dogfood_blocks()
    backend = blocks.get("backend")
    assert backend is not None, "docker-compose.dogfood.yml must configure backend"
    assert any(
        re.search(r"dockerfile:\s*backend/Dockerfile\.dogfood$", line.strip()) for line in backend
    ), "backend must build backend/Dockerfile.dogfood, not the pi-free base"
    assert any("BASE_IMAGE" in line and "${BASE_IMAGE:?" in line for line in backend), (
        "the BASE_IMAGE build arg must interpolate ${BASE_IMAGE:?...} (required, no default)"
    )

    text = DOGFOOD_COMPOSE.read_text()
    assert "${BASE_IMAGE:?" in text, "BASE_IMAGE must be a required interpolation"
    assert ":-" not in text, "no compose default is allowed: it would hide an unbuilt base"
    assert "ghcr.io" not in text, "the dogfood file builds locally and must pull no published image"
    assert text.count("build:") == 1, "only backend declares a build section"


def test_every_backend_image_service_repoints_to_one_local_tag():
    """Every consumer of the backend image must run the same dogfood build.

    backend, worker, heavy-worker, sync-worker and beat all read the same queue
    and the same code. If the overlay repoints only some of them, the missed
    queue silently runs pi-free code inside the founder's own stack — the
    service set is derived from the prod file so a newly added backend-image
    service makes this test fail instead of stranding a lane.
    """
    blocks = dogfood_blocks()
    derived = backend_pinned_services()
    assert "backend" in derived, "the prod file must pin backend's image"
    assert set(blocks) == derived, (
        "the dogfood overlay must repoint exactly the backend-image services"
        f" {sorted(derived)}, not {sorted(set(blocks))}"
    )

    tags = set()
    for name, body in blocks.items():
        images = IMAGE_LINE.findall("\n".join(body))
        assert len(images) == 1, f"{name} must carry exactly one image, found {len(images)}"
        tags.add(images[0])

    assert len(tags) == 1, f"all backend-image services must share one tag, got {sorted(tags)}"
    tag = tags.pop()
    assert "ghcr.io" not in tag, f"dogfood tag must be a local build, not a pull: {tag}"


def test_self_host_files_stay_pull_only():
    """The published self-host path must never depend on a local build.

    Third parties follow the two-file command in the README against GHCR images.
    A dogfood or BASE_IMAGE reference in either file would make that published
    path demand a locally built base image that a self-hoster does not have.
    """
    for path in (PROD_COMPOSE, LOCAL_COMPOSE):
        text = compose_text(path)
        assert "Dockerfile.dogfood" not in text, f"{path.name} must stay pull-only"
        assert "BASE_IMAGE" not in text, f"{path.name} must not require a local base build"


def test_no_dev_only_service_or_port_in_any_compose_file():
    """A dev-only lane must not creep back into the shipped compose contract.

    The `collab` service and its 3458 port were removed because a service no
    self-host image provides breaks `up` for every reader of the published
    command; the guard names both so neither returns through a new overlay file.
    """
    for path in (PROD_COMPOSE, LOCAL_COMPOSE, DOGFOOD_COMPOSE):
        text = compose_text(path)
        assert "collab" not in set(service_blocks(path)), f"{path.name} declares a collab service"
        assert "3458" not in text, f"{path.name} maps the removed dev port 3458"
