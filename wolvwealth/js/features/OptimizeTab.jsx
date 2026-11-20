import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { ObjectiveFields } from "../components/ObjectiveFields";
import { AllocationTable, TradesTable } from "../components/tables";
import { Alert, Button, Card, Field, NumberInput, TextArea, TextInput } from "../components/ui";
import { optimize } from "../lib/api";
import { formatMoney, formatNumber, formatPercent } from "../lib/format";
import { parseHoldings } from "../lib/holdings";
import { DEFAULT_OBJECTIVE_FORM, buildObjectivePayload, describeObjective } from "../lib/objectives";
import { parseUniverse } from "../lib/series";
import { isValid, validateOptimizeForm } from "../lib/validate";

const DEFAULT_FORM = {
  initialCash: 10000,
  holdings: "",
  universe: "top50",
  ...DEFAULT_OBJECTIVE_FORM,
  maxPositions: 10,
  maxWeight: 1,
  minWeight: 0,
  maxTurnover: "",
  costBps: 0,
};

/** Translate form state into the /api/optimize/ payload. */
export function buildOptimizePayload(form) {
  const constraints = {};
  if (form.maxPositions !== "" && form.maxPositions > 0) constraints.max_positions = form.maxPositions;
  if (form.maxWeight !== "" && form.maxWeight < 1) constraints.max_weight = form.maxWeight;
  if (form.minWeight !== "" && form.minWeight > 0) constraints.min_universal_weight = form.minWeight;
  if (form.maxTurnover !== "" && form.maxTurnover !== undefined) {
    constraints.max_turnover = Number((form.maxTurnover / 100).toPrecision(12));
  }
  const payload = {
    initial_cash: form.initialCash,
    universe: parseUniverse(form.universe),
    objective: buildObjectivePayload(form),
    constraints,
  };
  const { holdings } = parseHoldings(form.holdings);
  if (Object.keys(holdings).length) payload.initial_holdings = holdings;
  if (form.costBps > 0) payload.transaction_cost_bps = form.costBps;
  return payload;
}

