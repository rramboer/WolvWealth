import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import { DrawdownChart, EquityChart } from "../components/charts";
import { MetricsTable, RebalanceLog } from "../components/tables";
import { Alert, Button, Card, Field, NumberInput, ProgressBar, Select, TextInput } from "../components/ui";
import { useBacktestJob } from "../hooks/useBacktestJob";
import { formatPercent, formatSeconds, seriesLabel } from "../lib/format";
import { estimateRebalances, estimateSeconds, parseUniverse } from "../lib/series";
import { isValid, validateBacktestForm } from "../lib/validate";

const FREQUENCIES = ["daily", "weekly", "biweekly", "monthly", "quarterly", "semiannually", "yearly"].map((f) => ({
  value: f,
  label: f[0].toUpperCase() + f.slice(1),
}));

function shiftYears(isoDate, years) {
  const date = new Date(isoDate);
  date.setUTCFullYear(date.getUTCFullYear() + years);
  return date.toISOString().slice(0, 10);
}

function defaultForm(config) {
  const start = shiftYears(config.lastPriceDate, -3);
  return {
    start: start < config.firstPriceDate ? config.firstPriceDate : start,
    end: config.lastPriceDate,
    initialCash: 100000,
    frequency: "monthly",
    universe: "top50",
    lookbackDays: 1095,
    executionLag: 1,
    costBps: 5,
    maxPositions: 10,
    maxWeight: 1,
    minWeight: 0,
  };
}

/** Translate form state into the /api/backtest/ payload. */
export function buildBacktestPayload(form) {
  const constraints = {};
  if (form.maxPositions !== "" && form.maxPositions > 0) constraints.max_positions = form.maxPositions;
  if (form.maxWeight !== "" && form.maxWeight < 1) constraints.max_weight = form.maxWeight;
  if (form.minWeight !== "" && form.minWeight > 0) constraints.min_universal_weight = form.minWeight;
  return {
    start: form.start,
    end: form.end,
    initial_cash: form.initialCash,
    frequency: form.frequency,
    universe: parseUniverse(form.universe),
    lookback_days: form.lookbackDays,
    execution_lag: form.executionLag,
    transaction_cost_bps: form.costBps,
    constraints,
  };
}

function universeSizeGuess(entries, universeSize) {
  let count = 0;
  for (const entry of entries) {
    const top = /^TOP(\d+)$/.exec(entry);
    count += top ? Math.min(Number(top[1]), universeSize) : 1;
  }
  return count;
}

