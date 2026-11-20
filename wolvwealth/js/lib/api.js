// Thin client for the WolvWealth JSON API, authenticated by the browser
// session. State-changing calls carry the CSRF token from the page.
//
// Contract: every exported call either resolves with a body of the expected
// shape or rejects with an ApiError. A 2xx whose body is not JSON, or is JSON
// of the wrong shape (a proxy error page, a captive portal, a truncated
// response), rejects with `invalidBody: true` rather than resolving with
// null or garbage that would crash the caller later.

export class ApiError extends Error {
  constructor(message, status, retryAfter = null, { invalidBody = false } = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.retryAfter = retryAfter; // Seconds, from the Retry-After header on 429 responses.
    this.invalidBody = invalidBody; // A success status arrived with an unusable body.
  }
}

export const JOB_STATUSES = new Set(["queued", "running", "succeeded", "failed"]);

function retryAfterSeconds(response) {
  const value = Number(response.headers?.get?.("Retry-After"));
  return Number.isFinite(value) && value >= 0 ? value : null;
}

function unusable(status, what) {
  return new ApiError(`The server sent an unexpected response (${what}).`, status, null, { invalidBody: true });
}

const isObject = (value) => typeof value === "object" && value !== null && !Array.isArray(value);

async function request(method, url, { body, csrfToken } = {}) {
  const headers = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (csrfToken) headers["X-CSRF-Token"] = csrfToken;

  let response;
  try {
    response = await fetch(url, {
      method,
      headers,
      credentials: "same-origin",
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError("Could not reach the server. Check your connection and try again.", 0);
  }

  let data = null;
  let parsed = true;
  try {
    data = await response.json();
  } catch {
    parsed = false;
  }
  if (!response.ok) {
    const message = data?.error?.message ?? `Request failed with status ${response.status}.`;
    throw new ApiError(message, response.status, retryAfterSeconds(response));
  }
  if (!parsed) throw unusable(response.status, "not JSON");
  return { data, status: response.status };
}

/** Reject anything that is not a job snapshot the UI can act on. */
function asJobSnapshot({ data, status }) {
  if (!isObject(data)) return Promise.reject(unusable(status, "not a job status"));
  if (!JOB_STATUSES.has(data.status)) return Promise.reject(unusable(status, `unknown job status ${JSON.stringify(data.status)}`));
  if (typeof data.status_url !== "string" || !data.status_url) {
    return Promise.reject(unusable(status, "missing status_url"));
  }
  if (data.status === "succeeded" && !isObject(data.report)) return Promise.reject(unusable(status, "missing report"));
  return data;
}

/** POST /api/backtest/ -> job snapshot (202 while running, 200 when served from cache). */
export const startBacktest = (payload, csrfToken) =>
  request("POST", "/api/backtest/", { body: payload, csrfToken }).then(asJobSnapshot);

/** GET the job's status_url -> job snapshot. */
export const getBacktestJob = (statusUrl) => request("GET", statusUrl).then(asJobSnapshot);

/** POST /api/optimize/ -> optimized portfolio. */
export const optimize = (payload, csrfToken) =>
  request("POST", "/api/optimize/", { body: payload, csrfToken }).then(({ data, status }) =>
    isObject(data) && isObject(data.optimized_portfolio) ? data : Promise.reject(unusable(status, "missing portfolio")),
  );
