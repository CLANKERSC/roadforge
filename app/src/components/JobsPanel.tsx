import { useEffect, useMemo, useState } from "react";
import type { Job, LogEntry } from "../lib/types";
import { etaSeconds, formatDuration } from "../lib/jobs";

const STATE_LABEL: Record<Job["state"], string> = {
  queued: "Waiting",
  running: "Building",
  done: "Done",
  failed: "Failed",
  cancelled: "Cancelled",
};

interface Props {
  jobs: Job[];
  logs: Record<string, LogEntry[]>;
  onCancel: (id: string) => void;
  onRebuild: (id: string) => void;
  onShowLogs: (id: string) => void;
  onPreview: (job: Job) => void;
  onInstallBeamng: (id: string) => Promise<string>;
}

function useNow(): number {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(t);
  }, []);
  return now;
}

export function JobsPanel({ jobs, logs, onCancel, onRebuild, onShowLogs, onPreview, onInstallBeamng }: Props) {
  const [openLogs, setOpenLogs] = useState<string | null>(null);
  const now = useNow();

  return (
    <section className="builds">
      <h2>Builds</h2>
      {jobs.length === 0 && <p className="muted small">No builds yet.</p>}
      <ul className="jobs">
        {jobs.map((job) => (
          <JobCard
            key={job.id}
            job={job}
            now={now}
            logs={logs[job.id] ?? []}
            logsOpen={openLogs === job.id}
            onToggleLogs={() => {
              const next = openLogs === job.id ? null : job.id;
              setOpenLogs(next);
              if (next) onShowLogs(job.id);
            }}
            onCancel={() => onCancel(job.id)}
            onRebuild={() => onRebuild(job.id)}
            onPreview={() => onPreview(job)}
            onInstallBeamng={() => onInstallBeamng(job.id)}
          />
        ))}
      </ul>
    </section>
  );
}

function JobCard(props: {
  job: Job;
  now: number;
  logs: LogEntry[];
  logsOpen: boolean;
  onToggleLogs: () => void;
  onCancel: () => void;
  onRebuild: () => void;
  onPreview: () => void;
  onInstallBeamng: () => Promise<string>;
}) {
  const { job, now, logs } = props;
  const [install, setInstall] = useState<{ ok: boolean; text: string } | null>(null);
  const hasBeamng = job.state === "done" && job.completed_stages.includes("export") && wantsBeamng(job);
  const installBeamng = () => {
    setInstall({ ok: true, text: "Copying…" });
    props
      .onInstallBeamng()
      .then((where) => setInstall({ ok: true, text: `Installed. Start BeamNG and pick it in Freeroam. (${where})` }))
      .catch((err) => setInstall({ ok: false, text: err instanceof Error ? err.message : String(err) }));
  };
  const eta = etaSeconds(job, now);
  const active = job.state === "queued" || job.state === "running";
  const pct = Math.round(job.progress * 100);
  const stages = useMemo(
    () =>
      job.stages.map((s) => ({
        id: s,
        status: job.completed_stages.includes(s) ? "done" : job.stage === s ? "running" : "todo",
      })),
    [job.stages, job.completed_stages, job.stage],
  );

  return (
    <li className={`job job-${job.state}`}>
      <div className="job-top">
        <strong>{job.name}</strong>
        <span className={`badge badge-${job.state}`}>{STATE_LABEL[job.state]}</span>
      </div>
      <div className="bar" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
        <div className="bar-fill" style={{ width: `${pct}%` }} />
      </div>
      <ol className="stages">
        {stages.map((s) => (
          <li key={s.id} className={`stage stage-${s.status}`}>
            {s.id}
            {s.status === "running" && ` ${Math.round(job.stage_progress * 100)}%`}
          </li>
        ))}
      </ol>
      <div className="job-msg small">
        {job.message}
        {eta !== null && <span className="muted"> · about {formatDuration(eta)} left</span>}
      </div>
      <div className="job-actions">
        {active && (
          <button onClick={props.onCancel} disabled={job.cancel_requested}>
            Cancel
          </button>
        )}
        {job.state === "done" && job.completed_stages.includes("check") && (
          <button className="primary" onClick={props.onPreview}>
            View in 3D
          </button>
        )}
        {hasBeamng && <button onClick={installBeamng}>Install to BeamNG</button>}
        {!active && <button onClick={props.onRebuild}>Rebuild</button>}
        <button className="link" onClick={props.onToggleLogs}>
          {props.logsOpen ? "Hide log" : "Show log"}
        </button>
      </div>
      {install && (
        <div className={`small install ${install.ok ? "install-ok" : "install-bad"}`} role="status">
          {install.text}
        </div>
      )}
      {props.logsOpen && (
        <pre className="log">
          {logs.length === 0
            ? "No log lines yet."
            : logs
                .map((l) => `${new Date(l.ts * 1000).toLocaleTimeString()}  ${l.level.toUpperCase().padEnd(7)} ${l.message}`)
                .join("\n")}
        </pre>
      )}
    </li>
  );
}

/** Did this build ask for a BeamNG map? */
export function wantsBeamng(job: Job): boolean {
  const selection = (job.params as { selection?: { games?: unknown } }).selection;
  return Array.isArray(selection?.games) && selection.games.includes("beamng");
}
