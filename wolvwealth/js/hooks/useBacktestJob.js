import { useCallback, useEffect, useRef, useState } from "react";
import { getBacktestJob, startBacktest } from "../lib/api";

export const IDLE = { status: "idle", job: null, error: null };

const FINISHED = new Set(["succeeded", "failed"]);

/**
 * Drives one backtest job: POST to start, then poll its status URL until it
 * finishes. `state.status` is "idle" | "starting" | "queued" | "running" |
 * "succeeded" | "failed"; `state.job` is the latest server snapshot.
 */
export function useBacktestJob({ csrfToken, pollIntervalMs = 1000, onStarted } = {}) {
  const [state, setState] = useState(IDLE);
  const runId = useRef(0); // Bumped on every run/reset so stale polls are ignored.
  const timer = useRef(null);

  const clearTimer = () => {
    if (timer.current) {
      clearTimeout(timer.current);
      timer.current = null;
    }
  };

  useEffect(() => {
    return () => {
      runId.current += 1;
      clearTimer();
    };
  }, []);

  const applySnapshot = useCallback((id, job) => {
    if (id !== runId.current) return false;
    setState({ status: job.status, job, error: job.status === "failed" ? job.error : null });
    return !FINISHED.has(job.status);
  }, []);

  const run = useCallback(
    async (payload) => {
      const id = ++runId.current;
      clearTimer();
      setState({ status: "starting", job: null, error: null });
      let job;
      try {
        job = await startBacktest(payload, csrfToken);
      } catch (error) {
        if (id === runId.current) setState({ status: "failed", job: null, error: error.message });
        return;
      }
      onStarted?.(job);
      if (!applySnapshot(id, job)) return;

      const poll = async () => {
        if (id !== runId.current) return;
        try {
          const snapshot = await getBacktestJob(job.status_url);
          if (applySnapshot(id, snapshot)) timer.current = setTimeout(poll, pollIntervalMs);
        } catch (error) {
          if (id === runId.current) setState((prev) => ({ ...prev, status: "failed", error: error.message }));
        }
      };
      timer.current = setTimeout(poll, pollIntervalMs);
    },
    [csrfToken, pollIntervalMs, onStarted, applySnapshot],
  );

  const reset = useCallback(() => {
    runId.current += 1;
    clearTimer();
    setState(IDLE);
  }, []);

  const busy = state.status === "starting" || state.status === "queued" || state.status === "running";
  return { state, run, reset, busy };
}
