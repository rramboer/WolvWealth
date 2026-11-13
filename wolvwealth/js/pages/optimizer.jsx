import React, { useState } from "react";

// The optimizer page: a form that builds a request for POST /api/optimize/,
// and a results view for everything the response can contain. The API key
// and price date come from data attributes on #reactEntry (see
// templates/optimizer.html and views/optimizer.py).

const TOP_N = ["", "top20", "top50", "top100", "top200", "top300", "top400", "top500"];
const LOT_METHODS = [
    ["min_tax", "Lowest tax first"],
    ["fifo", "Oldest first (FIFO)"],
    ["hifo", "Highest cost first"],
    ["lifo", "Newest first (LIFO)"],
];

const today = () => new Date().toISOString().slice(0, 10);
const money = (n) => n.toLocaleString("en-US", { style: "currency", currency: "USD" });
const signedMoney = (n) => (n < 0 ? "-" : "+") + money(Math.abs(n));
const pct = (n, digits = 1) => `${n.toFixed(digits)}%`;
const shares = (n) => Number(n.toFixed(4)).toLocaleString("en-US", { maximumFractionDigits: 4 });

const newLot = () => ({ shares: "", cost_per_share: "", acquired: "" });
const newHolding = () => ({ ticker: "", shares: "", useLots: false, lots: [newLot()] });

// Shared styling.
const input = "w-full rounded-lg bg-gray-dark border border-white/20 text-white text-sm px-3 py-2 " +
    "focus:outline-none focus:border-cyan";
const label = "block text-xs font-medium text-white/70 mb-1";
const card = "rounded-2xl bg-gray-dark/60 border border-white/10 p-5";
const smallButton = "text-xs text-cyan hover:text-white";

