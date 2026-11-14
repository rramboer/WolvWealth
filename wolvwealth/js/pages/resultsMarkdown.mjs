// Markdown of an optimization result for pasting back into an AI assistant,
// so it can check the trades against the brokerage account with the user.
// The other half of the round trip in llmInstructions.mjs. No React, so
// node can test it (hence .mjs).

const money = (n) => (n < 0 ? "-" : "") + "$" + Math.abs(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const signedMoney = (n) => (n < 0 ? "-" : "+") + money(Math.abs(n));
// Share counts exactly as the API gave them, without float noise or trailing zeros.
const shareCount = (n) => String(Number(n.toFixed(8)));
const pct = (n, digits = 1) => `${n.toFixed(digits)}%`;
// "A", "A and B", "A, B and C".
const list = (items) => (items.length < 2 ? items.join("") : `${items.slice(0, -1).join(", ")} and ${items.at(-1)}`);

function table(headers, rows) {
    return [
        `| ${headers.join(" | ")} |`,
        `| ${headers.map((h) => (/^(Shares|Price|Value|Cost\/share|Gain|Weight)$/.test(h) ? "---:" : "---")).join(" | ")} |`,
        ...rows.map((row) => `| ${row.join(" | ")} |`),
    ];
}

// result is the /api/optimize/ response; priceDate is the date of the price
// data the page shows.
export function resultsMarkdown(result, { priceDate } = {}) {
    const lines = [];
    const sells = result.trades.filter((t) => t.action === "sell");
    const buys = result.trades.filter((t) => t.action === "buy");
    const trades = [...sells, ...buys];
    const fromRequest = result.price_sources?.from_request || [];
    const tax = result.tax_summary;

    lines.push("# WolvWealth results to review", "");
    let pricing = `Prices are from ${result.price_sources?.price_data_date || priceDate || "the price data"}`;
    if (fromRequest.length) {
        pricing += `, except ${list(fromRequest)}, which use${fromRequest.length === 1 ? "s a price" : " prices"} I entered`;
    }
    lines.push(
        "WolvWealth suggested these trades for my account. Help me check them against what you can see in the " +
            `account before I place anything. ${pricing}.`,
        "",
        "## Ground rules",
        "",
        "- Don't place, change or cancel any order unless I explicitly confirm that specific order.",
        "- Compare each price below with a live quote. The share counts were sized at these prices, so tell me " +
            "about any that have moved more than a few percent before we go on.",
        "- Check that my holdings and cash match what the account shows, and tell me about anything that doesn't.",
        "- Place the sells first; their proceeds pay for the buys.",
    );
    if (trades.some((t) => t.lots)) {
        lines.push(
            "- For each sell with lots listed, sell exactly those lots (specific lot identification). If the account " +
                "can't choose lots, tell me before placing it: the broker's default (usually oldest first) changes the gains.",
        );
    }
    lines.push(
        "- If a ticker can't be traded in fractional shares, tell me, and round buys down rather than up.",
        "",
    );

    if (result.data_warnings?.length) {
        lines.push("## Check this price data first", "");
        for (const w of result.data_warnings) lines.push(`- **${w.ticker}**: ${w.message}`);
        lines.push("");
    }

    lines.push("## Trades, in order", "");
    if (!trades.length) {
        lines.push("None: the account already matches the target.", "");
    } else {
        lines.push(
            ...table(
                ["#", "Action", "Ticker", "Shares", "Price", "Value"],
                trades.map((t, i) => [
                    String(i + 1), t.action.toUpperCase(), t.ticker, shareCount(t.shares), money(t.price), money(t.value),
                ]),
            ),
            "",
        );
    }

    for (const [i, t] of trades.entries()) {
        if (!t.lots) continue;
        lines.push(`### Lots for trade ${i + 1}: sell ${shareCount(t.shares)} ${t.ticker}`, "");
        lines.push(
            ...table(
                ["Bought", "Shares", "Cost/share", "Term", "Gain"],
                t.lots.map((lot) => [lot.acquired, shareCount(lot.shares), money(lot.cost_per_share), lot.term, signedMoney(lot.gain)]),
            ),
            "",
            `Realized gain on this sale: ${signedMoney(t.realized_gain)}.`,
            "",
        );
    }

    if (tax) {
        lines.push(
            `## Realized gains if traded on ${tax.trade_date}`,
            "",
            `- Short-term: ${signedMoney(tax.short_term_gain)}`,
            `- Long-term: ${signedMoney(tax.long_term_gain)}`,
            `- Total: ${signedMoney(tax.total_gain)}`,
        );
        for (const w of tax.wash_sale_warnings) {
            lines.push(`- **Possible wash sale, ${w.ticker}** (bought ${w.recent_purchases.join(", ")}): ${w.message}`);
        }
        if (tax.unknown_basis.length) {
            const one = tax.unknown_basis.length === 1;
            lines.push(`- No gain figure for ${list(tax.unknown_basis)}: entered as ${one ? "a share count" : "share counts"}, not lots.`);
        }
        if (tax.priced_from_price_data.length) {
            lines.push(`- Gains for ${list(tax.priced_from_price_data)} use the ${tax.price_data_date} price, not a current one.`);
        }
        lines.push(
            `- Lots were chosen by ${tax.lot_method}, ranking with ${pct(tax.tax_rates.short_term * 100, 0)} short-term ` +
                `and ${pct(tax.tax_rates.long_term * 100, 0)} long-term rates.`,
            "",
        );
    }

    const portfolio = Object.entries(result.optimized_portfolio).sort((a, b) => b[1].percent_weight - a[1].percent_weight);
    lines.push(
        "## Target portfolio",
        "",
        ...table(
            ["Ticker", "Weight", "Shares", "Value"],
            portfolio.map(([ticker, p]) => [ticker, pct(p.percent_weight, 2), shareCount(p.shares), money(p.value)]),
        ),
        "",
    );
    if (result.metrics) {
        const m = result.metrics;
        lines.push(
            `Portfolio value ${money(m.portfolio_value)}; expected annual return ${pct(m.expected_annual_return * 100)}, ` +
                `volatility ${pct(m.annual_volatility * 100)}, Sharpe ratio ${m.sharpe_ratio.toFixed(2)}, ` +
                `${m.num_assets} positions. These are estimates from historical prices.`,
            "",
        );
    }
    lines.push("_From WolvWealth. Estimates, not financial or tax advice._", "");
    return lines.join("\n");
}
