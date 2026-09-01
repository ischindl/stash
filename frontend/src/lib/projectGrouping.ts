/** How the developer console groups its session list by project.

    A project is a non-Default session folder. The Default folder and the sessions
    with no folder at all are not projects — they answer no routing decision and
    fold into one `Unsorted` group at the end of the list, where a row can still
    show the working directory it came from.

    Pure on purpose: it takes the console's session rows and returns groups, so the
    ordering rules below are testable without a render. */

import type { DeveloperSession } from "./api";

export const UNSORTED_LABEL = "Unsorted";

export interface ProjectGroup {
  /** Project groups key on the folder id — folder names are not unique. */
  key: string;
  label: string;
  folderId: string | null;
  isProject: boolean;
  /** Newest first, inherited from the server's ordering. */
  sessions: DeveloperSession[];
}

function isProjectRow(session: DeveloperSession): boolean {
  return session.session_folder_id !== null && session.session_folder_is_default !== true;
}

/** The `cwd` directory name, for an unfiled row that wants to say where it came
 *  from. Windows separators included, because transcripts arrive from any host. */
function cwdBasename(cwd: string | null): string | null {
  if (!cwd) return null;
  const segments = cwd.split(/[/\\]+/).filter(Boolean);
  return segments.length > 0 ? segments[segments.length - 1] : null;
}

/** Where a row says it belongs: its project's name, else the directory it came
 *  from, else `Unsorted`. A named folder always beats `cwd`. */
export function projectLabel(session: DeveloperSession): string {
  if (isProjectRow(session)) return session.session_folder_name ?? UNSORTED_LABEL;
  return cwdBasename(session.cwd) ?? UNSORTED_LABEL;
}

function newestTimestamp(sessions: DeveloperSession[]): number {
  let newest = 0;
  for (const session of sessions) {
    const at = session.last_event_at ? Date.parse(session.last_event_at) : 0;
    if (!Number.isNaN(at) && at > newest) newest = at;
  }
  return newest;
}

export function groupSessionsByProject(sessions: DeveloperSession[]): ProjectGroup[] {
  const groups = new Map<string, ProjectGroup>();

  for (const session of sessions) {
    const project = isProjectRow(session);
    const key = project ? (session.session_folder_id as string) : UNSORTED_LABEL;
    const existing = groups.get(key);
    if (existing) {
      existing.sessions.push(session);
      continue;
    }
    groups.set(key, {
      key,
      label: project ? (session.session_folder_name ?? UNSORTED_LABEL) : UNSORTED_LABEL,
      folderId: project ? (session.session_folder_id as string) : null,
      isProject: project,
      sessions: [session],
    });
  }

  const byNewest = (a: ProjectGroup, b: ProjectGroup) => {
    const delta = newestTimestamp(b.sessions) - newestTimestamp(a.sessions);
    return delta !== 0 ? delta : a.label.localeCompare(b.label);
  };

  // Unsorted sits last however recent its sessions are: it is the catch-all, not
  // a project competing for attention.
  const ordered = [...groups.values()].sort(byNewest);
  return [
    ...ordered.filter((group) => group.isProject),
    ...ordered.filter((group) => !group.isProject),
  ];
}
