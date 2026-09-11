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
  const [endpoints, setEndpoints] = useState<ModelEndpoint[]>([]);
  const [folders, setFolders] = useState<SessionFolder[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  // The three reads belong to one screen: the rows, the boxes their pickers offer,
  // and the projects that can still get a curator.
  const refresh = useCallback(() => {
    Promise.all([listCurators(), listModelEndpoints(), listSessionFolders()])
      .then(([curatorRows, status, folderRows]) => {
        setCurators(curatorRows);
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
  onChanged,
}: {
  curator: Curator;
  endpoints: ModelEndpoint[];
  onChanged: () => void;
}) {
  const confirm = useConfirm();
  const [draftCron, setDraftCron] = useState(curator.schedule_cron ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const isFolderCurator = curator.curator_folder_id !== null;
  const isIdle = curator.schedule_cron === null;
  const backlog = curator.event_backlog;

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
            ) : (
              curator.next_run_at && (
                <span className="rounded-full bg-raised px-2 py-0.5 text-[11px] font-medium text-muted-foreground">
                  Next run {new Date(curator.next_run_at).toLocaleDateString()}
                </span>
              )
            )}
          </div>
          {isFolderCurator && backlog && (
            <p className="mt-0.5 text-[12.5px] text-muted-foreground">
              {backlog.distinct_events} unread events
            </p>
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

      {error && <p className="mt-2 text-[12px] text-error">{error}</p>}
    </li>
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
