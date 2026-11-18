import { useCallback, useEffect, useRef, useState } from "react";
import { getBacktestJob, startBacktest } from "../lib/api";
import { isTransient, permanentFailureMessage, retryDelay } from "../lib/retry";

export const IDLE = { status: "idle", job: null, error: null, reconnecting: null };

const FINISHED = new Set(["succeeded", "failed"]);
const RATE_LIMIT_FALLBACK_SECONDS = 5; // When a 429 arrives without Retry-After.
const STARTED_UNKNOWN =
  "The backtest may still have started; running it again in a minute may return the cached result.";

/**
 * Drives one backtest job: POST to start, then poll its status URL until it
 * finishes. `state.status` is "idle" | "starting" | "queued" | "running" |
 * "succeeded" | "failed"; `state.job` is the latest server snapshot.
 *
 * Polling survives transient failures (network errors, 5xx, 408, 429, and a
 * 2xx whose body is not a valid snapshot): the
 * job keeps running on the server, so the hook backs off and retries, and
 * exposes `state.reconnecting` ({ attempt, maxRetries, delayMs, reason }) so
 * the UI can say so. It gives up after `maxRetries` consecutive failures;
 * 429s do not count, because the server answered and the job is alive.
 * Permanent failures (404 job gone, 401/403 session ended) fail at once.
 *
 * Starting the job is never retried automatically: it is a POST that charges
 * a use, and after a network error we cannot know whether it arrived.
 */
export function useBacktestJob({
  csrfToken,
  pollIntervalMs = 1000,
  maxRetries = 8,
  maxRetryDelayMs = 30000,
  random = Math.random,
  onStarted,
} = {}) {
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
    setState({ status: job.status, job, error: job.status === "failed" ? job.error : null, reconnecting: null });
    return !FINISHED.has(job.status);
  }, []);

  const run = useCallback(
    async (payload) => {
      const id = ++runId.current;
      clearTimer();
      setState({ status: "starting", job: null, error: null, reconnecting: null });
      const fail = (message) => {
        if (id === runId.current) setState((prev) => ({ ...prev, status: "failed", error: message, reconnecting: null }));
      };
      let job;
      try {
        job = await startBacktest(payload, csrfToken);
      } catch (error) {
        // An unreadable reply to the POST may still mean the job started (and was charged).
        fail(error.invalidBody ? `${error.message} ${STARTED_UNKNOWN}` : error.message);
        return;
      }
      try {
        onStarted?.(job);
        if (!applySnapshot(id, job)) return;
      } catch (error) {
        fail(`Unexpected error after starting the backtest: ${error.message}`);
        return;
      }

      let failures = 0; // Consecutive transient failures; reset by any successful poll.

      // Every path through poll() either schedules the next poll or ends in a
      // terminal state. The outer try is the safety net that guarantees it:
      // an exception escaping an async timer callback is otherwise swallowed
      // as an unhandled rejection and the UI freezes on its last state.
      const poll = async () => {
        try {
          await pollOnce();
        } catch (error) {
          fail(`Unexpected error while checking the backtest: ${error.message}`);
        }
      };

      const pollOnce = async () => {
        if (id !== runId.current) return;
        let snapshot;
        try {
          snapshot = await getBacktestJob(job.status_url);
        } catch (error) {
          if (id !== runId.current) return;
          if (!isTransient(error)) {
            fail(permanentFailureMessage(error));
            return;
          }
          if (error.status === 429) {
            // Rate limited: the server is up and the job is running. Wait as told; not a failure.
            const seconds = error.retryAfter ?? RATE_LIMIT_FALLBACK_SECONDS;
            timer.current = setTimeout(poll, Math.max(pollIntervalMs, seconds * 1000));
            return;
          }
          failures += 1;
          if (failures > maxRetries) {
            fail(
              `Lost contact with the server while the backtest was running (${error.message}). ` +
                "It may still finish; run it again in a minute and the result may come back from cache.",
            );
            return;
          }
          const delayMs = retryDelay(failures, {
            baseMs: pollIntervalMs,
            maxMs: maxRetryDelayMs,
            retryAfterSec: error.retryAfter,
            random,
          });
          setState((prev) => ({ ...prev, reconnecting: { attempt: failures, maxRetries, delayMs, reason: error.message } }));
          timer.current = setTimeout(poll, delayMs);
          return;
        }
        failures = 0;
        if (applySnapshot(id, snapshot)) timer.current = setTimeout(poll, pollIntervalMs);
      };
      timer.current = setTimeout(poll, pollIntervalMs);
    },
    [csrfToken, pollIntervalMs, maxRetries, maxRetryDelayMs, random, onStarted, applySnapshot],
  );

  const reset = useCallback(() => {
    runId.current += 1;
    clearTimer();
    setState(IDLE);
  }, []);

  const busy = state.status === "starting" || state.status === "queued" || state.status === "running";
  return { state, run, reset, busy };
}