function Optimization() {
    const entry = document.getElementById("reactEntry");
    const apiKey = entry?.dataset.apiKey || "";
    const priceDate = entry?.dataset.priceDate || "";
    const [usesLeft, setUsesLeft] = useState(Number(entry?.dataset.uses || 0));

    const [cash, setCash] = useState("10000");
    const [holdings, setHoldings] = useState([newHolding()]);
    const [topN, setTopN] = useState("top100");
    const [extraTickers, setExtraTickers] = useState("");
    const [maxWeight, setMaxWeight] = useState("");
    const [maxPositions, setMaxPositions] = useState("");
    const [lotMethod, setLotMethod] = useState("min_tax");
    const [tradeDate, setTradeDate] = useState(today());
    const [shortRate, setShortRate] = useState("24");
    const [longRate, setLongRate] = useState("15");
    const [prices, setPrices] = useState("");

    const [status, setStatus] = useState("idle"); // idle | loading | done | error
    const [error, setError] = useState("");
    const [result, setResult] = useState(null);

    const anyLots = holdings.some((h) => h.useLots && h.ticker.trim());

    function buildRequest() {
        const body = {};
        if (cash !== "") body.initial_cash = Number(cash);

        const initialHoldings = {};
        for (const h of holdings) {
            const ticker = h.ticker.trim().toUpperCase();
            if (!ticker) continue;
            if (h.useLots) {
                initialHoldings[ticker] = h.lots
                    .filter((lot) => lot.shares !== "" || lot.cost_per_share !== "" || lot.acquired !== "")
                    .map((lot) => ({
                        shares: Number(lot.shares),
                        cost_per_share: Number(lot.cost_per_share),
                        acquired: lot.acquired,
                    }));
            } else {
                initialHoldings[ticker] = Number(h.shares);
            }
        }
        if (Object.keys(initialHoldings).length) body.initial_holdings = initialHoldings;

        const universe = [];
        if (topN) universe.push(topN);
        for (const t of extraTickers.split(/[\s,]+/)) if (t) universe.push(t.toUpperCase());
        if (universe.length) body.universe = universe;

        const constraints = {};
        if (maxWeight !== "") constraints.max_weight = Number(maxWeight) / 100;
        if (maxPositions !== "") constraints.max_positions = Number(maxPositions);
        if (Object.keys(constraints).length) body.constraints = constraints;

        if (anyLots) {
            body.lot_method = lotMethod;
            if (tradeDate) body.trade_date = tradeDate;
            body.tax_rates = { short_term: Number(shortRate) / 100, long_term: Number(longRate) / 100 };
        }

        const priceOverrides = {};
        for (const line of prices.split(/[\n,;]+/)) {
            const [ticker, price] = line.trim().split(/[\s:=]+/);
            if (ticker && price) priceOverrides[ticker.toUpperCase()] = Number(price);
        }
        if (Object.keys(priceOverrides).length) body.prices = priceOverrides;
        return body;
    }

    async function submit(event) {
        event.preventDefault();
        setStatus("loading");
        setError("");
        try {
            const response = await fetch("/api/optimize/", {
                method: "POST",
                headers: { "Content-Type": "application/json", Authorization: apiKey },
                body: JSON.stringify(buildRequest()),
            });
            const text = await response.text();
            let data;
            try {
                data = JSON.parse(text);
            } catch (e) {
                throw new Error(`The server returned something that isn't JSON (HTTP ${response.status}).`);
            }
            if (!response.ok) throw new Error(data?.error?.message || `HTTP ${response.status}`);
            setResult(data);
            setUsesLeft((n) => Math.max(0, n - 1));
            setStatus("done");
        } catch (e) {
            setError(e.message);
            setStatus("error");
        }
    }

    return (
        <section className="w-full max-w-screen-xl mx-auto px-5 pt-28 pb-16 text-white">
            <h1 className="text-3xl font-semibold ubuntu mb-1">Optimize</h1>
            <p className="text-sm text-white/60 mb-8">
                Prices as of {priceDate}. {usesLeft} optimization{usesLeft === 1 ? "" : "s"} left on your key.
            </p>
            <div className="grid grid-cols-1 lg:grid-cols-5 gap-8">
                <form onSubmit={submit} className="lg:col-span-2 space-y-6">
                    <div className={card}>
                        <label className={label} htmlFor="cash">Cash to invest ($)</label>
                        <input id="cash" className={input} type="number" min="0" step="any"
                            value={cash} onChange={(e) => setCash(e.target.value)} />
                    </div>

                    <HoldingsEditor holdings={holdings} setHoldings={setHoldings} />

                    <div className={card + " space-y-4"}>
                        <h2 className="font-semibold">Universe and constraints</h2>
                        <div>
                            <label className={label} htmlFor="topn">Choose from</label>
                            <select id="topn" className={input} value={topN} onChange={(e) => setTopN(e.target.value)}>
                                {TOP_N.map((n) => (
                                    <option key={n} value={n}>
                                        {n ? `Largest ${n.slice(3)} stocks` : "Only the tickers below"}
                                    </option>
                                ))}
                            </select>
                        </div>
                        <div>
                            <label className={label} htmlFor="extra">Also include these tickers</label>
                            <input id="extra" className={input} placeholder="AAPL MSFT BND" value={extraTickers}
                                onChange={(e) => setExtraTickers(e.target.value)} />
                        </div>
                        <div className="grid grid-cols-2 gap-4">
                            <div>
                                <label className={label} htmlFor="maxw">Max weight per stock (%)</label>
                                <input id="maxw" className={input} type="number" min="0" max="100" step="any"
                                    placeholder="none" value={maxWeight} onChange={(e) => setMaxWeight(e.target.value)} />
                            </div>
                            <div>
                                <label className={label} htmlFor="maxp">Max positions</label>
                                <input id="maxp" className={input} type="number" min="1" step="1" placeholder="none"
                                    value={maxPositions} onChange={(e) => setMaxPositions(e.target.value)} />
                            </div>
                        </div>
                    </div>

                    {anyLots && (
                        <div className={card + " space-y-4"}>
                            <h2 className="font-semibold">Tax lots</h2>
                            <div>
                                <label className={label} htmlFor="method">Sell lots</label>
                                <select id="method" className={input} value={lotMethod}
                                    onChange={(e) => setLotMethod(e.target.value)}>
                                    {LOT_METHODS.map(([value, name]) => <option key={value} value={value}>{name}</option>)}
                                </select>
                            </div>
                            <div className="grid grid-cols-3 gap-4">
                                <div>
                                    <label className={label} htmlFor="tdate">Trade date</label>
                                    <input id="tdate" className={input} type="date" value={tradeDate}
                                        onChange={(e) => setTradeDate(e.target.value)} />
                                </div>
                                <div>
                                    <label className={label} htmlFor="st">Short-term rate (%)</label>
                                    <input id="st" className={input} type="number" min="0" max="100" step="any"
                                        value={shortRate} onChange={(e) => setShortRate(e.target.value)} />
                                </div>
                                <div>
                                    <label className={label} htmlFor="lt">Long-term rate (%)</label>
                                    <input id="lt" className={input} type="number" min="0" max="100" step="any"
                                        value={longRate} onChange={(e) => setLongRate(e.target.value)} />
                                </div>
                            </div>
                            <p className="text-xs text-white/50">
                                The rates only decide which lots &ldquo;lowest tax first&rdquo; sells. Gains are reported, not taxes.
                            </p>
                        </div>
                    )}

                    <div className={card}>
                        <label className={label} htmlFor="prices">Current prices (optional)</label>
                        <textarea id="prices" className={input + " h-20"} placeholder={"AAPL 355.63\nNVDA 173.73"}
                            value={prices} onChange={(e) => setPrices(e.target.value)} />
                        <p className="text-xs text-white/50 mt-2">
                            One ticker and price per line. Replaces the {priceDate} price for valuing holdings, trades and gains.
                        </p>
                    </div>

                    <button type="submit" disabled={status === "loading"}
                        className="w-full rounded-lg bg-blue hover:bg-cyan hover:text-black disabled:opacity-50 font-semibold py-3 transition">
                        {status === "loading" ? "Optimizing…" : "Optimize"}
                    </button>
                    {status === "loading" && (
                        <p className="text-xs text-white/50 text-center">
                            The largest universes take up to a minute.
                        </p>
                    )}
                </form>

                <div className="lg:col-span-3 space-y-6">
                    {status === "error" && (
                        <div className="rounded-2xl border border-red bg-red/10 p-5">
                            <h2 className="font-semibold text-red mb-1">That didn&rsquo;t work</h2>
                            <p className="text-sm">{error}</p>
                        </div>
                    )}
                    {result && <Results result={result} priceDate={priceDate} />}
                    {!result && status !== "error" && (
                        <div className={card + " text-white/50 text-sm"}>
                            Enter what you have, choose what to pick from, and optimize. You&rsquo;ll get the target
                            portfolio, the trades to get there and, if you enter tax lots, which lots to sell.
                        </div>
                    )}
                </div>
            </div>
        </section>
    );
}

