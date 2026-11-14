// Tests for wolvwealth/js/pages/resultsMarkdown.mjs.
import assert from "node:assert/strict";
import { test } from "node:test";

import { resultsMarkdown } from "../../wolvwealth/js/pages/resultsMarkdown.mjs";

// Trimmed from a real response: NVDA held as lots, KO as a share count, NFE flagged.
const FULL = {
    metrics: { portfolio_value: 6949.18, expected_annual_return: 0.231, annual_volatility: 0.112, sharpe_ratio: 2.07, num_assets: 3 },
    optimized_portfolio: {
        MA: { percent_weight: 23.19, shares: 2.4388, value: 1611.38 },
        NVDA: { percent_weight: 11.82, shares: 4.7268, value: 821.18 },
        KO: { percent_weight: 12.19, shares: 8.6799, value: 846.9 },
    },
    trades: [
        {
            action: "sell", ticker: "NVDA", shares: 25.2732, price: 173.73, value: 4390.71, realized_gain: -649.44,
            lots: [
                { acquired: "2024-06-10", shares: 12.0, cost_per_share: 300.0, term: "long", gain: -1515.24 },
                { acquired: "2026-10-25", shares: 8.0, cost_per_share: 170.0, term: "short", gain: 29.84 },
                { acquired: "2021-05-03", shares: 5.2732, cost_per_share: 15.2, term: "long", gain: 835.96 },
            ],
        },
        { action: "buy", ticker: "MA", shares: 2.4388, price: 660.73, value: 1611.39 },
        { action: "sell", ticker: "NFE", shares: 100, price: 3.47, value: 347.0 },
        { action: "buy", ticker: "KO", shares: 4.6799, price: 97.57, value: 456.62 },
    ],
    data_warnings: [{ ticker: "NFE", message: "NFE's price history has 988 days at or below $0." }],
    tax_summary: {
        trade_date: "2026-11-14", lot_method: "min_tax", tax_rates: { short_term: 0.24, long_term: 0.15 },
        short_term_gain: 29.84, long_term_gain: -679.28, total_gain: -649.44,
        unknown_basis: ["NFE"], priced_from_price_data: [], price_data_date: "2026-11-11",
        wash_sale_warnings: [{ ticker: "NVDA", loss: -1515.24, recent_purchases: ["2026-10-25"], message: "This may be a wash sale." }],
    },
    price_sources: { price_data_date: "2026-11-11", from_request: ["NVDA"], from_price_data: ["KO", "MA", "NFE"] },
};

test("starts with the ground rules, including confirming every order", () => {
    const md = resultsMarkdown(FULL, { priceDate: "2026-11-11" });
    assert.ok(md.startsWith("# WolvWealth results to review\n"));
    assert.match(md, /Prices are from 2026-11-11, except NVDA, which uses a price I entered\./);
    assert.match(md, /Don't place, change or cancel any order unless I explicitly confirm that specific order\./);
    assert.match(md, /sell exactly those lots \(specific lot identification\)/);
    assert.ok(md.indexOf("## Ground rules") < md.indexOf("## Trades, in order"));
});

test("lists sells before buys, numbered, with exact share counts", () => {
    const md = resultsMarkdown(FULL);
    const rows = md.split("\n").filter((l) => /^\| \d+ \|/.test(l));
    assert.deepEqual(rows, [
        "| 1 | SELL | NVDA | 25.2732 | $173.73 | $4,390.71 |",
        "| 2 | SELL | NFE | 100 | $3.47 | $347.00 |",
        "| 3 | BUY | MA | 2.4388 | $660.73 | $1,611.39 |",
        "| 4 | BUY | KO | 4.6799 | $97.57 | $456.62 |",
    ]);
});

test("gives each sell's lots under its trade number", () => {
    const md = resultsMarkdown(FULL);
    assert.match(md, /### Lots for trade 1: sell 25\.2732 NVDA\n\n\| Bought \| Shares \| Cost\/share \| Term \| Gain \|/);
    assert.match(md, /\| 2024-06-10 \| 12 \| \$300\.00 \| long \| -\$1,515\.24 \|/);
    assert.match(md, /\| 2021-05-03 \| 5\.2732 \| \$15\.20 \| long \| \+\$835\.96 \|/);
    assert.match(md, /Realized gain on this sale: -\$649\.44\./);
});

test("includes data warnings, gains, wash sales and the target", () => {
    const md = resultsMarkdown(FULL);
    assert.match(md, /## Check this price data first\n\n- \*\*NFE\*\*: NFE's price history has 988 days at or below \$0\./);
    assert.match(md, /- Long-term: -\$679\.28/);
    assert.match(md, /\*\*Possible wash sale, NVDA\*\* \(bought 2026-10-25\): This may be a wash sale\./);
    assert.match(md, /No gain figure for NFE: entered as a share count, not lots\./);
    assert.match(md, /\| MA \| 23\.19% \| 2\.4388 \| \$1,611\.38 \|/);
    assert.match(md, /Portfolio value \$6,949\.18; expected annual return 23\.1%, volatility 11\.2%, Sharpe ratio 2\.07, 3 positions\./);
    // Target sorted by weight.
    assert.ok(md.indexOf("| KO | 12.19%") < md.indexOf("| NVDA | 11.82%"));
});

test("leaves out sections that don't apply", () => {
    const md = resultsMarkdown(
        {
            optimized_portfolio: { AAPL: { percent_weight: 100, shares: 2.8, value: 1000 } },
            trades: [{ action: "buy", ticker: "AAPL", shares: 2.8, price: 357.14, value: 1000 }],
        },
        { priceDate: "2026-11-11" },
    );
    for (const absent of ["## Check this price data", "### Lots", "## Realized gains", "specific lot identification", "Portfolio value", "except"]) {
        assert.ok(!md.includes(absent), absent);
    }
    assert.match(md, /Prices are from 2026-11-11\./);
});

test("says so when there's nothing to trade", () => {
    const md = resultsMarkdown({ optimized_portfolio: {}, trades: [] });
    assert.match(md, /## Trades, in order\n\nNone: the account already matches the target\./);
});


test("lists read naturally", () => {
    const md = resultsMarkdown({
        ...FULL,
        price_sources: { ...FULL.price_sources, from_request: ["KO", "NVDA"] },
        tax_summary: { ...FULL.tax_summary, unknown_basis: ["NFE", "XOM", "KO"] },
    });
    assert.match(md, /except KO and NVDA, which use prices I entered\./);
    assert.match(md, /No gain figure for NFE, XOM and KO: entered as share counts, not lots\./);
});
