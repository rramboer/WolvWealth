// Tests for wolvwealth/js/pages/requestForm.mjs and llmInstructions.mjs.
// Run with `npm test` (node --test); no packages needed.
import assert from "node:assert/strict";
import { test } from "node:test";

import { EXAMPLE_REQUEST, llmInstructions } from "../../wolvwealth/js/pages/llmInstructions.mjs";
import {
    defaultForm, extractJson, findDuplicateKeys, formToRequest, parsePastedRequest, requestToForm,
} from "../../wolvwealth/js/pages/requestForm.mjs";

const FULL_REQUEST = {
    initial_cash: 10000,
    initial_holdings: {
        AAPL: [
            { shares: 3, cost_per_share: 120.5, acquired: "2021-03-01" },
            { shares: 2, cost_per_share: 190, acquired: "2026-10-20" },
        ],
        MSFT: 10,
    },
    universe: ["top100", "BND", "BRK-B"],
    constraints: { max_weight: 0.07, min_universal_weight: 0.01, max_positions: 15 },
    lot_method: "hifo",
    tax_rates: { short_term: 0.32, long_term: 0.15 },
    trade_date: "2026-11-14",
    prices: { AAPL: 355.63, MSFT: 523 },
};

function fill(request) {
    const { form, notes, errors } = requestToForm(request);
    assert.deepEqual(errors, []);
    return { form, notes };
}

test("a full request survives the form unchanged", () => {
    const { form, notes } = fill(FULL_REQUEST);
    assert.deepEqual(notes, []);
    assert.deepEqual(formToRequest(form), FULL_REQUEST);
});

test("fields left out get what the API would use", () => {
    const { form } = fill({ initial_holdings: { KO: 4 } });
    assert.equal(form.cash, "");
    assert.equal(form.topN, "top500");
    assert.deepEqual(formToRequest(form), { initial_holdings: { KO: 4 }, universe: ["top500"] });
});

test("the example in the instructions pastes cleanly", () => {
    const text = llmInstructions({ today: "2026-11-14" });
    const request = parsePastedRequest(text);
    assert.deepEqual(request, EXAMPLE_REQUEST);
    const { form, notes } = fill(request);
    assert.deepEqual(notes, []);
    assert.deepEqual(formToRequest(form), {
        ...EXAMPLE_REQUEST,
        universe: ["top500"],
        lot_method: "min_tax",
        tax_rates: { short_term: 0.24, long_term: 0.15 },
    });
});

test("the instructions use today's date", () => {
    assert.match(llmInstructions({ today: "2027-01-05" }), /"trade_date": "2027-01-05"/);
});

test("extractJson finds JSON in an assistant's reply", () => {
    const reply = 'Here you go:\n\n```json\n{"initial_cash": 5}\n```\n\nI left out your crypto.';
    assert.equal(extractJson(reply), '{"initial_cash": 5}');
    assert.equal(extractJson('```\n{"a": 1}\n```'), '{"a": 1}');
    assert.equal(extractJson('Sure! {"a": {"b": 1}} Let me know.'), '{"a": {"b": 1}}');
    // A ```json block wins over an earlier unlabeled one.
    assert.equal(extractJson('```\nnot this\n```\n```json\n{"a": 2}\n```'), '{"a": 2}');
    // A fence mentioned mid-sentence isn't a code block.
    assert.equal(extractJson('Use a ```json block.\n```json\n{"a": 3}\n```'), '{"a": 3}');
});