export default function OptimizeTab({ config, onUseConsumed }) {
  const [form, setForm] = useState(DEFAULT_FORM);
  const [status, setStatus] = useState({ busy: false, result: null, error: null });
  const update = (key) => (value) => setForm((prev) => ({ ...prev, [key]: value }));
  const errors = useMemo(() => validateOptimizeForm(form, config), [form, config]);
  const canRun = !status.busy && isValid(errors);

  const submit = async (event) => {
    event.preventDefault();
    if (!canRun) return;
    setStatus({ busy: true, result: null, error: null });
    const payload = buildOptimizePayload(form);
    try {
      const result = await optimize(payload, config.csrfToken);
      onUseConsumed?.();
      setStatus({ busy: false, result, error: null, rebalancing: Boolean(payload.initial_holdings) });
    } catch (error) {
      setStatus({ busy: false, result: null, error: error.message });
    }
  };

  const metrics = status.result?.metrics;
  const optimizedFor = describeObjective(status.result?.objective);
  const rebalance = status.result?.rebalance;

  return (
    <div className="grid gap-6 lg:grid-cols-[340px_1fr]">
      <Card title="Portfolio settings" className="h-fit">
        <form className="space-y-4" onSubmit={submit}>
          <Field label="Amount to invest ($)" error={errors.initialCash} hint="New cash; 0 is fine if you list holdings.">
            <NumberInput value={form.initialCash} min={0} step={500} onChange={update("initialCash")} />
          </Field>
          <Field
            label="Current holdings (optional)"
            error={errors.holdings}
            hint="Ticker and shares, one per line, e.g. AAPL 10. The optimizer rebalances from these."
          >
            <TextArea
              value={form.holdings}
              placeholder={"AAPL 10\nMSFT 2.5"}
              onChange={(event) => update("holdings")(event.target.value)}
              spellCheck={false}
            />
          </Field>
          <Field label="Universe" error={errors.universe} hint='Tickers and/or topN, e.g. "top50" or "AAPL MSFT NVDA".'>
            <TextInput
              type="text"
              value={form.universe}
              onChange={(event) => update("universe")(event.target.value)}
              spellCheck={false}
            />
          </Field>
          <ObjectiveFields form={form} errors={errors} update={update} />
          <div className="grid grid-cols-3 gap-3">
            <Field label="Max positions" error={errors.maxPositions}>
              <NumberInput value={form.maxPositions} min={0} onChange={update("maxPositions")} />
            </Field>
            <Field label="Max weight" error={errors.maxWeight}>
              <NumberInput value={form.maxWeight} min={0} max={1} step={0.05} onChange={update("maxWeight")} />
            </Field>
            <Field label="Min weight" error={errors.minWeight}>
              <NumberInput value={form.minWeight} min={0} max={1} step={0.01} onChange={update("minWeight")} />
            </Field>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Max turnover (% sold)" error={errors.maxTurnover} hint="Most of your holdings to sell. Empty: no limit.">
              <NumberInput value={form.maxTurnover} min={0} max={100} step={5} onChange={update("maxTurnover")} />
            </Field>
            <Field label="Trading cost (bps)" error={errors.costBps} hint="Per trade, e.g. 5 = 0.05%.">
              <NumberInput value={form.costBps} min={0} step={1} onChange={update("costBps")} />
            </Field>
          </div>
          <div className="flex items-center gap-3">
            <Button type="submit" disabled={!canRun}>
              {status.busy ? "Optimizing…" : "Optimize"}
            </Button>
            <span className="text-xs text-white/50">Costs 1 use</span>
          </div>
        </form>
      </Card>

      <div className="space-y-6">
        {status.error && <Alert>{status.error}</Alert>}
        {status.result && (
          <>
            {(status.result.notes ?? []).map((note) => (
              <Alert key={note} kind="info">
                {note}
              </Alert>
            ))}
            {metrics && (
              <div className="grid gap-4 sm:grid-cols-3">
                <Stat label="Expected annual return" value={formatPercent(metrics.expected_annual_return)} />
                <Stat label="Annual volatility" value={formatPercent(metrics.annual_volatility, { signed: false })} />
                <Stat label="Sharpe ratio" value={formatNumber(metrics.sharpe_ratio)} />
              </div>
            )}
            <Card title={`Allocation of ${formatMoney(metrics?.portfolio_value ?? form.initialCash, { digits: 0 })}`}>
              <AllocationTable portfolio={status.result.optimized_portfolio} cash={status.result.cash} />
              {optimizedFor && <p className="mt-3 text-xs text-white/60">Optimized for: {optimizedFor}.</p>}
              <p className="mt-3 text-xs text-white/40">
                Expected figures come from the optimizer&apos;s model of the past and are not forecasts. Use the Backtest
                tab to see how the strategy actually behaved.
              </p>
            </Card>
            {status.rebalancing && (
              <Card title="Trades">
                <TradesTable trades={status.result.trades} />
                {rebalance && (
                  <p className="mt-3 text-xs text-white/60">
                    Sells {formatPercent(rebalance.turnover, { signed: false })} of the portfolio (
                    {formatMoney(rebalance.sold)} sold, {formatMoney(rebalance.bought)} bought)
                    {rebalance.transaction_cost > 0 && `; estimated trading cost ${formatMoney(rebalance.transaction_cost)}`}.
                  </p>
                )}
              </Card>
            )}
          </>
        )}
        {!status.result && !status.error && (
          <Card>
            <p className="text-sm text-white/60">
              Build an optimized portfolio from the latest prices. Set an amount, choose a universe and what to optimize
              for, and optionally cap the number of positions or the weight of any single stock. List what you already
              hold to get the trades that rebalance it, limited by how much you are willing to sell.
            </p>
          </Card>
        )}
      </div>
    </div>
  );
}
OptimizeTab.propTypes = { config: PropTypes.object.isRequired, onUseConsumed: PropTypes.func };

function Stat({ label, value }) {
  return (
    <div className="rounded-lg border border-white/10 bg-gray p-4">
      <p className="text-xs uppercase tracking-wide text-white/60">{label}</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums text-white">{value}</p>
    </div>
  );
}
Stat.propTypes = { label: PropTypes.node, value: PropTypes.node };
