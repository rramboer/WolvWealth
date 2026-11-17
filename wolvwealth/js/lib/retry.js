// Retry policy for polling a running backtest. Pure functions so the policy
// can be tested without timers.

/**
 * Whether a failed request is worth repeating unchanged.
 *
 * Transient: no response at all (network error, status 0), request timeout
 * (408), rate limiting (429) and server-side failures (5xx), where the same
 * GET can succeed moments later. Everything else, notably 404 (the job is
 * gone: expired, or a server restart cleared the in-memory job store) and
 * 401/403 (the session ended), will fail the same way every time.
 */
export function isTransient(error) {
  const status = error?.status;
  if (typeof status !== "number") return false; // A bug in our code, not the network.
  return status === 0 || status === 408 || status === 429 || status >= 500;
}

/**
 * Milliseconds to wait before retry number `attempt` (1-based).
 *
 * Exponential (base x 2^attempt, capped at maxMs) with "equal jitter": a
 * random point in the upper half of the window, so a crowd of clients that
 * lost the server at the same moment do not all come back at the same moment.
 * A server-provided Retry-After always wins when it asks for longer.
 */
export function retryDelay(attempt, { baseMs = 1000, maxMs = 30000, retryAfterSec = null, random = Math.random } = {}) {
  const window = Math.min(maxMs, baseMs * 2 ** attempt);
  const jittered = window / 2 + random() * (window / 2);
  const serverAsked = retryAfterSec == null ? 0 : retryAfterSec * 1000;
  return Math.round(Math.max(jittered, serverAsked));
}

/** User-facing explanation for an error that retrying will not fix. */
export function permanentFailureMessage(error) {
  if (error?.status === 404) {
    return "This backtest is no longer available on the server (it may have expired or the server restarted). Run it again.";
  }
  if (error?.status === 401 || error?.status === 403) {
    return "Your session has ended. Log in again, then rerun the backtest.";
  }
  return error?.message ?? "Unexpected error while checking the backtest.";
}
