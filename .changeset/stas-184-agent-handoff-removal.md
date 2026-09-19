---
"stash": patch
---

Replace the skill page's "Agent Handoff" copy button with the MCP URLs an agent is
actually configured with, in the places people already hand things over.

**What users get:** the folder share dialog carries an "Agent access (MCP)" row with
the folder's own MCP URL and a "Copy agent URL" button, and it reads that URL from the
same `GET /api/v1/share` call that lists the share — no share *action* has to know the
endpoint exists. The skill publish popover shows the public URL next to the "MCP URL for
agents", the panel's toolbar button copies that MCP URL, and a published skill's panel
links to its public page instead of leaving the link inside the popover.

**What was removed:** the "Agent Handoff" affordance in `SkillShareButton` (its
`HandoffStatus` state, `copyAgentHandoffLink()`, and the `agentHandoffUrl()` builder that
pointed at `/api/v1/skills/shared-skill?format=text`). It is replaced outright — no alias,
no second button, no state kept to render the old copy.

**What did not change:** `GET /api/v1/skills/{slug}?format=text` still answers, because the
public skill page advertises its own agent-readable forms (`/skills/<slug>.md` and
`.json`); what went away is the UI that asked a human to hand a *text* URL to an agent.
The MCP servers expose `list` and `read` only, and skill handles are slug-derived with no
new persistence.

**When a folder's agent URL is live:** exactly while `permission_service.get_visibility`
calls the folder public — its own link on, or a skill published over it — because
`share_mcp_service.folder_share_url` asks that same predicate the MCP endpoint gates on. A
folder cannot advertise a URL its own gate would refuse, so the dialog's caption names both
conditions ("… for as long as the folder is open by link or published as a skill").

**Note:** this repo has no tooling that consumes `.changeset/`; `CHANGELOG.md`'s Unreleased
bullet is the authoritative release note. This file exists because the task framework
mandates a removal record for a net-negative change — the founder may delete it.
