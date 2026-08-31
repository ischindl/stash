"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import DeveloperGate from "@/components/developer/DeveloperGate";
import { PageHeading, SectionHeading } from "@/components/developer/DocsPrimitives";
import ProjectWikiToggle from "@/components/developer/ProjectWikiToggle";
import SessionUpload from "@/components/SessionUpload";
import CustomSelect from "@/components/CustomSelect";
import {
  assignDeveloperSessions,
  listDeveloperSessions,
  listSessionFolders,
  setDeveloperProjectWiki,
  type DeveloperSession,
  type SessionFolder,
} from "@/lib/api";
import { groupSessionsByProject, projectLabel, type ProjectGroup } from "@/lib/projectGrouping";

export default function DeveloperSessions() {
  return (
    <DeveloperGate>
      <Sessions />
    </DeveloperGate>
  );
}

function Sessions() {
  const router = useRouter();
  const [sessions, setSessions] = useState<DeveloperSession[] | null>(null);
  const [folders, setFolders] = useState<SessionFolder[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  // One fetch of sessions plus one of the project list: a session row already
  // carries its project's name and clearance, so the grouping needs no per-row
  // request. The project list exists only to say what you could file into.
  const load = useCallback(() => {
    Promise.all([listDeveloperSessions(), listSessionFolders()])
      .then(([sessionRes, folderRes]) => {
        setSessions(sessionRes.sessions);
        // The Default folder is the unfiled catch-all and answers no routing
        // decision, so it is never something you can file into or clear.
        setFolders(folderRes.folders.filter((folder) => !folder.is_default));
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load sessions"));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function clearProject(group: ProjectGroup, shareWiki: boolean) {
    if (group.folderId === null) return;
    try {
      await setDeveloperProjectWiki(group.folderId, shareWiki);
      load();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : "Could not change that project");
    }
  }

  async function moveToProject(session: DeveloperSession, folderId: string) {
    try {
      await assignDeveloperSessions([session.id], folderId);
      load();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : "Could not move that session");
    }
  }

  const groups = sessions === null ? [] : groupSessionsByProject(sessions);

  return (
    <>
      <PageHeading title="Sessions">
        Every session your product has recorded, grouped by the project it is filed under and
        newest first inside each group. Rows with no user are the workspace&apos;s own agents —
        mostly the curator reading through what your users said.
      </PageHeading>

      <WikiRoutingExplainer />

      <SessionUpload onUploaded={load} projects={folders} />

      {actionError && <p className="mt-3 text-[13px] text-error">{actionError}</p>}

      {error ? (
        <p className="mt-4 text-[15px] text-error">Couldn&apos;t load sessions: {error}</p>
      ) : sessions === null ? (
        <p className="mt-4 text-[15px] text-muted-foreground">Loading…</p>
      ) : sessions.length === 0 ? (
        <p className="mt-4 rounded border border-dashed border-border px-6 py-10 text-center text-[15px] leading-7 text-muted-foreground">
          No sessions yet. They appear as soon as your backend uploads one.
        </p>
      ) : (
        <div className="mt-4 overflow-x-auto rounded border border-border bg-surface">
          <table className="w-full text-left">
            <thead>
              <tr className="border-b border-border">
                <Th>Session</Th>
                <Th>User</Th>
                <Th>Agent</Th>
                <Th align="right">Events</Th>
                <Th align="right">Last</Th>
                <Th align="right">Project</Th>
              </tr>
            </thead>
            {groups.map((group) => (
              <tbody key={group.key}>
                <GroupHeader group={group} onClear={clearProject} />
                {group.sessions.map((s) => (
                  <tr
                    key={s.id}
                    onClick={() => router.push(`/sessions/${encodeURIComponent(s.session_id)}`)}
                    className="cursor-pointer border-b border-border transition-colors last:border-b-0 hover:bg-raised"
                  >
                    <td className="max-w-[360px] truncate px-4 py-3 text-[14px]">
                      <Link
                        href={`/sessions/${encodeURIComponent(s.session_id)}`}
                        onClick={(e) => e.stopPropagation()}
                        className="text-foreground hover:underline"
                      >
                        {s.title || s.session_id}
                      </Link>
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-[13.5px]">
                      {s.user_id ? (
                        <span className="text-foreground">{s.user_name}</span>
                      ) : (
                        <span className="text-muted-foreground">—</span>
                      )}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 font-mono text-[12px] text-muted-foreground">
                      {s.agent_name || "—"}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-right font-mono text-[12px] text-muted-foreground">
                      {s.event_count}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-right font-mono text-[12px] text-muted-foreground">
                      {formatDate(s.last_event_at)}
                    </td>
                    <td
                      className="whitespace-nowrap px-4 py-3 text-right"
                      onClick={(e) => e.stopPropagation()}
                    >
                      <MoveToProject
                        session={s}
                        label={projectLabel(s)}
                        projects={folders}
                        onMove={moveToProject}
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            ))}
          </table>
        </div>
      )}
    </>
  );
}

/** The routing rules the switches on this page implement, stated once, above them.
 *  Fixed copy: a switch on its own cannot show that a project is one of two gates a
 *  session must clear, nor that every project starts closed. */
function WikiRoutingExplainer() {
  return (
    <section className="mb-8">
      <SectionHeading>What feeds which wiki</SectionHeading>
      <div className="mt-4 rounded border border-border bg-surface px-5 py-4">
        <ul className="max-w-3xl space-y-2.5 text-[13.5px] leading-6 text-dim">
          <li>
            A user&apos;s own wiki always learns from that user&apos;s sessions, and never from
            anyone else&apos;s.
          </li>
          <li>
            The shared wiki is opt-in at two levels: a user who opted out stays out, whatever any
            project says.
          </li>
          <li>
            A project that is <strong className="font-medium text-foreground">OFF</strong>{" "}
            contributes nothing to the shared wiki &mdash; and every project starts OFF.
          </li>
          <li>
            Sessions you file under a project follow that project&apos;s switch. Unfiled sessions
            and Default-folder sessions feed the shared wiki as they always have.
          </li>
          <li>
            Files placed inside the External Wiki folder are already curator-cleared material;
            that channel is unchanged.
          </li>
          <li className="text-muted-foreground">
            Filing a session from this page takes the credentials of whoever set this workspace
            up: a teammate can change a switch here, but cannot move a session.
          </li>
        </ul>
      </div>
    </section>
  );
}

/** One project, its size, and its switch. The Unsorted group gets no switch:
 *  it is not a project and answers no routing decision, so offering one would
 *  imply a clearance that does not exist. */
function GroupHeader({
  group,
  onClear,
}: {
  group: ProjectGroup;
  onClear: (group: ProjectGroup, shareWiki: boolean) => Promise<void>;
}) {
  return (
    <tr className="border-b border-border bg-raised/60">
      <td colSpan={6} className="px-4 py-2">
        <div className="flex flex-wrap items-center gap-3">
          <span className="font-mono text-[11px] font-medium uppercase tracking-[0.14em] text-foreground">
            {group.label}
          </span>
          <span className="font-mono text-[11px] text-muted-foreground">
            {group.sessions.length} session{group.sessions.length === 1 ? "" : "s"}
          </span>
          {group.isProject && (
            <div className="ml-auto">
              <ProjectWikiToggle
                shareWiki={group.sessions[0].session_folder_share_wiki === true}
                projectName={group.label}
                onToggle={(shareWiki) => onClear(group, shareWiki)}
              />
            </div>
          )}
        </div>
      </td>
    </tr>
  );
}

/** Where a session sits today, plus the control to file it under another
 *  project. Today's project is the disabled placeholder rather than a
 *  repeatable option, so a stray click can never look like a change that did
 *  nothing. Unfiling is not offered — the console files, it does not unfile. */
function MoveToProject({
  session,
  label,
  projects,
  onMove,
}: {
  session: DeveloperSession;
  label: string;
  projects: SessionFolder[];
  onMove: (session: DeveloperSession, folderId: string) => Promise<void>;
}) {
  const [moving, setMoving] = useState(false);
  const targets = projects
    .filter((project) => project.id !== session.session_folder_id)
    .map((project) => ({ value: project.id, label: project.name }));

  if (targets.length === 0) {
    return <span className="font-mono text-[11.5px] text-muted-foreground">{label}</span>;
  }

  return (
    <CustomSelect
      ariaLabel={`Move ${session.title || session.session_id} to another project`}
      value=""
      disabled={moving}
      options={[{ value: "", label, disabled: true }, ...targets]}
      onChange={(folderId) => {
        if (!folderId) return;
        setMoving(true);
        void onMove(session, folderId).finally(() => setMoving(false));
      }}
    />
  );
}

function Th({
  children,
  align,
}: {
  children: React.ReactNode;
  align?: "right";
}) {
  return (
    <th
      className={`px-4 py-3 font-mono text-[10px] font-medium uppercase tracking-[0.14em] text-muted-foreground ${
        align === "right" ? "text-right" : ""
      }`}
    >
      {children}
    </th>
  );
}

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}