export default function BacktestTab({ config, onUseConsumed }) {
  const [form, setForm] = useState(() => defaultForm(config));
  const { state, run, reset, busy } = useBacktestJob({ csrfToken: config.csrfToken, onStarted: onUseConsumed });
  const update = (key) => (value) => setForm((prev) => ({ ...prev, [key]: value }));
  const onText = (key) => (event) => update(key)(event.target.value);

  const universeEntries = useMemo(() => parseUniverse(form.universe), [form.universe]);
  const errors = useMemo(() => validateBacktestForm(form, config), [form, config]);
  const rebalances = estimateRebalances(form.start, form.end, form.frequency);
  const tooMany = rebalances > config.maxRebalances;
  const universeGuess = universeSizeGuess(universeEntries, config.universeSize);
  const seconds = estimateSeconds(rebalances, universeGuess);
  const canRun = !busy && !tooMany && isValid(errors);

  const report = state.status === "succeeded" ? state.job.report : null;

  return (
    <div className="grid gap-6 lg:grid-cols-[340px_1fr]">
      <Card title="Backtest settings" className="h-fit">
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault();
            if (canRun) run(buildBacktestPayload(form));
          }}
        >
          <div className="grid grid-cols-2 gap-3">
            <Field label="Start" error={errors.start}>
              <TextInput type="date" value={form.start} min={config.firstPriceDate} max={config.lastPriceDate} onChange={onText("start")} />
            </Field>
            <Field label="End" error={errors.end}>
              <TextInput type="date" value={form.end} min={config.firstPriceDate} max={config.lastPriceDate} onChange={onText("end")} />
            </Field>
          </div>
          <Field label="Initial cash ($)" error={errors.initialCash}>
            <NumberInput value={form.initialCash} min={1} step={1000} onChange={update("initialCash")} />
          </Field>
          <Field label="Rebalance frequency" hint={`≈ ${rebalances} rebalances (limit ${config.maxRebalances}), about ${formatSeconds(seconds)}`}>
            <Select value={form.frequency} options={FREQUENCIES} onChange={onText("frequency")} />
          </Field>
          <Field
            label="Universe"
            error={errors.universe}
            hint={`Tickers and/or topN, e.g. "top50" or "AAPL MSFT NVDA". Max ${config.maxUniverseSize} tickers.`}
          >
            <TextInput type="text" value={form.universe} onChange={onText("universe")} spellCheck={false} />
          </Field>
          <div className="grid grid-cols-3 gap-3">
            <Field label="Lookback (days)" error={errors.lookbackDays}>
              <NumberInput value={form.lookbackDays} min={30} step={30} onChange={update("lookbackDays")} />
            </Field>
            <Field label="Lag (days)" error={errors.executionLag}>
              <NumberInput value={form.executionLag} min={0} max={5} onChange={update("executionLag")} />
            </Field>
            <Field label="Cost (bps)" error={errors.costBps}>
              <NumberInput value={form.costBps} min={0} step={1} onChange={update("costBps")} />
            </Field>
          </div>
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
          {tooMany && (
            <Alert kind="warning">
              About {rebalances} rebalances exceeds the limit of {config.maxRebalances}. Shorten the range or pick a lower
              frequency.
            </Alert>
          )}
          <div className="flex items-center gap-3">
            <Button type="submit" disabled={!canRun}>
              {busy ? "Running…" : "Run backtest"}
            </Button>
            {(report || state.status === "failed") && (
              <Button variant="secondary" onClick={reset}>
                Clear
              </Button>
            )}
            <span className="text-xs text-white/50">Costs 1 use</span>
          </div>
        </form>
      </Card>

      <div className="space-y-6">
        {busy && (
          <Card>
            <ProgressBar
              fraction={state.job?.progress?.fraction ?? 0}
              label={state.job?.progress?.message ?? (state.status === "starting" ? "Starting…" : "Queued…")}
              detail={
                state.job?.estimated_seconds_remaining != null
                  ? `About ${formatSeconds(state.job.estimated_seconds_remaining)} remaining`
                  : "Estimating time remaining…"
              }
            />
            {state.reconnecting && (
              <p role="status" className="mt-3 text-xs text-yellow">
                Connection problem ({state.reconnecting.reason}). The backtest is still running on the server; retrying in{" "}
                {formatSeconds(state.reconnecting.delayMs / 1000)} (attempt {state.reconnecting.attempt} of{" "}
                {state.reconnecting.maxRetries}).
              </p>
            )}
          </Card>
        )}
        {state.status === "failed" && <Alert>{state.error}</Alert>}
        {report && <BacktestResults report={report} cached={state.job.cached} />}
        {!busy && !report && state.status !== "failed" && (
          <Card>
            <p className="text-sm text-white/60">
              Replay the optimizer over history and compare it with an equal-weight portfolio of the same universe and
              buy-and-hold SPY. Progress is shown while it runs.
            </p>
          </Card>
        )}
      </div>
    </div>
  );
}
BacktestTab.propTypes = { config: PropTypes.object.isRequired, onUseConsumed: PropTypes.func };

function BacktestResults({ report, cached }) {
  const optimizer = report.metrics;
  const equal = report.benchmarks.equal_weight?.metrics;
  const edge = equal ? optimizer.total_return - equal.total_return : null;
  const warnings = [...report.warnings, ...report.result.warnings];
  return (
    <>
      <div className="grid gap-4 sm:grid-cols-3">
        <Stat label="Optimizer total return" value={formatPercent(optimizer.total_return)} tone={optimizer.total_return} />
        <Stat label="vs equal weight" value={edge === null ? "n/a" : formatPercent(edge)} tone={edge} />
        <Stat label="Max drawdown" value={formatPercent(optimizer.max_drawdown, { signed: false })} tone={-1} />
      </div>
      <Card title={`Growth of ${formatMoneyShort(report.config.initial_cash)}`}>
        <EquityChart report={report} />
      </Card>
      <Card title="Drawdown from peak">
        <DrawdownChart report={report} />
      </Card>
      <Card title="Realized performance">
        <MetricsTable report={report} />
        {cached && <p className="mt-3 text-xs text-white/40">Served from cache.</p>}
      </Card>
      <Card title={`Rebalances (${report.result.rebalances.length})`}>
        <RebalanceLog result={report.result} />
      </Card>
      {warnings.length > 0 && (
        <Card title="Notes">
          <ul className="list-disc space-y-2 pl-5 text-sm text-white/70">
            {warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
          <p className="mt-3 text-xs text-white/40">
            Benchmarks: {Object.keys(report.benchmarks).map(seriesLabel).join(", ") || "none"}.
          </p>
        </Card>
      )}
    </>
  );
}
BacktestResults.propTypes = { report: PropTypes.object.isRequired, cached: PropTypes.bool };

function formatMoneyShort(value) {
  return value >= 1000 ? `$${Math.round(value / 1000).toLocaleString("en-US")}k` : `$${value}`;
}

function Stat({ label, value, tone }) {
  const color = tone > 0 ? "text-green" : tone < 0 ? "text-red" : "text-white";
  return (
    <div className="rounded-lg border border-white/10 bg-gray p-4">
      <p className="text-xs uppercase tracking-wide text-white/60">{label}</p>
      <p className={`mt-1 text-2xl font-semibold tabular-nums ${color}`}>{value}</p>
    </div>
  );
}
Stat.propTypes = { label: PropTypes.node, value: PropTypes.node, tone: PropTypes.number };
