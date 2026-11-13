"use client";

import { useCallback, useEffect, useState } from "react";

import { useConfirm } from "@/components/ConfirmDialog";
import {
  createCurator,
  deleteCurator,
  listCurators,
  listModelEndpoints,
  listSessionFolders,
  patchCurator,
  type Curator,
  type ModelEndpoint,
  type SessionFolder,
} from "@/lib/api";
import { curatorAllowanceLine } from "@/lib/curator-allowance";

// A curator made idle has no cron left on its row to restore, so switching its
// schedule back on installs this one — the same nightly hour the server gives a
// new curator. The field beside the switch edits it.
const DEFAULT_NIGHTLY_CRON = "0 3 * * *";

// A model choice names both the box it runs on and the model it serves. Model ids
// contain colons and slashes (`llama3.1:8b`, `vendor/model`), so the two halves are
// joined by a character that cannot appear in either.
const CHOICE_SEP = "⋄";

function pinValue(credentialId: string | null, modelId: string | null): string {
  return `${credentialId ?? ""}${CHOICE_SEP}${modelId ?? ""}`;
}

function decodePin(value: string): { credentialId: string | null; modelId: string | null } {
  const [credentialId, modelId] = value.split(CHOICE_SEP);
  return { credentialId: credentialId || null, modelId: modelId || null };
}

