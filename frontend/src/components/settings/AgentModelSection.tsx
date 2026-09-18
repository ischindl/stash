"use client";

import { useCallback, useEffect, useState } from "react";

import { useConfirm } from "@/components/ConfirmDialog";
import {
  ApiError,
  connectAgentKey,
  connectLocalEndpoint,
  deleteLocalEndpoint,
  disconnectAgentCredential,
  finishAgentOAuth,
  listModelEndpoints,
  probeLocalEndpoint,
  PROBE_ONLY_MODEL,
  startAgentOAuth,
  type LocalEndpointDoc,
  type ModelEndpoint,
} from "@/lib/api";

// The provider a user connects for their cloud agent. Claude and Codex support
// OAuth (sign in with your subscription) or an API key; OpenRouter is key-only.
// A local box is not one of these cards any more: an account can connect several,
// and agents pin them individually, so they get their own list below.
type Provider = {
  id: string;
  label: string;
  blurb: string;
  oauth: boolean;
  keyHint: string;
};

const PROVIDERS: Provider[] = [
  {
    id: "anthropic",
    label: "Claude Code",
    blurb: "Sign in with your Claude subscription, or paste an Anthropic API key.",
    oauth: true,
    keyHint: "sk-ant-…",
  },
  {
    id: "openai",
    label: "Codex",
    blurb: "Sign in with ChatGPT, or paste an OpenAI API key.",
    oauth: true,
    keyHint: "sk-…",
  },
  {
    id: "openrouter",
    label: "OpenRouter",
    blurb: "Run any model on your own OpenRouter key.",
    oauth: false,
    keyHint: "sk-or-…",
  },
];

export default function AgentModelSection() {
  const [connected, setConnected] = useState<string[]>([]);
  const [endpoints, setEndpoints] = useState<ModelEndpoint[]>([]);
  const [local, setLocal] = useState<LocalEndpointDoc | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    listModelEndpoints()
      .then((status) => {
        setConnected(status.connected);
        setEndpoints(status.endpoints);
        setLocal(status.local);
        setLoadError(null);
      })
      .catch((e) => {
        setLoadError(e instanceof Error ? e.message : "Could not load your model connections");
      })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => refresh(), [refresh]);

  return (
    <section className="rounded-2xl border border-border bg-surface p-6 space-y-4">
      <div>
        <h2 className="text-lg font-semibold text-foreground">Cloud agent model</h2>
        <p className="text-sm text-muted-foreground mt-1">
          Connect Claude, Codex, OpenRouter, or a local model to run the agent on your own
          account. Pro members without a connection use the managed agent (OpenRouter
          GLM&nbsp;5.2).
        </p>
      </div>
      {loading ? (
        <div className="text-sm text-muted-foreground">Loading…</div>
      ) : (
        <div className="space-y-3">
          {PROVIDERS.map((p) => (
            <ProviderRow
              key={p.id}
              provider={p}
              connected={connected.includes(p.id)}
              onChange={setConnected}
            />
          ))}
          <EndpointsPanel endpoints={endpoints} local={local} onChanged={refresh} />
        </div>
      )}
      {loadError && <p className="text-[12px] text-error">{loadError}</p>}
    </section>
  );
}