function HoldingsEditor({ holdings, setHoldings }) {
    const update = (i, patch) => setHoldings(holdings.map((h, j) => (j === i ? { ...h, ...patch } : h)));
    const updateLot = (i, k, patch) =>
        update(i, { lots: holdings[i].lots.map((lot, l) => (l === k ? { ...lot, ...patch } : lot)) });
    const remove = (i) => setHoldings(holdings.filter((_, j) => j !== i));

    return (
        <div className={card + " space-y-4"}>
            <div className="flex items-baseline justify-between">
                <h2 className="font-semibold">What you already hold</h2>
                <button type="button" className={smallButton} onClick={() => setHoldings([...holdings, newHolding()])}>
                    + Add a holding
                </button>
            </div>
            {holdings.map((h, i) => (
                <div key={i} className="rounded-xl border border-white/10 p-3 space-y-3">
                    <div className="flex gap-3 items-end">
                        <div className="w-28">
                            <label className={label}>Ticker</label>
                            <input className={input + " uppercase"} placeholder="AAPL" value={h.ticker}
                                onChange={(e) => update(i, { ticker: e.target.value })} />
                        </div>
                        <div className="flex-1">
                            <label className={label}>Shares</label>
                            <input className={input} type="number" min="0" step="any" disabled={h.useLots}
                                value={h.useLots ? lotTotal(h.lots) : h.shares}
                                onChange={(e) => update(i, { shares: e.target.value })} />
                        </div>
                        <button type="button" className="text-white/40 hover:text-red pb-2" title="Remove"
                            onClick={() => remove(i)}>✕</button>
                    </div>
                    <label className="flex items-center gap-2 text-xs text-white/70">
                        <input type="checkbox" checked={h.useLots} onChange={(e) => update(i, { useLots: e.target.checked })} />
                        Enter tax lots (to see which lots to sell and the gains)
                    </label>
                    {h.useLots && (
                        <div className="space-y-2">
                            <div className="grid grid-cols-[1fr_1fr_1fr_1.5rem] gap-2 text-xs text-white/50">
                                <span>Shares</span><span>Cost per share ($)</span><span>Bought on</span><span />
                            </div>
                            {h.lots.map((lot, k) => (
                                <div key={k} className="grid grid-cols-[1fr_1fr_1fr_1.5rem] gap-2 items-center">
                                    <input className={input} type="number" min="0" step="any" value={lot.shares}
                                        onChange={(e) => updateLot(i, k, { shares: e.target.value })} />
                                    <input className={input} type="number" min="0" step="any" value={lot.cost_per_share}
                                        onChange={(e) => updateLot(i, k, { cost_per_share: e.target.value })} />
                                    <input className={input} type="date" value={lot.acquired}
                                        onChange={(e) => updateLot(i, k, { acquired: e.target.value })} />
                                    <button type="button" className="text-white/40 hover:text-red" title="Remove lot"
                                        onClick={() => update(i, { lots: h.lots.filter((_, l) => l !== k) })}>✕</button>
                                </div>
                            ))}
                            <button type="button" className={smallButton}
                                onClick={() => update(i, { lots: [...h.lots, newLot()] })}>
                                + Add a lot
                            </button>
                        </div>
                    )}
                </div>
            ))}
        </div>
    );
}

