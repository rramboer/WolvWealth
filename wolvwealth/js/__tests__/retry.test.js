import { describe, expect, it } from "vitest";
import { isTransient, permanentFailureMessage, retryDelay } from "../lib/retry";

describe("isTransient", () => {
  it.each([0, 408, 429, 500, 502, 503, 504])("retries status %i", (status) => {
    expect(isTransient({ status })).toBe(true);
  });

  it.each([400, 401, 403, 404, 409, 422])("does not retry status %i", (status) => {
    expect(isTransient({ status })).toBe(false);
  });

  it("does not retry errors without a status (bugs, not network trouble)", () => {
    expect(isTransient(new TypeError("x is undefined"))).toBe(false);
    expect(isTransient(undefined)).toBe(false);
  });
});

describe("retryDelay", () => {
  const top = () => 1; // jitter at the top of the window
  const bottom = () => 0; // jitter at the bottom of the window

  it("doubles each attempt within a jittered window", () => {
    expect(retryDelay(1, { baseMs: 1000, random: top })).toBe(2000);
    expect(retryDelay(2, { baseMs: 1000, random: top })).toBe(4000);
    expect(retryDelay(3, { baseMs: 1000, random: top })).toBe(8000);
    expect(retryDelay(1, { baseMs: 1000, random: bottom })).toBe(1000);
    expect(retryDelay(3, { baseMs: 1000, random: bottom })).toBe(4000);
  });

  it("is capped", () => {
    expect(retryDelay(10, { baseMs: 1000, maxMs: 30000, random: top })).toBe(30000);
    expect(retryDelay(50, { baseMs: 1000, maxMs: 30000, random: bottom })).toBe(15000);
  });

  it("never waits less than the server's Retry-After", () => {
    expect(retryDelay(1, { baseMs: 1000, retryAfterSec: 20, random: top })).toBe(20000);
    expect(retryDelay(5, { baseMs: 1000, retryAfterSec: 1, random: top })).toBe(30000);
  });

  it("spreads clients out instead of retrying in lockstep", () => {
    const delays = new Set(Array.from({ length: 50 }, () => retryDelay(3, { baseMs: 1000 })));
    expect(delays.size).toBeGreaterThan(10);
    for (const delay of delays) {
      expect(delay).toBeGreaterThanOrEqual(4000);
      expect(delay).toBeLessThanOrEqual(8000);
    }
  });
});

describe("permanentFailureMessage", () => {
  it("explains a vanished job and an ended session", () => {
    expect(permanentFailureMessage({ status: 404 })).toMatch(/no longer available/);
    expect(permanentFailureMessage({ status: 401 })).toMatch(/Log in again/);
    expect(permanentFailureMessage({ status: 403 })).toMatch(/Log in again/);
    expect(permanentFailureMessage({ status: 400, message: "Bad thing." })).toBe("Bad thing.");
  });
});
