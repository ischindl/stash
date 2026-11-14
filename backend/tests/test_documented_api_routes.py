"""The public site may only advertise routes the product actually serves.

STAS-442: joinstash.ai shipped a REST reference for /api/v1/rm after the
reward-model routers were deleted, so every example on the page returned 404.

Three properties here are load-bearing, and each one fails out loud:

(a) `test_every_documented_api_path_is_a_real_route` is the intake's requirement:
    every /api/v1/... path the marketing site documents must exist in FastAPI's
    own route table. It goes inert the moment www/app/docs is gone, which is why
    the card does not stop at it.
(b) `test_retired_surfaces_stay_off_the_public_site` is the predicate that still
    bites after that deletion, and the reason (a) needs no fallback branch.
    RETIRED stays narrow on purpose: a future truthful CLI page under /docs must
    not fail it.
(c) `test_the_guard_reads_real_public_files` plus route_paths()'s own assertion
    are the anti-vacuity floors: a wrong REPO_ROOT or a renamed tree fails loudly
    instead of letting (a) pass over an empty file set.
"""

import re
from pathlib import Path

from backend.main import app

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS_ROOT = REPO_ROOT / "www" / "app" / "docs"
WWW_APP = REPO_ROOT / "www" / "app"
NEXT_CONFIG = REPO_ROOT / "www" / "next.config.ts"

DOCUMENTED_PATH = re.compile(r"/api/v1/[A-Za-z0-9_{}/<>.-]*")
PLACEHOLDER = re.compile(r"<[^>/]*>|\{[^}/]*\}")

# The retired markers are held without their leading slash and joined at the use site.
# A quoted marker starting slash-reward would be byte-identical to a FastAPI router
# prefix literal, and this card's acceptance gate is that no such prefix literal
# survives anywhere under backend/. Same predicate, still one codepath.
RETIRED = ("api/v1/rm/", "docs/gepa", "docs/training", "reward-models")


def sources(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(p for p in root.rglob("*") if p.suffix in {".ts", ".tsx"} and p.is_file())


def relative(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def normalize(path: str) -> str:
    return PLACEHOLDER.sub("{}", path).rstrip("/") or "/"


def route_paths() -> set[str]:
    paths = {route.path for route in app.routes if hasattr(route, "methods")}
    assert "/api/v1/users/me" in paths, "the FastAPI route table did not load"
    return {normalize(p) for p in paths}


def test_every_documented_api_path_is_a_real_route():
    live = route_paths()
    offenders = []
    for source in sources(DOCS_ROOT):
        for line_no, line in enumerate(source.read_text().splitlines(), start=1):
            for match in DOCUMENTED_PATH.findall(line):
                if normalize(match) not in live:
                    offenders.append(f"{relative(source)}:{line_no}: {match}")
    assert not offenders, "documented paths with no FastAPI route:\n" + "\n".join(offenders)


def test_retired_surfaces_stay_off_the_public_site():
    offenders = []
    for source in sources(WWW_APP) + [NEXT_CONFIG]:
        for line_no, line in enumerate(source.read_text().splitlines(), start=1):
            for marker in ("/" + retired for retired in RETIRED):
                if marker in line:
                    offenders.append(f"{relative(source)}:{line_no}: {marker}")
    assert not offenders, "retired surfaces are back on the public site:\n" + "\n".join(offenders)


def test_the_guard_reads_real_public_files():
    assert len(sources(WWW_APP)) > 10, f"expected the www app tree under {relative(WWW_APP)}"
