import { describe, expect, it } from "vitest";
import { applyEvent, emptyJobsState, etaSeconds, formatDuration, sortedJobs, withJobs } from "./jobs";
import type { Job } from "./types";

function job(over: Partial<Job> = {}): Job {
  return {
    id: "a", kind: "demo", name: "x", params: {}, state: "queued", stages: ["fetch", "process", "package"],
    completed_stages: [], stage: null, stage_progress: 0, progress: 0, message: "", error: null,
    cancel_requested: false, created_at: 1, started_at: null, updated_at: 1, finished_at: null, ...over,
  };
}

describe("applyEvent", () => {
  it("adds and updates jobs", () => {
    let s = applyEvent(emptyJobsState, { type: "job", job: job() });
    s = applyEvent(s, { type: "job", job: job({ state: "running", updated_at: 2 }) });
    expect(s.jobs.a.state).toBe("running");
  });
  it("ignores stale updates", () => {
    let s = applyEvent(emptyJobsState, { type: "job", job: job({ state: "done", updated_at: 5 }) });
    s = applyEvent(s, { type: "job", job: job({ state: "running", updated_at: 3 }) });
    expect(s.jobs.a.state).toBe("done");
  });
  it("appends logs once", () => {
    const log = { id: 1, job_id: "a", ts: 1, level: "info", message: "hi" };
    let s = applyEvent(emptyJobsState, { type: "log", log });
    s = applyEvent(s, { type: "log", log });
    expect(s.logs.a).toHaveLength(1);
  });
});

describe("helpers", () => {
  it("sorts newest first", () => {
    const s = withJobs(emptyJobsState, [job({ id: "old", created_at: 1 }), job({ id: "new", created_at: 2 })]);
    expect(sortedJobs(s).map((j) => j.id)).toEqual(["new", "old"]);
  });
  it("estimates time left", () => {
    expect(etaSeconds(job({ state: "running", started_at: 100, progress: 0.5 }), 110)).toBeCloseTo(10);
    expect(etaSeconds(job({ state: "done" }), 110)).toBeNull();
  });
  it("formats durations", () => {
    expect(formatDuration(42)).toBe("42 s");
    expect(formatDuration(125)).toBe("2 min 5 s");
    expect(formatDuration(3720)).toBe("1 h 2 min");
  });
});
