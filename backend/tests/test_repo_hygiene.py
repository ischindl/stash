"""Anti-regression guard for the junk that used to live in the repo root.

A clean checkout once shipped an 81 KB agent-browser screenshot named
``--full-page`` — an output-path artifact of the CLI flag of the same name —
beside Celery beat's runtime state: ``celerybeat-schedule`` plus its ``-shm``
and ``-wal`` sidecars. ``git status`` stayed silent about the beat files because
``.gitignore`` carried only the dotted ``.celerybeat-schedule*`` rule while beat's
default cwd-relative names have no leading dot, so nothing matched them and
commit fe3cfec6 committed them. Upstream 2255e1be forward-deleted all four paths
and added the undotted rule; this module keeps that state from rotting back.

``git check-ignore`` is the instrument here, not a file-existence check. The four
names legitimately exist on disk during any real beat run, so on-disk presence
proves nothing; check-ignore instead reports a *tracked* path as not ignored,
which is exactly the state a force-added file puts us back into. One assertion
therefore covers both halves of the original leak: the rule that stopped matching,
and the file that got past it.
"""

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Committed junk that upstream 2255e1be forward-deleted from the repo root.
FORBIDDEN_TRACKED = (
    "--full-page",
    "celerybeat-schedule",
    "celerybeat-schedule-shm",
    "celerybeat-schedule-wal",
)

#: Beat schedule names that must stay ignored: the dotted form ``start.sh`` passes
#: to ``--schedule``, and the three undotted names beat writes by default when it
#: runs inside a checkout.
REQUIRED_IGNORED = (
    ".celerybeat-schedule",
    "celerybeat-schedule",
    "celerybeat-schedule-shm",
    "celerybeat-schedule-wal",
)


def test_root_junk_files_are_not_tracked():
    # The `--` separator is mandatory: `--full-page` would otherwise read as a flag.
    tracked = subprocess.run(
        ["git", "ls-files", "--", *FORBIDDEN_TRACKED],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout

    assert tracked == "", (
        f"These repo-root paths are tracked again:\n{tracked}"
        "Neither belongs in the index: `--full-page` is a screenshot that only "
        "ever supported a PR description (AGENTS.md forbids committing such "
        "assets), and the celerybeat-schedule trio is Celery beat's runtime "
        "state, which beat regenerates on its own. Forward-delete with "
        "`git rm -f -- <path>`; history keeps the old blobs."
    )


def test_beat_schedule_names_are_ignored():
    for name in REQUIRED_IGNORED:
        result = subprocess.run(
            ["git", "check-ignore", "-q", name],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"{name} is not ignored by .gitignore (git check-ignore exited "
            f"{result.returncode}). The original leak was this exact hole: the "
            "ignore file matched only the dotted `.celerybeat-schedule*`, so "
            "beat's undotted names were committed in fe3cfec6. A non-zero exit "
            "also means the path is tracked — check-ignore reports tracked paths "
            "as not ignored — so restore both: the pattern and the deletion."
        )
