// @vitest-environment jsdom
// Regression: a 2xx response whose body is not a valid job snapshot used to
// crash the polling loop outside its try/catch, leaving the UI on "queued"
// (or "Starting...") forever with no error.
import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, getBacktestJob } from "../lib/api";
import { isTransient } from "../lib/retry";
import { useBacktestJob } from "../hooks/useBacktestJob";
import OptimizerPage from "../pages/optimizer";

const STATUS_URL = "/api/backtest/j1/";
const started = { job_id: "j1", status: "queued", status_url: STATUS_URL };
const done = { ...started, status: "succeeded", progress: { fraction: 1 }, report: { ok: true } };
const fast = { csrfToken: "tok", pollIntervalMs: 2, maxRetryDelayMs: 10, random: () => 0 };

const ok = (body) => ({ ok: true, status: 200, headers: new Headers(), json: () => Promise.resolve(body) });
const notJson = (status = 200) => ({
  ok: true,
  status,
  headers: new Headers(),
  json: () => Promise.reject(new SyntaxError("Unexpected token '<', \"<!DOCTYPE \"... is not valid JSON")),
});

/** POST answers `start`; each GET consumes the next of `polls` (last one repeats). */
function scripted(start, polls) {
  let index = 0;
  const gets = { count: 0 };
  globalThis.fetch = vi.fn((url, init) => {
    if (init.method === "POST") return Promise.resolve(start);
    gets.count += 1;
    return Promise.resolve(polls[Math.min(index++, polls.length - 1)]);
  });
  return gets;
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("api client", () => {
  it("rejects a 2xx response whose body is not JSON instead of returning null", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(notJson());
    const error = await getBacktestJob(STATUS_URL).catch((e) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(200);
    expect(error.invalidBody).toBe(true);
    expect(error.message).toMatch(/unexpected response/i);
  });

  it("treats an unreadable success body as transient (a proxy or portal page may clear up)", () => {
    expect(isTransient(new ApiError("x", 200, null, { invalidBody: true }))).toBe(true);
  });
});

describe("polling never stalls on a malformed status response", () => {
  it.each([
    ["a non-JSON body", notJson()],
    ["JSON that is not a snapshot", ok({ hello: "world" })],
    ["a snapshot with an unknown status", ok({ ...started, status: "exploded" })],
    ["JSON null", ok(null)],
  ])("retries past %s and still delivers the result", async (_label, bad) => {
    const gets = scripted(ok(started), [bad, bad, ok(done)]);
    const { result } = renderHook(() => useBacktestJob(fast));
    act(() => {
      result.current.run({});
    });
    await waitFor(() => expect(result.current.state.status).toBe("succeeded"));
    expect(result.current.state.job.report).toEqual({ ok: true });
    expect(gets.count).toBe(3);
  });

  it("fails with an explanation, not a silent stall, when every response is malformed", async () => {
    scripted(ok(started), [notJson()]);
    const { result } = renderHook(() => useBacktestJob({ ...fast, maxRetries: 2 }));
    act(() => {
      result.current.run({});
    });
    await waitFor(() => expect(result.current.state.status).toBe("failed"));
    expect(result.current.state.error).toMatch(/Lost contact with the server/);
    expect(result.current.state.error).toMatch(/unexpected response/i);
    expect(result.current.busy).toBe(false);
  });
});

describe("starting never stalls on a malformed start response", () => {
  it.each([
    ["a non-JSON body", notJson(202)],
    ["JSON without a status URL", ok({ job_id: "j1", status: "queued" })],
    ["JSON null", ok(null)],
  ])("fails visibly on %s without polling", async (_label, bad) => {
    const gets = scripted(bad, [ok(done)]);
    const { result } = renderHook(() => useBacktestJob(fast));
    await act(async () => {
      await result.current.run({});
    });
    expect(result.current.state.status).toBe("failed");
    expect(result.current.state.error).toMatch(/not understood|unexpected response/i);
    expect(result.current.busy).toBe(false);
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(gets.count).toBe(0);
  });

  it("does not stall if the caller's onStarted callback throws", async () => {
    scripted(ok(started), [ok(done)]);
    const onStarted = () => {
      throw new Error("caller bug");
    };
    const { result } = renderHook(() => useBacktestJob({ ...fast, onStarted }));
    await act(async () => {
      await result.current.run({});
    });
    await waitFor(() => expect(result.current.state.status).toBe("failed"));
    expect(result.current.state.error).toMatch(/caller bug/);
  });
});

describe("the optimize tab", () => {
  const config = {
    csrfToken: "tok",
    firstPriceDate: "2006-01-03",
    lastPriceDate: "2024-01-30",
    universeSize: 1981,
    usesRemaining: 15,
    maxRebalances: 120,
    maxUniverseSize: 100,
  };

  it.each([
    ["a non-JSON body", notJson()],
    ["JSON without a portfolio", ok({ metrics: {} })],
  ])("shows an error instead of nothing for %s", async (_label, bad) => {
    globalThis.fetch = vi.fn().mockResolvedValue(bad);
    render(<OptimizerPage config={config} />);
    fireEvent.submit(screen.getByRole("button", { name: "Optimize" }).closest("form"));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toMatch(/unexpected response|not understood/i));
  });
});
