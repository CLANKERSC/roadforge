import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { MapView, type FlyTarget } from "./components/MapView";
import { JobsPanel } from "./components/JobsPanel";
import { BuildPanel } from "./components/BuildPanel";
import { SearchBox } from "./components/SearchBox";
import { brand } from "./lib/brand";
import { applyEvent, emptyJobsState, sortedJobs, withJobs, type JobsState } from "./lib/jobs";
import { draftReducer, emptyDraft, toSelection, type Game, type LonLat } from "./lib/selection";
import type { Place, Plan, RouteResult } from "./lib/types";
import { getWorkerInfo, WorkerClient } from "./lib/worker";

type Status = { kind: "connecting" } | { kind: "ok"; version: string } | { kind: "error"; message: string };

export default function App() {
  const [client, setClient] = useState<WorkerClient | null>(null);
  const [status, setStatus] = useState<Status>({ kind: "connecting" });
  const [live, setLive] = useState(false);
  const [state, setState] = useState<JobsState>(emptyJobsState);

  const [draft, dispatch] = useReducer(draftReducer, emptyDraft);
  const [name, setName] = useState("");
  const [games, setGames] = useState<Game[]>(["ac"]);
  const [route, setRoute] = useState<RouteResult | null>(null);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [planning, setPlanning] = useState(false);
  const [flyTo, setFlyTo] = useState<FlyTarget | null>(null);
  const flyNonce = useRef(0);

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

  // Route mode: ask the engine to follow real roads through the clicked points.
  const waypointsKey = draft.mode === "route" ? JSON.stringify(draft.points) : "";
  useEffect(() => {
    setRoute(null);
    if (!client || draft.mode !== "route" || draft.points.length < 2) return;
    let stale = false;
    const timer = setTimeout(() => {
      client
        .route(draft.points)
        .then((r) => !stale && setRoute(r))
        .catch((err) => !stale && setRoute({ coords: draft.points, km: 0, routed: false, warning: String(err) }));
    }, 250);
    return () => {
      stale = true;
      clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client, waypointsKey]);

  // Whenever the selection changes, ask the engine for size, limits and data sources.
  const routeCoords: LonLat[] | null = route ? route.coords : null;
  const selection = useMemo(() => toSelection(draft, routeCoords, games), [draft, routeCoords, games]);
  const selectionKey = JSON.stringify(selection);
  useEffect(() => {
    if (!client || !selection) {
      setPlan(null);
      setPlanning(false);
      return;
    }
    let stale = false;
    setPlanning(true);
    const timer = setTimeout(() => {
      client
        .plan(selection, name)
        .then((p) => !stale && setPlan(p))
        .catch((err) => !stale && setPlan({ ok: false, problems: [String(err)], layers: {} }))
        .finally(() => !stale && setPlanning(false));
    }, 300);
    return () => {
      stale = true;
      clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client, selectionKey, name]);

  // Keyboard shortcuts for drawing.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const typing = e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement;
      if (typing) return;
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") {
        e.preventDefault();
        dispatch({ type: "undo" });
      } else if (e.key === "Escape") dispatch({ type: "clear" });
      else if (e.key === "Enter") dispatch({ type: "close" });
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const jobs = useMemo(() => sortedJobs(state), [state]);
  const canBuild = !!client && !!selection && !!plan?.ok && !planning && name.trim().length > 0;

  const build = useCallback(() => {
    if (client && selection && name.trim()) client.createBuild(name.trim(), selection).catch(console.error);
  }, [client, selection, name]);
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
  const pickPlace = useCallback((place: Place) => {
    setFlyTo({ center: [place.lon, place.lat], bbox: place.bbox, nonce: ++flyNonce.current });
  }, []);

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
        <div className="map-wrap">
          <MapView
            draft={draft}
            dispatch={dispatch}
            routeCoords={routeCoords}
            outline={plan?.outline ?? null}
            flyTo={flyTo}
          />
          <SearchBox client={client} onPick={pickPlace} />
        </div>
        <aside className="panel">
          <BuildPanel
            draft={draft}
            dispatch={dispatch}
            name={name}
            onName={setName}
            games={games}
            onGames={setGames}
            plan={plan}
            planning={planning}
            routeWarning={draft.mode === "route" && route && !route.routed ? route.warning : null}
            canBuild={canBuild}
            onBuild={build}
          />
          <JobsPanel jobs={jobs} logs={state.logs} onCancel={cancel} onRebuild={rebuild} onShowLogs={showLogs} />
        </aside>
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
