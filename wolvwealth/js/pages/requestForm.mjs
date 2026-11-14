// Converting between the optimizer form and a /api/optimize/ request.
//
// formToRequest() builds the request the form describes. requestToForm()
// goes the other way, so a request pasted as JSON (for example, written by
// an AI assistant from a brokerage account) can fill the form for review.
// The two are inverses: a request filled into the form and sent again is the
// same request. No React here, so tests can run these with node
// (hence .mjs).

export const TOP_N = ["", "top20", "top50", "top100", "top200", "top300", "top400", "top500"];
export const LOT_METHODS = [
    ["min_tax", "Lowest tax first"],
    ["fifo", "Oldest first (FIFO)"],
    ["hifo", "Highest cost first"],
    ["lifo", "Newest first (LIFO)"],
];
export const DEFAULT_TAX_RATES = { short_term: 0.24, long_term: 0.15 };

export const today = () => new Date().toISOString().slice(0, 10);
export const newLot = () => ({ shares: "", cost_per_share: "", acquired: "" });
export const newHolding = () => ({ ticker: "", shares: "", useLots: false, lots: [newLot()] });

export function defaultForm() {
    return {
        cash: "10000",
        holdings: [newHolding()],
        topN: "top100",
        extraTickers: "",
        maxWeight: "",
        minWeight: "",
        maxPositions: "",
        lotMethod: "min_tax",
        tradeDate: today(),
        shortRate: String(DEFAULT_TAX_RATES.short_term * 100),
        longRate: String(DEFAULT_TAX_RATES.long_term * 100),
        prices: "",
    };
}

export const formHasLots = (form) => form.holdings.some((h) => h.useLots && h.ticker.trim());

// A fraction from the API as a form percentage: 0.07 -> "7", not "7.000000000000001".
const toPercent = (fraction) => String(Number((fraction * 100).toFixed(6)));
const fromPercent = (percent) => Number((Number(percent) / 100).toFixed(8));

export function formToRequest(form) {
    const body = {};
    if (form.cash !== "") body.initial_cash = Number(form.cash);

    const initialHoldings = {};
    for (const h of form.holdings) {
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
    if (form.topN) universe.push(form.topN);
    for (const t of form.extraTickers.split(/[\s,]+/)) if (t) universe.push(t.toUpperCase());
    if (universe.length) body.universe = universe;

    const constraints = {};
    if (form.maxWeight !== "") constraints.max_weight = fromPercent(form.maxWeight);
    if (form.minWeight !== "") constraints.min_universal_weight = fromPercent(form.minWeight);
    if (form.maxPositions !== "") constraints.max_positions = Number(form.maxPositions);
    if (Object.keys(constraints).length) body.constraints = constraints;

    if (formHasLots(form)) {
        body.lot_method = form.lotMethod;
        if (form.tradeDate) body.trade_date = form.tradeDate;
        body.tax_rates = { short_term: fromPercent(form.shortRate), long_term: fromPercent(form.longRate) };
    }

    const prices = {};
    for (const line of form.prices.split(/[\n,;]+/)) {
        const [ticker, price] = line.trim().split(/[\s:=]+/);
        if (ticker && price) prices[ticker.toUpperCase()] = Number(price);
    }
    if (Object.keys(prices).length) body.prices = prices;
    return body;
}

// Pasting

// Return the JSON in pasted text: the first ```json (or plain ```) code
// block if there is one, otherwise everything from the first "{" to the
// last "}", so a reply with a sentence before or after the JSON still works.
export function extractJson(text) {
    // Fences count only at the start of a line, so "a ```json block" in a sentence isn't one.
    const fences = [...text.matchAll(/^[ \t]*```([a-zA-Z]*)[ \t]*\n([\s\S]*?)^[ \t]*```/gm)];
    const fence = fences.find((m) => m[1].toLowerCase() === "json") || fences[0];
    if (fence) return fence[2].trim();
    const start = text.indexOf("{");
    const end = text.lastIndexOf("}");
    return start !== -1 && end > start ? text.slice(start, end + 1) : text.trim();
}

// Return the paths of keys that appear more than once in the same object of
// valid JSON text. JSON.parse keeps only the last one, so repeats would
// otherwise vanish without a word. Keys of initial_holdings and prices are
// compared in upper case, as the API compares tickers.
export function findDuplicateKeys(text) {
    const tickerObjects = new Set(["initial_holdings", "prices"]);
    const duplicates = [];
    let i = 0;
    const skipSpace = () => {
        while (i < text.length && /\s/.test(text[i])) i++;
    };
    const readString = () => {
        let j = i + 1;
        while (text[j] !== '"') j += text[j] === "\\" ? 2 : 1;
        const value = JSON.parse(text.slice(i, j + 1));
        i = j + 1;
        return value;
    };
    const readValue = (path) => {
        skipSpace();
        if (text[i] === "{") {
            i++;
            const seen = new Set();
            skipSpace();
            if (text[i] === "}") return void i++;
            for (;;) {
                skipSpace();
                const key = readString();
                skipSpace();
                i++; // The colon.
                const compared = tickerObjects.has(path) ? key.toUpperCase() : key;
                const keyPath = path ? `${path}.${key}` : key;
                if (seen.has(compared)) duplicates.push(keyPath);
                seen.add(compared);
                readValue(keyPath);
                skipSpace();
                if (text[i++] === "}") return;
            }
        }
        if (text[i] === "[") {
            i++;
            skipSpace();
            if (text[i] === "]") return void i++;
            for (let k = 0; ; k++) {
                readValue(`${path}[${k}]`);
                skipSpace();
                if (text[i++] === "]") return;
            }
        }
        if (text[i] === '"') return void readString();
        while (i < text.length && !/[\s,\]}]/.test(text[i])) i++; // A number, true, false or null.
    };
    readValue("");
    return duplicates;
}

