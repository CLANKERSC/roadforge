import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { MapView } from "./components/MapView";
import { JobsPanel } from "./components/JobsPanel";
import { brand } from "./lib/brand";
import { applyEvent, emptyJobsState, sortedJobs, withJobs, type JobsState } from "./lib/jobs";
import { getWorkerInfo, WorkerClient } from "./lib/worker";

type Status = { kind: "connecting" } | { kind: "ok"; version: string } | { kind: "error"; message: string };

export default function App() {
  const [client, setClient] = useState<WorkerClient | null>(null);
  const [status, setStatus] = useState<Status>({ kind: "connecting" });
  const [live, setLive] = useState(false);
  const [state, setState] = useState<JobsState>(emptyJobsState);
  const counter = useRef(1);

  // Find the worker and check it's alive. Retry for a while: it may still be starting.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      for (let attempt = 0; attempt < 60 && !cancelled; attempt++) {
        try {
          const c = new WorkerClient(await getWorkerInfo());
          const h = await c.health();
          if (cancelled) return;
          setClient(c);
          setStatus({ kind: "ok", version: h.version });
          return;
        } catch (err) {
          if (attempt === 59) setStatus({ kind: "error", message: String(err) });
          await new Promise((r) => setTimeout(r, 500));
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Load existing jobs, then follow live updates.
  useEffect(() => {
    if (!client) return;
    client.listJobs().then((jobs) => setState((s) => withJobs(s, jobs)));
    return client.subscribe(
      (event) => setState((s) => applyEvent(s, event)),
      (connected) => {
        setLive(connected);
        if (connected) client.listJobs().then((jobs) => setState((s) => withJobs(s, jobs)));
      },
    );
  }, [client]);

  const jobs = useMemo(() => sortedJobs(state), [state]);

  const startDemo = useCallback(() => {
    client?.createDemoJob(`Demo build ${counter.current++}`).catch(console.error);
  }, [client]);
  const cancel = useCallback((id: string) => void client?.cancel(id).catch(console.error), [client]);
  const rebuild = useCallback((id: string) => void client?.rebuild(id).catch(console.error), [client]);
  const showLogs = useCallback(
    (id: string) => {
      client
        ?.logs(id)
        .then((logs) => setState((s) => logs.reduce((acc, log) => applyEvent(acc, { type: "log", log }), s)))
        .catch(console.error);
    },
    [client],
  );

  return (
    <div className="app">
      <header className="topbar">
        <img src="/icon.svg" alt="" className="logo" />
        <div className="title">
          <h1>{brand.name}</h1>
          <span className="muted small">{brand.tagline}</span>
        </div>
        <div className="spacer" />
        <WorkerBadge status={status} live={live} />
      </header>
      <main className="main">
        <MapView />
        <JobsPanel
          jobs={jobs}
          logs={state.logs}
          connected={status.kind === "ok"}
          onStartDemo={startDemo}
          onCancel={cancel}
          onRebuild={rebuild}
          onShowLogs={showLogs}
        />
      </main>
      <footer className="disclaimer small muted">
        Maps made with {brand.name} are approximations for games, not for navigation.
      </footer>
    </div>
  );
}

function WorkerBadge({ status, live }: { status: Status; live: boolean }) {
  if (status.kind === "connecting") return <span className="pill pill-wait">Starting engine…</span>;
  if (status.kind === "error")
    return (
      <span className="pill pill-bad" title={status.message}>
        Engine not running
      </span>
    );
  return (
    <span className={`pill ${live ? "pill-ok" : "pill-wait"}`}>
      Engine {live ? "ready" : "reconnecting…"} · v{status.version}
    </span>
  );
}
