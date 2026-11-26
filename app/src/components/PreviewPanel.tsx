import { useEffect, useRef, useState } from "react";
import type { DrivabilityReport, Job, SceneInfo } from "../lib/types";
import type { WorkerClient } from "../lib/worker";

interface Props {
  job: Job;
  client: WorkerClient;
  onClose: () => void;
}

type Load = { kind: "loading" } | { kind: "ready"; triangles: number } | { kind: "error"; message: string };

/** Full-window 3D view of a finished build, with the drivability report next to it. */
export function PreviewPanel({ job, client, onClose }: Props) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const [load, setLoad] = useState<Load>({ kind: "loading" });
  const [report, setReport] = useState<DrivabilityReport | null>(null);

  useEffect(() => {
    let dispose: (() => void) | null = null;
    let cancelled = false;
    (async () => {
      try {
        const [glb, scene, drivability, lib] = await Promise.all([
          client.fileBytes(job.id, "preview.glb"),
          client.fileJson<SceneInfo>(job.id, "scene.json"),
          client.fileJson<DrivabilityReport>(job.id, "drivability.json"),
          import("../lib/preview3d"), // loaded only when a preview is opened
        ]);
        if (cancelled || !canvas.current) return;
        setReport(drivability);
        const handle = await lib.startPreview(canvas.current, glb, scene.spawn);
        if (cancelled) return handle.dispose();
        dispose = handle.dispose;
        setLoad({ kind: "ready", triangles: handle.triangles });
      } catch (err) {
        if (!cancelled) setLoad({ kind: "error", message: err instanceof Error ? err.message : String(err) });
      }
    })();
    return () => {
      cancelled = true;
      dispose?.();
    };
  }, [client, job.id]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.stopPropagation(); // Escape closes the preview only; it must not also clear the drawing
      onClose();
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [onClose]);

  return (
    <div className="preview" role="dialog" aria-label={`3D preview of ${job.name}`}>
      <div className="preview-view">
        <canvas ref={canvas} data-ready={load.kind === "ready"} data-triangles={load.kind === "ready" ? load.triangles : 0} />
        {load.kind === "loading" && <div className="preview-note">Loading the 3D model…</div>}
        {load.kind === "error" && <div className="preview-note preview-bad">Could not show the model: {load.message}</div>}
        <div className="preview-help small">Drag to look around · right-drag to move · scroll to zoom</div>
      </div>
      <aside className="preview-side">
        <div className="panel-head">
          <h2>{job.name}</h2>
          <button onClick={onClose}>Close</button>
        </div>
        {report && <ReportView report={report} />}
      </aside>
    </div>
  );
}

export function ReportView({ report }: { report: DrivabilityReport }) {
  const s = report.summary;
  return (
    <div className="report">
      <p className={report.ok ? "verdict verdict-ok" : "verdict verdict-bad"}>
        {report.ok ? "Drivable: all checks passed" : "Some checks need a look"}
      </p>
      <p className="small muted">
        {s.road_km.toFixed(1)} km of road · {s.roads} stretches · {s.junctions} junctions · {s.bridges} bridges
      </p>
      <h3>Checks</h3>
      <ul className="checks">
        {report.checks.map((c) => (
          <li key={c.id} className={c.ok ? "check-ok" : "check-bad"}>
            <span>{c.ok ? "✓" : "!"}</span> {c.label}
            <span className="muted small">
              {" "}
              ({c.value} {c.unit})
            </span>
          </li>
        ))}
      </ul>
      <h3>Repaired automatically</h3>
      {report.repairs.length === 0 ? (
        <p className="small muted">Nothing needed repairing.</p>
      ) : (
        <ul className="repairs small">
          {report.repairs.map((r) => (
            <li key={r.id}>{r.text}</li>
          ))}
        </ul>
      )}
      {report.notes.map((n) => (
        <p key={n} className="small note">
          {n}
        </p>
      ))}
    </div>
  );
}
