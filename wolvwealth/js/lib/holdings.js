// Current holdings typed as text: one "TICKER shares" entry per line or comma, e.g. "AAPL 10, MSFT 2.5".
// "AAPL: 10" and "AAPL=10" work too. The server checks the tickers; this checks the shape.

const TICKER = /^[A-Z][A-Z0-9.-]{0,9}$/;
const MAX_SHARES = 1e12;

/** { holdings: {TICKER: shares}, error: null } or { holdings: {}, error: "why" }. Repeated tickers add up. */
export function parseHoldings(text) {
  const holdings = {};
  const entries = (text ?? "")
    .split(/[\n,;]+/)
    .map((entry) => entry.trim())
    .filter(Boolean);
  for (const entry of entries) {
    const match = /^([A-Za-z][A-Za-z0-9.-]*)(?:\s*[:=]\s*|\s+)(\S+)$/.exec(entry); // A separator is required.
    if (!match) return { holdings: {}, error: `Write "${entry}" as a ticker and a share count, e.g. AAPL 10.` };
    const ticker = match[1].toUpperCase();
    const shares = Number(match[2]);
    if (!TICKER.test(ticker)) return { holdings: {}, error: `Not a ticker: ${match[1]}.` };
    if (!Number.isFinite(shares) || shares < 0 || shares > MAX_SHARES) {
      return { holdings: {}, error: `${ticker}: shares must be a number from 0 to ${MAX_SHARES.toLocaleString("en-US")}.` };
    }
    holdings[ticker] = (holdings[ticker] ?? 0) + shares;
  }
  return { holdings, error: null };
}

export const hasHoldings = (text) => Object.values(parseHoldings(text).holdings).some((shares) => shares > 0);
