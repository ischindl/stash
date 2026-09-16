"""The shipping backend image must be structurally pi-free.

Operator directive 2026-08-28, reaffirmed by the STAS-198 rework ruling of
2026-09-11: "The Docker image must not install pi at all. pi is not for
self-hosting." The node runtime and the pi coding agent belong in the
dogfood-only overlay Dockerfile that FROMs this pi-free build, not here.

Green must therefore come from MOVING the node+pi RUN block into the dogfood
overlay (backend/Dockerfile.dogfood) — never from deleting pi outright, which
would strand local exec mode with no buildable path. The /opt/hf model and
CPU-torch bakes are domain requirements and stay in this file (CEO ruling
2026-09-16: "nemixaj ich s pi bake").

A broad `pi|npm|node` alternation can never return zero: it matches `pip
install`, `libatspi2.0-0`, and the node runtime the image legitimately needs.
The ruling's own pattern names only real pi artifacts, and `/bin/pi` is added
here because that pattern looks for `/pi/` while the image symlinks the binary
as `/usr/local/bin/pi` — no trailing slash, so the symlink would otherwise be
left behind by a move that only relocated the install line.
"""

import re
from pathlib import Path

BACKEND_DOCKERFILE = Path(__file__).resolve().parents[2] / "backend" / "Dockerfile"

PI_REFERENCE = re.compile(
    r"earendil-works/pi|/pi/|pi install|pi-coding-agent|/bin/pi", re.IGNORECASE
)


def test_backend_dockerfile_installs_no_pi():
    text = BACKEND_DOCKERFILE.read_text()
    offenders = [line for line in text.splitlines() if PI_REFERENCE.search(line)]
    assert offenders == [], f"backend/Dockerfile references pi: {offenders}"
