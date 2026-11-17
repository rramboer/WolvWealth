// Thin client for the WolvWealth JSON API, authenticated by the browser
// session. State-changing calls carry the CSRF token from the page.

export class ApiError extends Error {
  constructor(message, status, retryAfter = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.retryAfter = retryAfter; // Seconds, from the Retry-After header on 429 responses.
  }
}

function retryAfterSeconds(response) {
  const value = Number(response.headers?.get?.("Retry-After"));
  return Number.isFinite(value) && value >= 0 ? value : null;
}

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
  try {
    data = await response.json();
  } catch {
    data = null;
  }
  if (!response.ok) {
    const message = data?.error?.message ?? `Request failed with status ${response.status}.`;
    throw new ApiError(message, response.status, retryAfterSeconds(response));
  }
  return data;
}

/** POST /api/backtest/ -> job snapshot (202 while running, 200 when served from cache). */
export const startBacktest = (payload, csrfToken) => request("POST", "/api/backtest/", { body: payload, csrfToken });

/** GET the job's status_url -> job snapshot. */
export const getBacktestJob = (statusUrl) => request("GET", statusUrl);

/** POST /api/optimize/ -> optimized portfolio. */
export const optimize = (payload, csrfToken) => request("POST", "/api/optimize/", { body: payload, csrfToken });
