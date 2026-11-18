import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { AllocationTable } from "../components/tables";
import { Alert, Button, Card, Field, NumberInput, TextInput } from "../components/ui";
import { optimize } from "../lib/api";
import { formatMoney, formatNumber, formatPercent } from "../lib/format";
import { parseUniverse } from "../lib/series";
import { isValid, validateOptimizeForm } from "../lib/validate";

const DEFAULT_FORM = { initialCash: 10000, universe: "top50", maxPositions: 10, maxWeight: 1, minWeight: 0 };

/** Translate form state into the /api/optimize/ payload. */
export function buildOptimizePayload(form) {
  const constraints = {};
  if (form.maxPositions !== "" && form.maxPositions > 0) constraints.max_positions = form.maxPositions;
  if (form.maxWeight !== "" && form.maxWeight < 1) constraints.max_weight = form.maxWeight;
  if (form.minWeight !== "" && form.minWeight > 0) constraints.min_universal_weight = form.minWeight;
  return { initial_cash: form.initialCash, universe: parseUniverse(form.universe), constraints };
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
    try {
      const result = await optimize(buildOptimizePayload(form), config.csrfToken);
      onUseConsumed?.();
      setStatus({ busy: false, result, error: null });
    } catch (error) {
      setStatus({ busy: false, result: null, error: error.message });
    }
  };

  const metrics = status.result?.metrics;

  return (
    <div className="grid gap-6 lg:grid-cols-[340px_1fr]">
      <Card title="Portfolio settings" className="h-fit">
        <form className="space-y-4" onSubmit={submit}>
          <Field label="Amount to invest ($)" error={errors.initialCash}>
            <NumberInput value={form.initialCash} min={1} step={500} onChange={update("initialCash")} />
          </Field>
          <Field label="Universe" error={errors.universe} hint='Tickers and/or topN, e.g. "top50" or "AAPL MSFT NVDA".'>
            <TextInput
              type="text"
              value={form.universe}
              onChange={(event) => update("universe")(event.target.value)}
              spellCheck={false}
            />
          </Field>
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
              <p className="mt-3 text-xs text-white/40">
                Expected figures come from the optimizer&apos;s model of the past and are not forecasts. Use the Backtest
                tab to see how the strategy actually behaved.
              </p>
            </Card>
          </>
        )}
        {!status.result && !status.error && (
          <Card>
            <p className="text-sm text-white/60">
              Build a max-Sharpe portfolio from the latest prices. Set an amount, choose a universe, and optionally cap the
              number of positions or the weight of any single stock.
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