// Turn pasted text into a request object, or throw an Error saying what's wrong.
export function parsePastedRequest(text) {
    const json = extractJson(text);
    if (!json) throw new Error("There's nothing to read. Paste the JSON your assistant gave you.");
    let request;
    try {
        request = JSON.parse(json);
    } catch (e) {
        throw new Error(`That isn't valid JSON: ${e.message}`);
    }
    if (request === null || typeof request !== "object" || Array.isArray(request)) {
        throw new Error("The JSON must be one object, starting with { and ending with }.");
    }
    const duplicates = findDuplicateKeys(json);
    if (duplicates.length) {
        const [first] = duplicates;
        const ticker = first.split(".").slice(1).join(".");
        if (first.startsWith("initial_holdings.") || first.startsWith("prices.")) {
            const field = first.split(".")[0];
            throw new Error(
                `${ticker.toUpperCase()} is listed more than once in ${field}. List each ticker once` +
                    (field === "initial_holdings" ? ", with all of its lots in one list." : "."),
            );
        }
        throw new Error(`"${first}" appears more than once in the JSON. Keep only one.`);
    }
    return request;
}

// A number, or a string that's plainly one ("1,234.50", "$12"), or undefined.
function toNumber(value) {
    if (typeof value === "number" && Number.isFinite(value)) return value;
    if (typeof value === "string") {
        const cleaned = value.replace(/[$,\s]/g, "");
        if (/^-?\d+(\.\d+)?$/.test(cleaned)) return Number(cleaned);
    }
    return undefined;
}

const isDate = (value) => typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value);
const isObject = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
const KNOWN_FIELDS = [
    "initial_cash", "initial_holdings", "universe", "constraints", "exclude_metrics",
    "prices", "lot_method", "tax_rates", "trade_date",
];