test("findDuplicateKeys finds repeats JSON.parse would drop", () => {
    const lots = '[{"shares": 1, "cost_per_share": 2, "acquired": "2020-01-01"}]';
    assert.deepEqual(findDuplicateKeys(`{"initial_holdings": {"AAPL": ${lots}, "AAPL": 5}}`), ["initial_holdings.AAPL"]);
    assert.deepEqual(findDuplicateKeys(`{"initial_holdings": {"AAPL": ${lots}, "aapl": 5}}`), ["initial_holdings.aapl"]);
    assert.deepEqual(findDuplicateKeys('{"prices": {"KO": 1, "ko": 2}}'), ["prices.ko"]);
    assert.deepEqual(findDuplicateKeys('{"initial_cash": 1, "initial_cash": 2}'), ["initial_cash"]);
    assert.deepEqual(findDuplicateKeys('{"h": {"A": [{"shares": 1, "shares": 2}]}}'), ["h.A[0].shares"]);
    // Case only matters for tickers; braces and quotes inside strings aren't structure.
    assert.deepEqual(findDuplicateKeys('{"a": 1, "A": 2, "s": "x\\"}{,", "t": "{\\"a\\": 1}"}'), []);
    assert.deepEqual(findDuplicateKeys('{"a": [], "b": {}, "c": [1, 2.5e3, true, null, "x"]}'), []);
});

test("parsePastedRequest explains what's wrong", () => {
    assert.throws(() => parsePastedRequest(""), /nothing to read/);
    assert.throws(() => parsePastedRequest('{"initial_cash": 5,}'), /isn't valid JSON/);
    assert.throws(() => parsePastedRequest("[1, 2]"), /must be one object/);
    assert.throws(
        () => parsePastedRequest('{"initial_holdings": {"AAPL": [{"shares": 1, "cost_per_share": 1, "acquired": "2020-01-01"}], "aapl": 5}}'),
        /AAPL is listed more than once in initial_holdings\. List each ticker once, with all of its lots in one list\./,
    );
    assert.throws(() => parsePastedRequest('{"prices": {"KO": 1, "KO": 2}}'), /KO is listed more than once in prices\. List each ticker once\.$/);
});

test("requestToForm reports bad values instead of filling them", () => {
    const { errors } = requestToForm({
        initial_cash: "lots",
        initial_holdings: {
            AAPL: [{ shares: 1, cost_per_share: 2, acquired: "03/01/2021" }, { shares: 1, acquired: "2021-03-01" }],
            KO: [],
        },
        constraints: { max_weight: 20, max_positions: 2.5 },
        lot_method: "FIFO",
        trade_date: "today",
    });
    assert.deepEqual(errors, [
        'initial_cash must be a number, not "lots".',
        'AAPL lot 1: acquired must be a date like 2024-03-15, not "03/01/2021".',
        "AAPL lot 2 is missing cost_per_share.",
        "KO has an empty list of lots.",
        "max_weight must be a fraction from 0 to 1 (0.2 means 20%), not 20.",
        "max_positions must be a whole number of 1 or more.",
        "lot_method must be one of min_tax, fifo, hifo, lifo.",
        'trade_date must be a date like 2026-11-14, not "today".',
    ]);
});

test("requestToForm notes what it changed or left out", () => {
    const { form, notes, errors } = requestToForm({
        initial_cash: "$1,234.50",
        initial_holdings: { msft: "12.5" },
        exclude_metrics: true,
        account: "Robinhood",
        constraints: { sector_cap: 0.3 },
    });
    assert.deepEqual(errors, []);
    assert.equal(form.cash, "1234.5");
    assert.equal(form.holdings[0].ticker, "MSFT");
    assert.equal(form.holdings[0].shares, "12.5");
    assert.deepEqual(notes, [
        'Left out "account", which WolvWealth doesn\'t use.',
        "Left out exclude_metrics; the page always shows metrics.",
        "Left out constraints.sector_cap, which WolvWealth doesn't use.",
        "Some numbers were written as text; they were read as numbers.",
    ]);
});

test("universe aliases collapse to the largest, the rest are extra tickers", () => {
    const { form } = fill({ universe: ["top50", "bnd", "TOP200"] });
    assert.equal(form.topN, "top200");
    assert.equal(form.extraTickers, "BND");
    assert.equal(fill({ universe: ["AAPL", "MSFT"] }).form.topN, "");
});

test("the default form builds the request the page sent before", () => {
    const form = defaultForm();
    assert.deepEqual(formToRequest(form), { initial_cash: 10000, universe: ["top100"] });
});