function ProviderRow({
  provider,
  connected,
  onChange,
}: {
  provider: Provider;
  connected: boolean;
  onChange: (c: string[]) => void;
}) {
  const [mode, setMode] = useState<"idle" | "key" | "oauth">("idle");
  const [apiKey, setApiKey] = useState("");
  const [oauthState, setOauthState] = useState<string | null>(null);
  const [pasted, setPasted] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function disconnect() {
    setBusy(true);
    try {
      onChange(await disconnectAgentCredential(provider.id));
    } finally {
      setBusy(false);
    }
  }

  async function saveKey() {
    setBusy(true);
    setError(null);
    try {
      onChange(await connectAgentKey(provider.id, apiKey));
      setMode("idle");
      setApiKey("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save key");
    } finally {
      setBusy(false);
    }
  }

  async function beginOAuth() {
    setBusy(true);
    setError(null);
    try {
      const { authorize_url, state } = await startAgentOAuth(provider.id);
      setOauthState(state);
      setMode("oauth");
      window.open(authorize_url, "_blank", "noopener,noreferrer");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not start sign-in");
    } finally {
      setBusy(false);
    }
  }

  async function finishOAuth() {
    if (!oauthState) return;
    setBusy(true);
    setError(null);
    try {
      onChange(await finishAgentOAuth(provider.id, pasted, oauthState));
      setMode("idle");
      setPasted("");
      setOauthState(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not complete sign-in");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rounded-xl border border-border bg-base p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-[14px] font-medium text-foreground">{provider.label}</span>
            {connected && (
              <span className="rounded-full bg-[var(--color-success)]/15 px-2 py-0.5 text-[11px] font-medium text-[var(--color-success)]">
                Connected
              </span>
            )}
          </div>
          <p className="mt-0.5 text-[12.5px] text-muted-foreground">{provider.blurb}</p>
        </div>
        {connected ? (
          <button
            type="button"
            onClick={disconnect}
            disabled={busy}
            className="shrink-0 rounded-md border border-border px-3 py-1.5 text-[12.5px] text-dim hover:text-error"
          >
            Disconnect
          </button>
        ) : (
          <div className="flex shrink-0 gap-2">
            {provider.oauth && (
              <button
                type="button"
                onClick={beginOAuth}
                disabled={busy}
                className="rounded-md bg-brand px-3 py-1.5 text-[12.5px] font-medium text-white hover:bg-brand-hover disabled:opacity-60"
              >
                Sign in
              </button>
            )}
            <button
              type="button"
              onClick={() => setMode(mode === "key" ? "idle" : "key")}
              className="rounded-md border border-border px-3 py-1.5 text-[12.5px] text-foreground hover:bg-raised"
            >
              API key
            </button>
          </div>
        )}
      </div>

      {mode === "key" && !connected && (
        <div className="mt-3 flex gap-2">
          <input
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder={provider.keyHint}
            className="flex-1 rounded-md border border-border bg-surface px-2.5 py-1.5 font-mono text-[12.5px] text-foreground"
          />
          <button
            type="button"
            onClick={saveKey}
            disabled={busy || !apiKey.trim()}
            className="rounded-md bg-brand px-3 py-1.5 text-[12.5px] font-medium text-white disabled:opacity-60"
          >
            Save
          </button>
        </div>
      )}

      {mode === "oauth" && !connected && (
        <div className="mt-3 space-y-2">
          <p className="text-[12.5px] text-muted-foreground">
            Approve in the tab that opened, then paste the code it shows you here.
          </p>
          <div className="flex gap-2">
            <input
              value={pasted}
              onChange={(e) => setPasted(e.target.value)}
              placeholder="Paste the code"
              className="flex-1 rounded-md border border-border bg-surface px-2.5 py-1.5 font-mono text-[12.5px] text-foreground"
            />
            <button
              type="button"
              onClick={finishOAuth}
              disabled={busy || !pasted.trim()}
              className="rounded-md bg-brand px-3 py-1.5 text-[12.5px] font-medium text-white disabled:opacity-60"
            >
              Connect
            </button>
          </div>
        </div>
      )}

      {error && <p className="mt-2 text-[12px] text-error">{error}</p>}
    </div>
  );
}

// ── Local endpoints: several boxes, each probed before it is stored ──

/** The refusal that keeps an endpoint in place: agents still pin it, and the
 *  server deleted nothing. It arrives as an OBJECT detail, which `ApiError.message`
 *  can only flatten, so it is read off the parsed body instead. */
type PinConflict = { message: string; agents: string[] };

function pinConflict(error: unknown): PinConflict | null {
  if (!(error instanceof ApiError) || error.status !== 409) return null;
  const detail = (error.body as { detail?: { message?: string; agents?: { name: string }[] } })
    ?.detail;
  if (!detail) return null;
  return { message: String(detail.message), agents: (detail.agents ?? []).map((a) => a.name) };
}

function EndpointsPanel({
  endpoints,
  local,
  onChanged,
}: {
  endpoints: ModelEndpoint[];
  local: LocalEndpointDoc | null;
  onChanged: () => void;
}) {
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState("");
  // A probe is only trustworthy while it describes exactly what is in the two
  // fields, so it is kept beside them and dropped whenever they change: the
  // endpoint cannot be stored on the strength of a test it was not served with.
  const [probed, setProbed] = useState<{ baseUrl: string; apiKey: string; models: string[] } | null>(
    null,
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const probeIsCurrent =
    probed !== null && probed.baseUrl === baseUrl.trim() && probed.apiKey === apiKey.trim();

  // Any edit to the address or the key retires the probe result: the model list
  // and the save button both belong to the endpoint that was actually tested.
  function invalidateProbe() {
    setProbed(null);
    setModel("");
    setError(null);
  }

  // The address of an endpoint you just removed comes back as the draft: the next
  // move is usually reconnecting that same box, and a URL is not worth retyping.
  // It enters through the same invalidation as typing, so a seed can never leave a
  // test result current for a box that is no longer connected. The key is not
  // carried — it only ever exists server-side, plus the default row's reveal.
  function seedDraftAfterRemoval(removedBaseUrl: string) {
    setBaseUrl(removedBaseUrl);
    invalidateProbe();
    onChanged();
  }

  async function testEndpoint() {
    setBusy(true);
    setError(null);
    setProbed(null);
    setModel("");
    try {
      const result = await probeLocalEndpoint(
        baseUrl.trim(),
        apiKey.trim() || null,
        PROBE_ONLY_MODEL,
      );
      if (result.ok && result.models?.length) {
        setProbed({ baseUrl: baseUrl.trim(), apiKey: apiKey.trim(), models: result.models });
        setModel(result.models[0]);
      } else if (result.ok) {
        setError("The endpoint answered but listed no models, so there is nothing to run.");
      } else {
        setError(result.error_detail ?? "The endpoint did not answer.");
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not reach the endpoint");
    } finally {
      setBusy(false);
    }
  }

  async function addEndpoint() {
    if (!probeIsCurrent || !probed || !model) return;
    setBusy(true);
    setError(null);
    try {
      await connectLocalEndpoint(probed.baseUrl, model, probed.apiKey || null);
      setBaseUrl("");
      setApiKey("");
      setModel("");
      setProbed(null);
      onChanged();
    } catch (e) {
      // The typed values stay put: a failed save must not cost the user a
      // retyped URL and key.
      setError(e instanceof Error ? e.message : "Could not add the endpoint");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rounded-xl border border-border bg-base p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-[14px] font-medium text-foreground">Local model endpoints</span>
            {endpoints.length > 0 && (
              <span className="rounded-full bg-[var(--color-success)]/15 px-2 py-0.5 text-[11px] font-medium text-[var(--color-success)]">
                {endpoints.length} connected
              </span>
            )}
          </div>
          <p className="mt-0.5 text-[12.5px] text-muted-foreground">
            Ollama or any OpenAI-compatible endpoint your cloud computer can reach — expose it
            with a tunnel (cloudflared, ngrok) or self-host it. Runs use the endpoint you pin;
            unpinned runs use the first one you added.
          </p>
        </div>
      </div>

      {endpoints.length === 0 ? (
        <p className="mt-3 text-[12.5px] text-muted-foreground">No local endpoints yet.</p>
      ) : (
        <ul className="mt-3 space-y-2">
          {endpoints.map((endpoint) => (
            <EndpointRow
              key={endpoint.id}
              endpoint={endpoint}
              keyDoc={local && local.base_url === endpoint.base_url ? local : null}
              onRemoved={seedDraftAfterRemoval}
            />
          ))}
        </ul>
      )}

      <div className="mt-4 space-y-2 border-t border-border pt-3">
        <p className="text-[12.5px] text-muted-foreground">
          Tested before it is saved: add the address, test it, and pick a model from the ones it
          answers with.
        </p>
        <div className="flex flex-col gap-2 sm:flex-row">
          <input
            type="url"
            value={baseUrl}
            onChange={(e) => {
              setBaseUrl(e.target.value);
              invalidateProbe();
            }}
            placeholder="http://your-host:11434/v1"
            aria-label="Endpoint base URL"
            className="flex-1 rounded-md border border-border bg-surface px-2.5 py-1.5 font-mono text-[12.5px] text-foreground"
          />
          <input
            type="password"
            value={apiKey}
            onChange={(e) => {
              setApiKey(e.target.value);
              invalidateProbe();
            }}
            placeholder="optional key…"
            aria-label="Endpoint key"
            className="flex-1 rounded-md border border-border bg-surface px-2.5 py-1.5 font-mono text-[12.5px] text-foreground"
          />
          <button
            type="button"
            onClick={testEndpoint}
            disabled={busy || !baseUrl.trim()}
            className="rounded-md border border-border px-3 py-1.5 text-[12.5px] text-foreground hover:bg-raised disabled:opacity-60"
          >
            Test endpoint
          </button>
        </div>

        {probeIsCurrent && probed && (
          <div className="flex gap-2">
            <label className="flex flex-1 items-center gap-2 text-[12.5px] text-muted-foreground">
              Model
              <select
                value={model}
                onChange={(e) => setModel(e.target.value)}
                className="min-w-0 flex-1 rounded-md border border-border bg-surface px-2.5 py-1.5 font-mono text-[12.5px] text-foreground"
              >
                {probed.models.map((served) => (
                  <option key={served} value={served}>
                    {served}
                  </option>
                ))}
              </select>
            </label>
            <button
              type="button"
              onClick={addEndpoint}
              disabled={busy || !model}
              className="rounded-md bg-brand px-3 py-1.5 text-[12.5px] font-medium text-white disabled:opacity-60"
            >
              Add endpoint
            </button>
          </div>
        )}

        {error && <p className="text-[12px] text-error">{error}</p>}
      </div>
    </div>
  );
}

function EndpointRow({
  endpoint,
  keyDoc,
  onRemoved,
}: {
  endpoint: ModelEndpoint;
  /** The stored key document, but only for the one endpoint it describes: the
   *  server reveals a key for the endpoint a run resolves to by default and for
   *  no other, so every other row shows no key affordance at all. */
  keyDoc: LocalEndpointDoc | null;
  /** Fired only once the server confirms the removal, with the address to leave
   *  behind as the add-form's draft. A refusal fires nothing. */
  onRemoved: (removedBaseUrl: string) => void;
}) {
  const confirm = useConfirm();
  const [revealed, setRevealed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [conflict, setConflict] = useState<PinConflict | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function remove() {
    const sure = await confirm({
      title: `Remove ${endpoint.name}?`,
      body: "Anything pinned to it has to be pinned somewhere else first.",
      confirmLabel: "Remove endpoint",
    });
    if (!sure) return;
    setBusy(true);
    setConflict(null);
    setError(null);
    try {
      await deleteLocalEndpoint(endpoint.id);
      onRemoved(endpoint.base_url);
    } catch (e) {
      const pinning = pinConflict(e);
      if (pinning) setConflict(pinning);
      else setError(e instanceof Error ? e.message : "Could not remove the endpoint");
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="rounded-lg border border-border bg-surface px-3 py-2">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="font-mono text-[12.5px] text-foreground">{endpoint.name}</div>
          <div className="truncate font-mono text-[11.5px] text-muted-foreground">
            {endpoint.base_url}
          </div>
          {endpoint.probe_error ? (
            <div className="mt-1 text-[11.5px] text-error">{endpoint.probe_error}</div>
          ) : (
            <div className="mt-1 flex flex-wrap gap-1">
              {endpoint.models.map((model) => (
                <span
                  key={model}
                  className="rounded bg-raised px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground"
                >
                  {model}
                </span>
              ))}
            </div>
          )}
          {keyDoc && revealed && (
            <div className="mt-1 font-mono text-[11.5px] text-muted-foreground">
              key: {keyDoc.api_key}
            </div>
          )}
        </div>
        <div className="flex shrink-0 gap-2">
          {keyDoc && (
            <button
              type="button"
              onClick={() => setRevealed(!revealed)}
              className="rounded-md border border-border px-3 py-1.5 text-[12.5px] text-dim hover:text-foreground"
            >
              {revealed ? "Hide key" : "Show key"}
            </button>
          )}
          <button
            type="button"
            onClick={remove}
            disabled={busy}
            className="rounded-md border border-border px-3 py-1.5 text-[12.5px] text-dim hover:text-error disabled:opacity-60"
          >
            Remove
          </button>
        </div>
      </div>
      {conflict && (
        <p className="mt-2 text-[12px] text-error">
          {conflict.message}
          {conflict.agents.length > 0 && ` Still pinned by: ${conflict.agents.join(", ")}.`}
        </p>
      )}
      {error && <p className="mt-2 text-[12px] text-error">{error}</p>}
    </li>
  );
}