export default function CuratorsSection() {
  const [curators, setCurators] = useState<Curator[]>([]);
  // The account's monthly ceiling, read off the list envelope. `null` is the
  // server's answer for an account that has no cap — not "still loading": the
  // line that renders it lives inside each row, and a row only exists once the
  // fetch that carried this value has resolved.
  const [freeRunsPerMonth, setFreeRunsPerMonth] = useState<number | null>(null);
  const [endpoints, setEndpoints] = useState<ModelEndpoint[]>([]);
  const [folders, setFolders] = useState<SessionFolder[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  // The three reads belong to one screen: the rows, the boxes their pickers offer,
  // and the projects that can still get a curator.
  const refresh = useCallback(() => {
    Promise.all([listCurators(), listModelEndpoints(), listSessionFolders()])
      .then(([curatorList, status, folderRows]) => {
        setCurators(curatorList.curators);
        setFreeRunsPerMonth(curatorList.free_runs_per_month);
        setEndpoints(status.endpoints);
        setFolders(folderRows.folders);
        setLoadError(null);
      })
      .catch((e) => {
        setLoadError(e instanceof Error ? e.message : "Could not load your curators");
      })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => refresh(), [refresh]);

  const curatedFolderIds = curators
    .map((c) => c.curator_folder_id)
    .filter((id): id is string => id !== null);

  return (
    <section className="rounded-2xl border border-border bg-surface p-6 space-y-4">
      <div>
        <h2 className="text-lg font-semibold text-foreground">Curators</h2>
        <p className="text-sm text-muted-foreground mt-1">
          The workspace curator reads everything you do; a project curator reads only that
          project. Each one runs on a model you choose, on a schedule you set.
        </p>
      </div>
      {loading ? (
        <div className="text-sm text-muted-foreground">Loading…</div>
      ) : (
        <div className="space-y-3">
          <ul className="space-y-3">
            {curators.map((curator) => (
              <CuratorRow
                key={curator.id}
                curator={curator}
                endpoints={endpoints}
                freeRunsPerMonth={freeRunsPerMonth}
                onChanged={refresh}
              />
            ))}
          </ul>
          <CreateCurator folders={folders} curatedFolderIds={curatedFolderIds} onCreated={refresh} />
        </div>
      )}
      {loadError && <p className="text-[12px] text-error">{loadError}</p>}
    </section>
  );
}

function titleFor(curator: Curator): string {
  if (curator.curator_folder_id === null) {
    return curator.curator_wiki === "external" ? "Shared-wiki curator" : "Workspace curator";
  }
  // The project behind a curator can be deleted while its curator survives; the row
  // has to say so rather than render a blank name.
  return curator.folder_name ?? "Curator of a deleted project";
}

function CuratorRow({
  curator,
  endpoints,
  freeRunsPerMonth,
  onChanged,
}: {
  curator: Curator;
  endpoints: ModelEndpoint[];
  freeRunsPerMonth: number | null;
  onChanged: () => void;
}) {
  const confirm = useConfirm();
  const [draftCron, setDraftCron] = useState(curator.schedule_cron ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const isFolderCurator = curator.curator_folder_id !== null;
  const isIdle = curator.schedule_cron === null;
  // The beat records why a tick refused to run. A skip for having no model is the
  // one that never fixes itself — it repeats every tick until a model exists — and
  // it is a *resolved* outcome, so the watchdog stays quiet and `last_run_error`
  // stays empty: this row is the only place the block can surface.
  const noModel = !isIdle && curator.last_run_outcome === "skipped_no_credential";
  // A spent allowance is the other self-repeating skip: it resolves with no error
  // row and no watchdog alert, and unlike the missing model it clears by itself —
  // at the start of the next calendar month, which is what the row has to say so
  // the wait is not read as a fault to fix.
  const creditsSpent = !isIdle && curator.last_run_outcome === "skipped_credits";
  const backlog = curator.event_backlog;
  // The account's ceiling beside this row's own worked-run count, or null when
  // there is nothing to state — an uncapped account, or a lane that has charged
  // nothing at all.
  const allowance = curatorAllowanceLine(curator, freeRunsPerMonth);
  // Whether this row's lane resolves a ceiling at all is the server's answer, read
  // once so both branches below speak from the same value.
  const resolvedCeiling = curator.effective_turn_timeout_seconds;

  async function save(patch: Parameters<typeof patchCurator>[1]) {
    setBusy(true);
    setError(null);
    try {
      await patchCurator(curator.id, patch);
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save that change");
    } finally {
      setBusy(false);
    }
  }

  async function chooseModel(value: string) {
    if (value === "") {
      // Clearing all three pins is what returns a curator to inheritance; a patch
      // that merely omits them would leave the stored choice in place.
      await save({ model_provider: null, model_id: null, credential_id: null });
      return;
    }
    const { credentialId, modelId } = decodePin(value);
    await save({ model_provider: "local", credential_id: credentialId, model_id: modelId });
  }

  async function chooseDigest(value: string) {
    if (value === "") {
      await save({ digest_provider: null, digest_model_id: null });
      return;
    }
    await save({ digest_provider: "local", digest_model_id: value });
  }

  async function saveSchedule() {
    await save({ schedule_cron: draftCron.trim() });
  }

  async function toggleIdle() {
    await save({ schedule_cron: isIdle ? draftCron.trim() || DEFAULT_NIGHTLY_CRON : null });
  }

  async function remove() {
    const sure = await confirm({
      title: `Delete ${titleFor(curator)}?`,
      body: "Its wiki pages stay; the curator that wrote them goes.",
      confirmLabel: "Delete curator",
    });
    if (!sure) return;
    setBusy(true);
    setError(null);
    try {
      await deleteCurator(curator.id);
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not delete the curator");
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="rounded-xl border border-border bg-base p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-[14px] font-medium text-foreground">{titleFor(curator)}</span>
            {isIdle ? (
              <span className="rounded-full bg-raised px-2 py-0.5 text-[11px] font-medium text-muted-foreground">
                Idle
              </span>
            ) : noModel ? (
              <span className="rounded-full border border-error/40 bg-error/10 px-2 py-0.5 text-[11px] font-medium text-error">
                No model connected
              </span>
            ) : creditsSpent ? (
              <span className="rounded-full border border-error/40 bg-error/10 px-2 py-0.5 text-[11px] font-medium text-error">
                Allowance spent
              </span>
            ) : (
              curator.next_run_at && (
                <span className="rounded-full bg-raised px-2 py-0.5 text-[11px] font-medium text-muted-foreground">
                  Next run {new Date(curator.next_run_at).toLocaleDateString()}
                </span>
              )
            )}
          </div>
          {noModel && (
            <p className="mt-0.5 text-[12.5px] text-error">
              Every scheduled run is skipping: connect a model under “Cloud agent model” above, or
              set ANTHROPIC_API_KEY on the backend.
            </p>
          )}
          {creditsSpent && (
            <p className="mt-0.5 text-[12.5px] text-error">
              This month’s free runs are used up, so every scheduled run is skipping. The count
              starts again on the first day of next month.
            </p>
          )}
          {isFolderCurator && backlog && (
            <p className="mt-0.5 text-[12.5px] text-muted-foreground">
              {backlog.distinct_events} unread events
            </p>
          )}
          {allowance && (
            <p className="mt-0.5 text-[12.5px] text-muted-foreground">{allowance}</p>
          )}
        </div>
        {isFolderCurator && (
          <button
            type="button"
            onClick={remove}
            disabled={busy}
            className="shrink-0 rounded-md border border-border px-3 py-1.5 text-[12.5px] text-dim hover:text-error disabled:opacity-60"
          >
            Delete
          </button>
        )}
      </div>

      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <ModelPicker
          curator={curator}
          endpoints={endpoints}
          inheritLabel={
            isFolderCurator
              ? "Inherit the default curator"
              : "Default (oldest connected endpoint)"
          }
          onChange={chooseModel}
        />

        {/* The digest is a second model that pre-reads the feed. It runs on local
            boxes only, and the shared-wiki curator refuses it outright: its feed is
            end-user material, and a second processor of it is not this knob's call. */}
        {isFolderCurator && (
          <DigestPicker
            curator={curator}
            endpoints={endpoints}
            onChange={chooseDigest}
          />
        )}
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <label className="flex items-center gap-2 text-[12.5px] text-muted-foreground">
          Schedule
          <input
            value={draftCron}
            onChange={(e) => setDraftCron(e.target.value)}
            placeholder="0 3 * * *"
            disabled={isIdle}
            className="w-32 rounded-md border border-border bg-surface px-2.5 py-1.5 font-mono text-[12.5px] text-foreground disabled:opacity-50"
          />
        </label>
        {!isIdle && (
          <button
            type="button"
            onClick={saveSchedule}
            disabled={busy || !draftCron.trim()}
            className="rounded-md border border-border px-3 py-1.5 text-[12.5px] text-foreground hover:bg-raised disabled:opacity-60"
          >
            Save
          </button>
        )}
        {isFolderCurator && (
          <IdleSwitch checked={isIdle} busy={busy} onToggle={toggleIdle} />
        )}
      </div>

      {/* One curator's own wall, in seconds. Only a lane whose run resolves a
          ceiling is given that number by the server, and only that row carries the
          control, so no row can ever show a number its next run would not use. A
          row whose lane resolves none says what bounds it instead. */}
      {resolvedCeiling === null ? (
        <p className="mt-3 text-[12px] text-muted-foreground">
          This lane has no per-curator ceiling; its run is bounded by the worker’s
          own task limit.
        </p>
      ) : (
        <CeilingControl
          curator={curator}
          resolvedCeiling={resolvedCeiling}
          busy={busy}
          onSave={save}
        />
      )}

      {error && <p className="mt-2 text-[12px] text-error">{error}</p>}
    </li>
  );
}

/** One curator's own wall, in seconds, and the only two writes that move it. The
 *  row mounts this only when the server sent a number for this lane, so every value
 *  here is the server's: the draft starts from the stored ceiling, which is empty
 *  when the deployment default applies, and the number beside it is the one the
 *  next run honours — which is also the bound the run's own lock is keyed to. */
function CeilingControl({
  curator,
  resolvedCeiling,
  busy,
  onSave,
}: {
  curator: Curator;
  resolvedCeiling: number;
  busy: boolean;
  onSave: (patch: Parameters<typeof patchCurator>[1]) => Promise<void>;
}) {
  // Prefilled from the *stored* ceiling, so an unset one renders empty rather than
  // a number the browser invented: empty is what "the deployment default applies"
  // looks like, and the server's own effective value says which number that is.
  const [draftCeiling, setDraftCeiling] = useState(curator.turn_timeout_seconds?.toString() ?? "");
  // A blank or unparsable draft would send 0 or NaN, which the server rightly
  // refuses, so the control says so by leaving its Save disabled instead.
  const ceilingDraft = Number(draftCeiling);
  const ceilingIsUsable =
    draftCeiling.trim() !== "" && Number.isInteger(ceilingDraft) && ceilingDraft >= 1;

  async function saveCeiling() {
    await onSave({ turn_timeout_seconds: Number(draftCeiling) });
  }

  return (
    <div className="mt-3 flex flex-wrap items-center gap-2">
      <label className="flex items-center gap-2 text-[12.5px] text-muted-foreground">
        Run ceiling
        <input
          type="number"
          min={1}
          value={draftCeiling}
          onChange={(e) => setDraftCeiling(e.target.value)}
          placeholder={String(resolvedCeiling)}
          className="w-24 rounded-md border border-border bg-surface px-2.5 py-1.5 text-[12.5px] text-foreground"
        />
      </label>
      <button
        type="button"
        onClick={saveCeiling}
        disabled={busy || !ceilingIsUsable}
        className="rounded-md border border-border px-3 py-1.5 text-[12.5px] text-foreground hover:bg-raised disabled:opacity-60"
      >
        Save ceiling
      </button>
      <button
        type="button"
        onClick={() => onSave({ turn_timeout_seconds: null })}
        disabled={busy || curator.turn_timeout_seconds === null}
        className="rounded-md border border-border px-3 py-1.5 text-[12.5px] text-dim hover:text-foreground disabled:opacity-60"
      >
        Deployment default
      </button>
      <span className="text-[12px] text-muted-foreground">
        seconds one run of this curator may take; its next run uses{" "}
        {resolvedCeiling}
      </span>
    </div>
  );
}

/** The boxes a curator can run on, grouped one per endpoint. The first option is no
 *  choice at all, which the server reads as inheritance. A row whose model was chosen
 *  without a credential runs on the oldest box, so that is the box whose option is
 *  shown as current — picking any option writes the box in explicitly. */
function ModelPicker({
  curator,
  endpoints,
  inheritLabel,
  onChange,
}: {
  curator: Curator;
  endpoints: ModelEndpoint[];
  inheritLabel: string;
  onChange: (value: string) => void;
}) {
  const pinnedBox =
    curator.credential_id !== null
      ? endpoints.find((e) => e.id === curator.credential_id) ?? null
      : endpoints[0] ?? null;
  const served =
    pinnedBox !== null &&
    curator.model_provider !== null &&
    pinnedBox.models.includes(curator.model_id ?? "");
  // A provider pinned with no model inside it is a real stored state (the box's own
  // default answers), and a box can stop serving the model it was chosen for — a
  // reinstall, an `ollama rm`. Either way the row is not inheriting, so it gets an
  // option that says what it is instead of silently reading as inheritance.
  const stored =
    curator.model_provider === null
      ? null
      : served
        ? null
        : curator.model_id ?? "The box's own default model";
  const value = served
    ? pinValue(pinnedBox!.id, curator.model_id)
    : stored !== null
      ? pinValue(curator.credential_id, curator.model_id)
      : "";

  return (
    <label className="flex items-center gap-2 text-[12.5px] text-muted-foreground">
      Model
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="min-w-0 flex-1 rounded-md border border-border bg-surface px-2.5 py-1.5 font-mono text-[12.5px] text-foreground"
      >
        <option value="">{inheritLabel}</option>
        {endpoints.map((endpoint) => (
          <optgroup key={endpoint.id} label={endpoint.name}>
            {endpoint.models.map((model) => (
              <option key={pinValue(endpoint.id, model)} value={pinValue(endpoint.id, model)}>
                {model}
              </option>
            ))}
          </optgroup>
        ))}
        {stored !== null && <option value={value}>{stored} (not on this endpoint)</option>}
      </select>
    </label>
  );
}

/** A local digest inherits the curator row's endpoint pin — there is no separate
 *  digest credential — so the only models worth offering are the ones served by the
 *  box the row pins, or by the oldest box when the row pins none. The box hint sits
 *  outside the label so the control's accessible name stays just "Digest". */
function DigestPicker({
  curator,
  endpoints,
  onChange,
}: {
  curator: Curator;
  endpoints: ModelEndpoint[];
  onChange: (value: string) => void;
}) {
  const box =
    endpoints.find((e) => e.id === curator.credential_id) ?? endpoints[0] ?? null;
  const modelId = curator.digest_model_id;
  const served = box !== null && box.models.includes(modelId ?? "");

  return (
    <div className="flex items-center gap-2 text-[12.5px] text-muted-foreground">
      <label className="flex min-w-0 flex-1 items-center gap-2">
        Digest
        <select
          value={modelId ?? ""}
          onChange={(e) => onChange(e.target.value)}
          className="min-w-0 flex-1 rounded-md border border-border bg-surface px-2.5 py-1.5 font-mono text-[12.5px] text-foreground"
        >
          <option value="">None (single-phase)</option>
          {box &&
            box.models.map((model) => (
              <option key={model} value={model}>
                {model}
              </option>
            ))}
          {modelId !== null && !served && (
            <option value={modelId}>{modelId} (not served by its endpoint any more)</option>
          )}
        </select>
      </label>
      <span className="shrink-0 text-[11.5px]">
        {box ? `on ${box.name}` : "needs an endpoint"}
      </span>
    </div>
  );
}

function IdleSwitch({
  checked,
  busy,
  onToggle,
}: {
  checked: boolean;
  busy: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      onClick={onToggle}
      disabled={busy}
      className="ml-auto flex items-center gap-2 text-[12.5px] text-muted-foreground disabled:opacity-60"
    >
      Idle
      <span
        className={`relative h-5 w-9 rounded-full transition-colors ${
          checked ? "bg-brand" : "bg-border"
        }`}
      >
        <span
          className={`absolute left-0.5 top-0.5 h-4 w-4 rounded-full bg-white transition-transform ${
            checked ? "translate-x-4" : ""
          }`}
        />
      </span>
    </button>
  );
}

function CreateCurator({
  folders,
  curatedFolderIds,
  onCreated,
}: {
  folders: SessionFolder[];
  curatedFolderIds: string[];
  onCreated: () => void;
}) {
  // The Default catch-all holds everything not filed into a project, which is
  // exactly what the workspace curator already reads, so it is not a candidate here.
  const candidates = folders.filter((f) => !f.is_default && !curatedFolderIds.includes(f.id));
  const [folderId, setFolderId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function create() {
    if (!folderId) return;
    setBusy(true);
    setError(null);
    try {
      await createCurator({ folder_id: folderId });
      setFolderId("");
      onCreated();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not create the curator");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rounded-xl border border-border bg-base p-4">
      <div className="flex flex-wrap items-center gap-2">
        <label className="flex min-w-0 flex-1 items-center gap-2 text-[12.5px] text-muted-foreground">
          Project
          <select
            value={folderId}
            onChange={(e) => setFolderId(e.target.value)}
            className="min-w-0 flex-1 rounded-md border border-border bg-surface px-2.5 py-1.5 text-[12.5px] text-foreground"
          >
            <option value="">
              {candidates.length === 0 ? "No projects left to curate" : "Choose a project"}
            </option>
            {candidates.map((folder) => (
              <option key={folder.id} value={folder.id}>
                {folder.name}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          onClick={create}
          disabled={busy || !folderId}
          className="rounded-md bg-brand px-3 py-1.5 text-[12.5px] font-medium text-white disabled:opacity-60"
        >
          Add curator
        </button>
      </div>
      <p className="mt-2 text-[12px] text-muted-foreground">
        A new curator inherits its model and starts on the nightly schedule; make it idle
        afterwards if you only want it on demand.
      </p>
      {error && <p className="mt-2 text-[12px] text-error">{error}</p>}
    </div>
  );
}
