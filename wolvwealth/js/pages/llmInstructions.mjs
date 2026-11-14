// Markdown instructions a user pastes into an AI assistant (one that can see
// their brokerage account, e.g. through an MCP connection) so it replies with
// JSON the optimizer page can import. The example is built from an object, so
// it's always valid JSON; tests import it with parsePastedRequest(). .mjs so
// node can run the tests without a build.

export const EXAMPLE_REQUEST = {
    initial_cash: 2500,
    initial_holdings: {
        AAPL: [
            { shares: 10, cost_per_share: 152.3, acquired: "2023-04-17" },
            { shares: 4.5, cost_per_share: 228.1, acquired: "2025-12-02" },
        ],
        MSFT: 12.5,
        "BRK-B": 3,
    },
    prices: { AAPL: 355.63, MSFT: 523.0, "BRK-B": 589.83 },
    trade_date: "2026-11-14",
};

const FENCE = "```";

export function llmInstructions({ today } = {}) {
    const example = { ...EXAMPLE_REQUEST, trade_date: today || EXAMPLE_REQUEST.trade_date };
    return [
        "# Format my portfolio for WolvWealth",
        "",
        "I use WolvWealth to optimize my stock portfolio. Using what you can see in my brokerage account, " +
            "build the input it needs and reply with it as one JSON code block that I'll paste into the site.",
        "",
        "## What to include",
        "",
        "- `initial_cash`: the cash I want invested, in dollars. Use settled cash or buying power, never margin. " +
            "If I gave you an amount, use that instead.",
        "- `initial_holdings`: every stock and ETF I hold, keyed by ticker.",
        "  - If you can see each tax lot's purchase date and cost per share, give the ticker a list of lots: " +
            '`[{"shares": 10, "cost_per_share": 152.30, "acquired": "2023-04-17"}]`.',
        "  - Otherwise give the total share count as a number: `\"MSFT\": 12.5`. An average cost is not a lot; " +
            "don't turn it into one.",
        "- `prices`: the current price of each holding, if you can see live quotes.",
        `- \`trade_date\`: today's date (${example.trade_date}), if any holding has lots.`,
        "",
        "## Rules",
        "",
        "- Tickers in upper case. Share classes use a dash: `BRK-B`, not `BRK.B` or `BRK/B`.",
        "- List each ticker once, with all of its lots in one list. If I have more than one account, ask me " +
            "which to use; only combine them if I say so.",
        "- `cost_per_share` is per share, not the lot's total cost. If you only see the total, divide it by the shares.",
        "- Dates are `YYYY-MM-DD`. Numbers are plain JSON numbers: no `$`, no commas, no quotes.",
        "- Leave out options, crypto, bonds, cash sweep or money market funds, and anything not traded on a " +
            "US stock exchange. Say what you left out after the JSON.",
        "- Never guess or make up a value. If you can't see something, leave it out (or use a share count " +
            "instead of lots) and say so after the JSON.",
        "",
        "## Format",
        "",
        "Reply with exactly one code block marked `json`, holding a single JSON object and nothing else " +
            "(no comments). A short note after the code block is fine. For example:",
        "",
        `${FENCE}json`,
        JSON.stringify(example, null, 2),
        FENCE,
        "",
        "## Only if I ask",
        "",
        "- `universe`: what to choose from, a list of tickers and/or one of `\"top20\"`, `\"top50\"`, `\"top100\"`, " +
            "`\"top200\"`, `\"top300\"`, `\"top400\"`, `\"top500\"` (the largest US stocks). Leave it out for the top 500.",
        "- `constraints`: `max_weight` (the most in any one stock, as a fraction: 0.2 is 20%), " +
            "`min_universal_weight` (the least in every stock, as a fraction) and `max_positions` (a whole number).",
        "- `lot_method`: which lots to sell first: `\"min_tax\"` (the default), `\"fifo\"`, `\"hifo\"` or `\"lifo\"`.",
        "- `tax_rates`: my rates as fractions, e.g. `{\"short_term\": 0.32, \"long_term\": 0.15}`.",
        "",
        "## Afterwards",
        "",
        "If I paste WolvWealth's results back to you (they start with \"# WolvWealth results to review\"), help me " +
            "check them against my account and follow the ground rules in them. Don't place, change or cancel any " +
            "order unless I explicitly confirm that specific order.",
        "",
    ].join("\n");
}
