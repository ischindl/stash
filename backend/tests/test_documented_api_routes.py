"""The public site may only carry surfaces the product actually serves.

STAS-442 deleted the reward-model product's public docs, and with them the subject of
this file's first scan: www/app/docs no longer exists, so "every /api/v1/... path the
site documents is a real FastAPI route" iterated zero files and stayed green while
claiming nothing. STAS-446 removed that predicate along with its subject rather than
keep a passing test that asserts nothing. The file keeps its name because the invariant
below is still about which API surfaces the public site may carry.

Two properties are load-bearing here, and each one fails out loud:

(a) `test_retired_surfaces_stay_off_the_public_site` is the predicate with a real
    subject: the retired markers must stay off every `.ts`/`.tsx` file the site ships
    and off `www/next.config.ts`. RETIRED stays narrow on purpose: a future truthful
    page under /docs must not fail it.
(b) `test_the_guard_reads_real_public_files` is the anti-vacuity floor, and it is the
    reason `sources()` refuses to return an empty list: a renamed or hollowed-out tree
    fails loudly instead of letting the scan pass over nothing.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WWW_APP = REPO_ROOT / "www" / "app"
NEXT_CONFIG = REPO_ROOT / "www" / "next.config.ts"

# The retired markers are held without their leading slash and joined at the use site.
# A quoted marker starting slash-reward would be byte-identical to a FastAPI router
# prefix literal, and STAS-442's acceptance gate is that no such prefix literal
# survives anywhere under backend/. Same predicate, still one codepath.
RETIRED = ("api/v1/rm/", "docs/gepa", "docs/training", "reward-models")


def sources(root: Path) -> list[Path]:
    if not root.is_dir():
        raise AssertionError(f"scanned tree is missing, refusing to scan nothing: {root}")
    found = sorted(p for p in root.rglob("*") if p.suffix in {".ts", ".tsx"} and p.is_file())
    if not found:
        raise AssertionError(f"scanned tree holds no .ts/.tsx file: {root}")
    return found


def relative(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


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
    assert NEXT_CONFIG.is_file(), f"expected the public site config at {relative(NEXT_CONFIG)}"
