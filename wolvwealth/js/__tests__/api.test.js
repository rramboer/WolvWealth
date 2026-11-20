import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, getBacktestJob, startBacktest } from "../lib/api";

function mockFetch(status, body) {
  const response = { ok: status >= 200 && status < 300, status, json: () => Promise.resolve(body) };
  globalThis.fetch = vi.fn().mockResolvedValue(response);
  return globalThis.fetch;
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("startBacktest", () => {
  it("posts JSON with the CSRF token and same-origin cookies", async () => {
    const fetch = mockFetch(202, { job_id: "j1", status: "queued", status_url: "/api/backtest/j1/" });
    const job = await startBacktest({ start: "2021-01-01" }, "tok");
    expect(job.job_id).toBe("j1");
    const [url, init] = fetch.mock.calls[0];
    expect(url).toBe("/api/backtest/");
    expect(init.method).toBe("POST");
    expect(init.credentials).toBe("same-origin");
    expect(init.headers["X-CSRF-Token"]).toBe("tok");
    expect(JSON.parse(init.body)).toEqual({ start: "2021-01-01" });
  });

  it("surfaces the API's error message and status", async () => {
    mockFetch(429, { error: { message: "You already have 2 backtest(s) running.", status_code: 429 } });
    await expect(startBacktest({}, "tok")).rejects.toMatchObject({
      name: "ApiError",
      status: 429,
      message: "You already have 2 backtest(s) running.",
    });
  });

  it("exposes Retry-After on rate-limited responses", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 429,
      headers: new Headers({ "Retry-After": "12" }),
      json: () => Promise.resolve({ error: { message: "Too many requests. Try again in 12 seconds." } }),
    });
    const error = await startBacktest({}, "tok").catch((e) => e);
    expect(error.status).toBe(429);
    expect(error.retryAfter).toBe(12);
    expect(error.message).toBe("Too many requests. Try again in 12 seconds.");
  });

  it("falls back to a generic message when the body is not JSON", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({ ok: false, status: 502, json: () => Promise.reject(new Error()) });
    await expect(startBacktest({}, "tok")).rejects.toThrow("Request failed with status 502.");
  });

  it("reports network failures as an ApiError with status 0", async () => {
    globalThis.fetch = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"));
    const error = await startBacktest({}, "tok").catch((e) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(0);
  });
});

describe("getBacktestJob", () => {
  it("GETs the status URL without a CSRF header", async () => {
    const fetch = mockFetch(200, { job_id: "j1", status: "running", status_url: "/api/backtest/j1/" });
    await getBacktestJob("/api/backtest/j1/");
    const [url, init] = fetch.mock.calls[0];
    expect(url).toBe("/api/backtest/j1/");
    expect(init.method).toBe("GET");
    expect(init.headers["X-CSRF-Token"]).toBeUndefined();
    expect(init.body).toBeUndefined();
  });
});
