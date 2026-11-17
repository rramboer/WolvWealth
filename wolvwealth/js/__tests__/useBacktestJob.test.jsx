// @vitest-environment jsdom
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useBacktestJob } from "../hooks/useBacktestJob";

const STATUS_URL = "/api/backtest/j1/";

function jsonResponse(status, body) {
  return { ok: status < 300, status, json: () => Promise.resolve(body) };
}

/** fetch mock that answers POST with `started` and successive GETs with `polls`. */
function scriptedFetch(started, polls) {
  let pollIndex = 0;
  return vi.fn((url, init) => {
    if (init.method === "POST") return Promise.resolve(jsonResponse(202, started));
    const snapshot = polls[Math.min(pollIndex++, polls.length - 1)];
    return Promise.resolve(jsonResponse(200, snapshot));
  });
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("useBacktestJob", () => {
  it("walks from starting through running to succeeded", async () => {
    const started = { job_id: "j1", status: "queued", status_url: STATUS_URL, progress: null };
    const running = { ...started, status: "running", progress: { fraction: 0.5, message: "Optimizing rebalance 3 of 5" } };
    const done = { ...started, status: "succeeded", progress: { fraction: 1 }, report: { metrics: {} } };
    globalThis.fetch = scriptedFetch(started, [running, done]);
    const onStarted = vi.fn();

    const { result } = renderHook(() => useBacktestJob({ csrfToken: "tok", pollIntervalMs: 5, onStarted }));
    expect(result.current.state.status).toBe("idle");

    act(() => {
      result.current.run({ start: "2021-01-01" });
    });
    expect(result.current.state.status).toBe("starting");
    expect(result.current.busy).toBe(true);

    await waitFor(() => expect(result.current.state.status).toBe("succeeded"));
    expect(onStarted).toHaveBeenCalledTimes(1);
    expect(result.current.state.job.report).toEqual({ metrics: {} });
    expect(result.current.busy).toBe(false);
    expect(globalThis.fetch.mock.calls.filter(([, init]) => init.method === "GET").length).toBeGreaterThanOrEqual(2);
  });

  it("keeps polling through a 429 instead of failing the job", async () => {
    const started = { job_id: "j1", status: "queued", status_url: STATUS_URL };
    const done = { ...started, status: "succeeded", progress: { fraction: 1 }, report: {} };
    const limited = {
      ok: false,
      status: 429,
      headers: new Headers({ "Retry-After": "0" }),
      json: () => Promise.resolve({ error: { message: "Too many requests. Try again in 1 second.", status_code: 429 } }),
    };
    let gets = 0;
    globalThis.fetch = vi.fn((url, init) => {
      if (init.method === "POST") return Promise.resolve(jsonResponse(202, started));
      gets += 1;
      return Promise.resolve(gets === 1 ? limited : jsonResponse(200, done));
    });

    const { result } = renderHook(() => useBacktestJob({ csrfToken: "tok", pollIntervalMs: 5 }));
    act(() => {
      result.current.run({});
    });
    await waitFor(() => expect(result.current.state.status).toBe("succeeded"));
    expect(gets).toBe(2);
    expect(result.current.state.error).toBeNull();
  });

  it("stops polling once the job fails and exposes the error", async () => {
    const started = { job_id: "j1", status: "queued", status_url: STATUS_URL };
    const failed = { ...started, status: "failed", error: "Fewer than two trading days" };
    globalThis.fetch = scriptedFetch(started, [failed]);

    const { result } = renderHook(() => useBacktestJob({ csrfToken: "tok", pollIntervalMs: 5 }));
    act(() => {
      result.current.run({});
    });
    await waitFor(() => expect(result.current.state.status).toBe("failed"));
    expect(result.current.state.error).toBe("Fewer than two trading days");
    const gets = globalThis.fetch.mock.calls.filter(([, init]) => init.method === "GET").length;
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(globalThis.fetch.mock.calls.filter(([, init]) => init.method === "GET").length).toBe(gets);
  });

  it("does not poll when the start response is already finished (cache hit)", async () => {
    const started = { job_id: "j1", status: "succeeded", status_url: STATUS_URL, cached: true, report: {} };
    globalThis.fetch = scriptedFetch(started, []);

    const { result } = renderHook(() => useBacktestJob({ csrfToken: "tok", pollIntervalMs: 5 }));
    await act(async () => {
      await result.current.run({});
    });
    expect(result.current.state.status).toBe("succeeded");
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(globalThis.fetch).toHaveBeenCalledTimes(1);
  });

  it("reports a rejected start as failed", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(jsonResponse(400, { error: { message: "Invalid frequency." } }));
    const { result } = renderHook(() => useBacktestJob({ csrfToken: "tok" }));
    await act(async () => {
      await result.current.run({});
    });
    expect(result.current.state).toEqual({ status: "failed", job: null, error: "Invalid frequency.", reconnecting: null });
  });

  it("reset returns to idle and ignores late polls", async () => {
    const started = { job_id: "j1", status: "queued", status_url: STATUS_URL };
    const running = { ...started, status: "running", progress: { fraction: 0.2 } };
    globalThis.fetch = scriptedFetch(started, [running]);
    const { result } = renderHook(() => useBacktestJob({ csrfToken: "tok", pollIntervalMs: 5 }));
    act(() => {
      result.current.run({});
    });
    await waitFor(() => expect(result.current.state.status).toBe("running"));
    act(() => {
      result.current.reset();
    });
    expect(result.current.state.status).toBe("idle");
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(result.current.state.status).toBe("idle");
  });
});