function lotTotal(lots) {
    const total = lots.reduce((sum, lot) => sum + (Number(lot.shares) || 0), 0);
    return total ? String(Number(total.toFixed(8))) : "";
}

function Results({ result, priceDate }) {
    const warnings = Object.fromEntries((result.data_warnings || []).map((w) => [w.ticker, w]));
    const portfolio = Object.entries(result.optimized_portfolio).sort((a, b) => b[1].percent_weight - a[1].percent_weight);
    const sells = result.trades.filter((t) => t.action === "sell");
    const buys = result.trades.filter((t) => t.action === "buy");

    return (
        <>
            {result.data_warnings && <DataWarnings warnings={result.data_warnings} />}

            {result.metrics && <Metrics metrics={result.metrics} />}

            <div className={card}>
                <h2 className="font-semibold mb-3">Target portfolio</h2>
                <table className="w-full text-sm">
                    <thead className="text-xs text-white/50 text-left">
                        <tr><th className="pb-2">Ticker</th><th className="pb-2 text-right">Weight</th>
                            <th className="pb-2 text-right">Shares</th><th className="pb-2 text-right">Value</th></tr>
                    </thead>
                    <tbody>
                        {portfolio.map(([ticker, p]) => (
                            <tr key={ticker} className="border-t border-white/10">
                                <td className="py-1.5"><Ticker ticker={ticker} warning={warnings[ticker]} /></td>
                                <td className="py-1.5 text-right">{pct(p.percent_weight, 2)}</td>
                                <td className="py-1.5 text-right">{shares(p.shares)}</td>
                                <td className="py-1.5 text-right">{money(p.value)}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>

            <div className={card}>
                <h2 className="font-semibold mb-1">Trades, in order</h2>
                <p className="text-xs text-white/50 mb-3">
                    Sell first so the proceeds can pay for the buys. Largest first on each side.
                </p>
                {result.trades.length === 0 && <p className="text-sm text-white/60">Nothing to trade; you already hold the target.</p>}
                <ol className="space-y-2">
                    {[...sells, ...buys].map((t, i) => <Trade key={t.ticker + t.action} n={i + 1} trade={t} warning={warnings[t.ticker]} />)}
                </ol>
            </div>

            {result.tax_summary && <TaxSummary summary={result.tax_summary} />}

            {result.price_sources && (
                <p className="text-xs text-white/50 px-1">
                    Prices from your request: {result.price_sources.from_request.join(", ") || "none"}.
                    From the {result.price_sources.price_data_date} data: {result.price_sources.from_price_data.join(", ") || "none"}.
                </p>
            )}
            {!result.price_sources && (
                <p className="text-xs text-white/50 px-1">All prices are from {priceDate}.</p>
            )}
        </>
    );
}

function Ticker({ ticker, warning }) {
    return (
        <span className="inline-flex items-center gap-1.5 font-medium">
            {ticker}
            {warning && (
                <a href="#data-warnings" title={warning.message}
                    className="rounded px-1 text-[10px] font-bold bg-yellow text-black leading-4">
                    CHECK DATA
                </a>
            )}
        </span>
    );
}

function DataWarnings({ warnings }) {
    return (
        <div id="data-warnings" className="rounded-2xl border border-yellow bg-yellow/10 p-5 space-y-4">
            <h2 className="font-semibold text-yellow">
                Check the price data for {warnings.map((w) => w.ticker).join(", ")} before trading
            </h2>
            {warnings.map((w) => (
                <div key={w.ticker} className="text-sm space-y-1">
                    <p>{w.message}</p>
                    <ul className="text-xs text-white/70 list-disc pl-5">
                        {w.non_positive_prices && (
                            <li>{w.non_positive_prices.days} days at or below $0, {w.non_positive_prices.from} to {w.non_positive_prices.to}</li>
                        )}
                        {w.large_moves && (
                            <li>
                                One-day moves over 90%: {w.large_moves.map((m) =>
                                    `${m.date} (${m.change_percent > 0 ? "+" : ""}${m.change_percent.toLocaleString()}%)`).join(", ")}
                            </li>
                        )}
                    </ul>
                </div>
            ))}
        </div>
    );
}

function Metrics({ metrics }) {
    const items = [
        ["Portfolio value", money(metrics.portfolio_value)],
        ["Expected return", pct(metrics.expected_annual_return * 100)],
        ["Volatility", pct(metrics.annual_volatility * 100)],
        ["Sharpe ratio", metrics.sharpe_ratio.toFixed(2)],
        ["Positions", metrics.num_assets],
    ];
    return (
        <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
            {items.map(([name, value]) => (
                <div key={name} className={card + " !p-4"}>
                    <div className="text-xs text-white/50">{name}</div>
                    <div className="text-lg font-semibold">{value}</div>
                </div>
            ))}
        </div>
    );
}

function Trade({ n, trade, warning }) {
    const sell = trade.action === "sell";
    return (
        <li className="rounded-xl border border-white/10 p-3">
            <div className="flex items-center gap-3 text-sm">
                <span className="text-white/40 w-5">{n}.</span>
                <span className={"rounded px-2 py-0.5 text-xs font-bold uppercase " + (sell ? "bg-red text-black" : "bg-green text-black")}>
                    {trade.action}
                </span>
                <Ticker ticker={trade.ticker} warning={warning} />
                <span className="text-white/70">{shares(trade.shares)} shares @ {money(trade.price)}</span>
                <span className="ml-auto font-medium">{money(trade.value)}</span>
            </div>
            {trade.lots && (
                <div className="mt-3 ml-8">
                    <table className="w-full text-xs">
                        <thead className="text-white/50 text-left">
                            <tr><th className="pb-1">Lot bought</th><th className="pb-1 text-right">Shares</th>
                                <th className="pb-1 text-right pr-4">Cost/share</th><th className="pb-1">Term</th><th className="pb-1 text-right">Gain</th></tr>
                        </thead>
                        <tbody>
                            {trade.lots.map((lot, i) => (
                                <tr key={i} className="border-t border-white/10">
                                    <td className="py-1">{lot.acquired}</td>
                                    <td className="py-1 text-right">{shares(lot.shares)}</td>
                                    <td className="py-1 text-right pr-4">{money(lot.cost_per_share)}</td>
                                    <td className="py-1">{lot.term}</td>
                                    <td className={"py-1 text-right " + (lot.gain < 0 ? "text-red" : "text-green")}>{signedMoney(lot.gain)}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                    <p className="text-xs mt-2 text-white/70">
                        Realized gain on this sale:{" "}
                        <span className={trade.realized_gain < 0 ? "text-red" : "text-green"}>{signedMoney(trade.realized_gain)}</span>.
                        Tell your broker to sell these specific lots.
                    </p>
                </div>
            )}
        </li>
    );
}

function TaxSummary({ summary }) {
    const gains = [
        ["Short-term", summary.short_term_gain],
        ["Long-term", summary.long_term_gain],
        ["Total", summary.total_gain],
    ];
    const method = LOT_METHODS.find(([v]) => v === summary.lot_method)?.[1] || summary.lot_method;
    return (
        <div className={card + " space-y-4"}>
            <h2 className="font-semibold">Realized gains if you trade on {summary.trade_date}</h2>
            <div className="grid grid-cols-3 gap-3">
                {gains.map(([name, value]) => (
                    <div key={name}>
                        <div className="text-xs text-white/50">{name}</div>
                        <div className={"text-lg font-semibold " + (value < 0 ? "text-red" : value > 0 ? "text-green" : "")}>
                            {signedMoney(value)}
                        </div>
                    </div>
                ))}
            </div>
            {summary.wash_sale_warnings.map((w) => (
                <div key={w.ticker} className="rounded-xl border border-yellow bg-yellow/10 p-3 text-sm">
                    <span className="font-semibold text-yellow">Possible wash sale: </span>{w.message}{" "}
                    <span className="text-white/60">(bought {w.recent_purchases.join(", ")}; loss {money(w.loss)})</span>
                </div>
            ))}
            <ul className="text-xs text-white/60 space-y-1 list-disc pl-5">
                <li>Lots chosen: {method.toLowerCase()}, assuming {pct(summary.tax_rates.short_term * 100, 0)} short-term and {pct(summary.tax_rates.long_term * 100, 0)} long-term rates.</li>
                {summary.unknown_basis.length > 0 && (
                    <li>No gain shown for {summary.unknown_basis.join(", ")}: entered as a share count, not lots.</li>
                )}
                {summary.priced_from_price_data.length > 0 && (
                    <li>Gains for {summary.priced_from_price_data.join(", ")} use the {summary.price_data_date} price. Enter current prices for today&rsquo;s.</li>
                )}
                <li>Estimates, not tax advice. Other accounts, purchases after the sale, carryovers and state taxes aren&rsquo;t included.</li>
            </ul>
        </div>
    );
}

export default Optimization;
