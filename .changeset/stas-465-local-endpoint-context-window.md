"stash": patch
---

Make the context window of a local model endpoint a number its owner states for that one
box, instead of a number Stash shipped for every box.

**What users get:** the Settings form for a local endpoint now asks how large that
server's context window is, as part of adding the box (the "Test connection" button does
not dial until the field holds a whole number of tokens), and each connected box shows
its own number on its row with a "Save size" control to change it later. A resize writes
the size and nothing else: the server copies the address, model and key out of the stored
credential, so the control that resizes a box cannot move which box is addressed. Two
boxes can hold two different sizes, and nothing about a box is inferred from its model
name.

**What was removed:** the hardcoded pair in `_synthesized_models_json` (`contextWindow:
131072`, `maxTokens: 8192`) that every local model entry carried whatever the box actually
was. `maxTokens` stops being an independent constant too: it is derived as the smaller of
the shipped output ceiling (`OUTPUT_CEILING_TOKENS`, still 8192) and the stated window
minus the room an answer is refused to reasoning (`ANSWER_ROOM_TOKENS`, 1024). No window
default survives in the service layer: a credential document with no `context_window`
refuses the turn, naming migration 0275 and the same sentence the connect form answers
with, because a box that lost its size would be asked for output it cannot serve.

**What migration `0275` does:** existing local endpoints keep working without being
reconnected. A row whose owner never stored a `models.json` is stamped with the window
Stash itself used to claim about every box, which is the size those rows were already
being told. A row that stores its own `models.json` is stamped with the `contextWindow`
that file already declares for its local model, so the backfill matches the bytes `pi` was
already reading. A row whose file cannot be read, or whose stamped window sits below the
floor the service enforces (2048 tokens), refuses the upgrade naming its own id rather
than being clamped to a guess. A row that already carries a window is left as written, and
a database with no local endpoint never opens the keyring. The downgrade is a deliberate
no-op: old code ignores the extra key, so rolling back must not destroy a size a user
stated.

**What did not change:** what a turn asks for is still bounded by 8192 output tokens, so a
box that was healthy under the old pair stays healthy. The thinking-level control, the
`models.json` override path (a window edit is refused while a verbatim override is stored,
because those bytes and not this number are what the next turn writes), the probe that
dials a box before it is saved, and `backend/services/harness.py`'s account of what `pi`
does with a ceiling are untouched. Nothing here re-measures a live box: this change's
evidence is the source tree and its tests, and STAS-455's task document
`step2-ceiling-decision` is cited by card and key rather than repeated as a measurement
this run took.

**Note:** this repo has no tooling that consumes `.changeset/`; `CHANGELOG.md`'s Unreleased
bullet is the authoritative release note. This file is the removal record the task
framework mandates for taking a shipped constant out of the code.