/**
 * fetch mock: POST answers `started`; each GET consumes the next step. A step
 * is a snapshot object (200), { status, body, headers } for an error response,
 * or "network" for a rejected fetch. The last step repeats.
 */
function sequencedFetch(started, steps) {
  let index = 0;
  const gets = { count: 0 };
  globalThis.fetch = vi.fn((url, init) => {
    if (init.method === "POST") return Promise.resolve(jsonResponse(202, started));
    gets.count += 1;
    const step = steps[Math.min(index++, steps.length - 1)];
    if (step === "network") return Promise.reject(new TypeError("Failed to fetch"));
    if (step.httpStatus) {
      return Promise.resolve({
        ok: false,
        status: step.httpStatus,
        headers: new Headers(step.headers ?? {}),
        json: () => Promise.resolve(step.body ?? {}),
      });
    }
    return Promise.resolve(jsonResponse(200, step));
  });
  return gets;
}

describe("useBacktestJob retries transient polling failures", () => {
  const started = { job_id: "j1", status: "queued", status_url: STATUS_URL };
  const running = { ...started, status: "running", progress: { fraction: 0.5 } };
  const done = { ...started, status: "succeeded", progress: { fraction: 1 }, report: { ok: true } };
  const fast = { csrfToken: "tok", pollIntervalMs: 2, maxRetryDelayMs: 10, random: () => 0 };

  async function runHook(options = fast) {
    const hook = renderHook(() => useBacktestJob(options));
    act(() => {
      hook.result.current.run({});
    });
    return hook;
  }

  it.each([
    ["a 500", { httpStatus: 500, body: { error: { message: "Internal Server Error" } } }],
    ["a 502 from the proxy", { httpStatus: 502 }],
    ["a 503 during a restart", { httpStatus: 503 }],
    ["a network error", "network"],
  ])("recovers from %s and still delivers the result", async (_label, failure) => {
    const gets = sequencedFetch(started, [failure, failure, running, done]);
    const { result } = await runHook();
    await waitFor(() => expect(result.current.state.status).toBe("succeeded"));
    expect(result.current.state.job.report).toEqual({ ok: true });
    expect(result.current.state.error).toBeNull();
    expect(result.current.state.reconnecting).toBeNull();
    expect(gets.count).toBe(4);
  });

  it("reports that it is reconnecting while it retries", async () => {
    sequencedFetch(started, ["network"]);
    const { result } = await runHook({ ...fast, maxRetries: 50, maxRetryDelayMs: 50 });
    await waitFor(() => expect(result.current.state.reconnecting).not.toBeNull());
    expect(result.current.state.status).toBe("queued"); // still the last known state, not "failed"
    expect(result.current.busy).toBe(true);
    expect(result.current.state.reconnecting).toMatchObject({ maxRetries: 50, reason: expect.stringMatching(/reach the server/) });
  });

  it("gives up after maxRetries consecutive failures with an explanation", async () => {
    const gets = sequencedFetch(started, [{ httpStatus: 503 }]);
    const { result } = await runHook({ ...fast, maxRetries: 3 });
    await waitFor(() => expect(result.current.state.status).toBe("failed"));
    expect(result.current.state.error).toMatch(/Lost contact with the server/);
    expect(gets.count).toBe(4); // first try + 3 retries
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(gets.count).toBe(4); // and it stopped
  });

  it("resets the failure budget after any successful poll", async () => {
    const flaky = { httpStatus: 500 };
    sequencedFetch(started, [flaky, running, flaky, running, flaky, running, done]);
    const { result } = await runHook({ ...fast, maxRetries: 1 });
    await waitFor(() => expect(result.current.state.status).toBe("succeeded"));
  });

  it("does not count 429s against the failure budget", async () => {
    const limited = { httpStatus: 429, headers: { "Retry-After": "0" } };
    sequencedFetch(started, [limited, limited, limited, done]);
    const { result } = await runHook({ ...fast, maxRetries: 1 });
    await waitFor(() => expect(result.current.state.status).toBe("succeeded"));
  });

  it.each([
    [404, /no longer available/],
    [401, /Log in again/],
    [403, /Log in again/],
  ])("fails at once on a permanent %i without retrying", async (status, message) => {
    const gets = sequencedFetch(started, [{ httpStatus: status }, done]);
    const { result } = await runHook();
    await waitFor(() => expect(result.current.state.status).toBe("failed"));
    expect(result.current.state.error).toMatch(message);
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(gets.count).toBe(1);
  });

  it("never retries the POST that starts (and charges for) the job", async () => {
    globalThis.fetch = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"));
    const { result } = renderHook(() => useBacktestJob(fast));
    await act(async () => {
      await result.current.run({});
    });
    expect(result.current.state.status).toBe("failed");
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(globalThis.fetch).toHaveBeenCalledTimes(1);
  });

  it("stops retrying when reset or unmounted", async () => {
    const gets = sequencedFetch(started, ["network"]);
    const { result, unmount } = await runHook({ ...fast, maxRetries: 100 });
    await waitFor(() => expect(result.current.state.reconnecting).not.toBeNull());
    act(() => {
      result.current.reset();
    });
    const afterReset = gets.count;
    await new Promise((resolve) => setTimeout(resolve, 40));
    expect(gets.count).toBe(afterReset);
    unmount();
  });
});
