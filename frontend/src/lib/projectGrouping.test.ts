/** The console groups its session list by project, and every rule here is one the
 *  routing decision depends on: the Default folder is not a project (it answers no
 *  routing decision), folder names repeat so ids are the only safe key, and an
 *  unfiled row's `cwd` is a display hint — never a project. */

import { describe, expect, it } from "vitest";

import type { DeveloperSession } from "./api";
import { groupSessionsByProject, projectLabel } from "./projectGrouping";

function session(overrides: Partial<DeveloperSession> = {}): DeveloperSession {
  return {
    id: "row-1",
    session_id: "s-1",
    agent_name: "claude",
    title: "Session",
    cwd: null,
    event_count: 3,
    started_at: "2026-01-01T00:00:00Z",
    last_event_at: "2026-01-02T00:00:00Z",
    user_id: null,
    user_name: null,
    user_external_id: null,
    session_folder_id: null,
    session_folder_name: null,
    session_folder_is_default: null,
    session_folder_share_wiki: null,
    ...overrides,
  };
}

function project(overrides: Partial<DeveloperSession> = {}): Partial<DeveloperSession> {
  return {
    session_folder_id: "folder-a",
    session_folder_name: "acme-diesel",
    session_folder_is_default: false,
    session_folder_share_wiki: true,
    ...overrides,
  };
}

describe("projectLabel", () => {
  it("prefers a named folder over the cwd", () => {
    const row = session(project({ cwd: "/srv/payments-api" }));
    expect(projectLabel(row)).toBe("acme-diesel");
  });

  it("treats the Default folder as no project at all", () => {
    const row = session({
      session_folder_id: "folder-default",
      session_folder_name: "Default",
      session_folder_is_default: true,
      cwd: "/srv/payments-api",
    });
    expect(projectLabel(row)).toBe("payments-api");
  });

  it("shows the cwd basename for an unfiled session", () => {
    expect(projectLabel(session({ cwd: "/srv/payments-api" }))).toBe("payments-api");
  });

  it("falls back to Unsorted with neither a project nor a cwd", () => {
    expect(projectLabel(session({ cwd: null }))).toBe("Unsorted");
  });
});

describe("groupSessionsByProject", () => {
  it("returns no groups for no sessions", () => {
    expect(groupSessionsByProject([])).toEqual([]);
  });

  it("keeps two folders that share a name as two groups", () => {
    const groups = groupSessionsByProject([
      session(project({ id: "r1", session_folder_id: "folder-a", session_folder_name: "acme" })),
      session(project({ id: "r2", session_folder_id: "folder-b", session_folder_name: "acme" })),
    ]);

    expect(groups).toHaveLength(2);
    expect(groups.map((group) => group.key)).toEqual(["folder-a", "folder-b"]);
    expect(groups.map((group) => group.folderId)).toEqual(["folder-a", "folder-b"]);
  });

  it("folds unfiled and Default sessions into one trailing Unsorted group", () => {
    const groups = groupSessionsByProject([
      session({ id: "r1", last_event_at: "2026-03-03T00:00:00Z" }),
      session(
        project({
          id: "r2",
          session_folder_id: "folder-a",
          session_folder_name: "acme",
          session_folder_is_default: false,
          last_event_at: "2026-01-01T00:00:00Z",
        })
      ),
      session({
        id: "r3",
        session_folder_id: "folder-default",
        session_folder_name: "Default",
        session_folder_is_default: true,
        last_event_at: "2026-02-02T00:00:00Z",
      }),
    ]);

    expect(groups.map((group) => group.label)).toEqual(["acme", "Unsorted"]);
    const unsorted = groups[groups.length - 1];
    expect(unsorted.isProject).toBe(false);
    expect(unsorted.folderId).toBeNull();
    expect(unsorted.sessions.map((row) => row.id)).toEqual(["r1", "r3"]);
  });

  it("orders projects by their newest session", () => {
    const groups = groupSessionsByProject([
      session(
        project({
          id: "r1",
          session_folder_id: "folder-old",
          session_folder_name: "old-project",
          last_event_at: "2026-01-01T00:00:00Z",
        })
      ),
      session(
        project({
          id: "r2",
          session_folder_id: "folder-new",
          session_folder_name: "new-project",
          last_event_at: "2026-05-05T00:00:00Z",
        })
      ),
      session(project({ id: "r3", session_folder_id: "folder-new", last_event_at: null })),
    ]);

    expect(groups.map((group) => group.label)).toEqual(["new-project", "old-project"]);
    expect(groups[0].sessions.map((row) => row.id)).toEqual(["r2", "r3"]);
  });
});