// Return {form, notes, errors} for a parsed request. errors means the form
// shouldn't be filled; notes are things worth telling the user (fields left
// out, values converted). Fields the request leaves out get what the API
// would use: no cash, the top 500, no constraints, today, default rates.
export function requestToForm(request) {
    const errors = [];
    const notes = [];
    const form = { ...defaultForm(), cash: "", topN: "top500" };
    let convertedText = false;
    const number = (value, where, { min = -Infinity, integer = false } = {}) => {
        const n = toNumber(value);
        if (n === undefined) {
            errors.push(`${where} must be a number, not ${JSON.stringify(value)}.`);
            return "";
        }
        if (typeof value === "string") convertedText = true;
        if (n < min || (integer && !Number.isInteger(n))) {
            errors.push(`${where} must be ${integer ? "a whole number" : "a number"}${min > -Infinity ? ` of ${min} or more` : ""}.`);
        }
        return String(n);
    };

    for (const key of Object.keys(request)) {
        if (!KNOWN_FIELDS.includes(key)) notes.push(`Left out "${key}", which WolvWealth doesn't use.`);
    }
    if (request.exclude_metrics !== undefined) notes.push("Left out exclude_metrics; the page always shows metrics.");

    if (request.initial_cash !== undefined) form.cash = number(request.initial_cash, "initial_cash", { min: 0 });

    if (request.initial_holdings !== undefined) {
        if (!isObject(request.initial_holdings)) {
            errors.push("initial_holdings must be an object of tickers, like {\"AAPL\": 10}.");
        } else {
            form.holdings = Object.entries(request.initial_holdings).map(([ticker, value]) => {
                const holding = { ...newHolding(), ticker: ticker.toUpperCase() };
                if (!Array.isArray(value)) {
                    holding.shares = number(value, `${ticker} shares`, { min: 0 });
                    return holding;
                }
                if (value.length === 0) errors.push(`${ticker} has an empty list of lots.`);
                holding.useLots = true;
                holding.lots = value.map((lot, k) => {
                    const where = `${ticker} lot ${k + 1}`;
                    if (!isObject(lot)) {
                        errors.push(`${where} must be an object with shares, cost_per_share and acquired.`);
                        return newLot();
                    }
                    for (const field of ["shares", "cost_per_share", "acquired"]) {
                        if (lot[field] === undefined) errors.push(`${where} is missing ${field}.`);
                    }
                    if (lot.acquired !== undefined && !isDate(lot.acquired)) {
                        errors.push(`${where}: acquired must be a date like 2024-03-15, not ${JSON.stringify(lot.acquired)}.`);
                    }
                    return {
                        shares: lot.shares === undefined ? "" : number(lot.shares, `${where} shares`, { min: 0 }),
                        cost_per_share:
                            lot.cost_per_share === undefined ? "" : number(lot.cost_per_share, `${where} cost_per_share`, { min: 0 }),
                        acquired: isDate(lot.acquired) ? lot.acquired : "",
                    };
                });
                return holding;
            });
            if (form.holdings.length === 0) form.holdings = [newHolding()];
        }
    }

    if (request.universe !== undefined) {
        if (!Array.isArray(request.universe) || request.universe.some((t) => typeof t !== "string")) {
            errors.push("universe must be a list of tickers, like [\"top100\", \"BND\"].");
        } else if (request.universe.length) {
            const aliases = request.universe.filter((t) => TOP_N.includes(t.toLowerCase()) && t);
            // topN aliases overlap, so the largest one covers the rest.
            form.topN = aliases.length
                ? aliases.map((t) => t.toLowerCase()).sort((a, b) => Number(b.slice(3)) - Number(a.slice(3)))[0]
                : "";
            form.extraTickers = request.universe
                .filter((t) => !aliases.includes(t))
                .map((t) => t.toUpperCase())
                .join(" ");
        }
    }

    if (request.constraints !== undefined) {
        const c = request.constraints;
        if (!isObject(c)) {
            errors.push("constraints must be an object.");
        } else {
            if (c.max_weight !== undefined) form.maxWeight = toPercentField(c.max_weight, "max_weight");
            if (c.min_universal_weight !== undefined) form.minWeight = toPercentField(c.min_universal_weight, "min_universal_weight");
            if (c.max_positions !== undefined) form.maxPositions = number(c.max_positions, "max_positions", { min: 1, integer: true });
            for (const key of Object.keys(c)) {
                if (!["max_weight", "min_universal_weight", "max_positions"].includes(key)) {
                    notes.push(`Left out constraints.${key}, which WolvWealth doesn't use.`);
                }
            }
        }
    }

    if (request.lot_method !== undefined) {
        if (LOT_METHODS.some(([value]) => value === request.lot_method)) form.lotMethod = request.lot_method;
        else errors.push(`lot_method must be one of ${LOT_METHODS.map(([v]) => v).join(", ")}.`);
    }
    if (request.trade_date !== undefined) {
        if (isDate(request.trade_date)) form.tradeDate = request.trade_date;
        else errors.push(`trade_date must be a date like 2026-11-14, not ${JSON.stringify(request.trade_date)}.`);
    }
    if (request.tax_rates !== undefined) {
        if (!isObject(request.tax_rates)) errors.push("tax_rates must be an object with short_term and long_term.");
        else {
            if (request.tax_rates.short_term !== undefined) form.shortRate = toPercentField(request.tax_rates.short_term, "tax_rates.short_term");
            if (request.tax_rates.long_term !== undefined) form.longRate = toPercentField(request.tax_rates.long_term, "tax_rates.long_term");
        }
    }

    if (request.prices !== undefined) {
        if (!isObject(request.prices)) errors.push("prices must be an object of tickers to prices, like {\"AAPL\": 355.63}.");
        else {
            form.prices = Object.entries(request.prices)
                .map(([ticker, price]) => `${ticker.toUpperCase()} ${number(price, `${ticker} price`, { min: 0 })}`)
                .join("\n");
        }
    }

    if (convertedText) notes.push("Some numbers were written as text; they were read as numbers.");
    return { form, notes, errors };

    // A fraction from 0 to 1 as a form percentage.
    function toPercentField(value, where) {
        const n = toNumber(value);
        if (n === undefined || n < 0 || n > 1) {
            errors.push(`${where} must be a fraction from 0 to 1 (0.2 means 20%), not ${JSON.stringify(value)}.`);
            return "";
        }
        return toPercent(n);
    }
}
