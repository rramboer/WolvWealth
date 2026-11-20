// Tabular views: realized metrics, the rebalance log, a portfolio allocation, and the trades to get there.
import PropTypes from "prop-types";
import { formatMoney, formatNumber, formatPercent, seriesLabel } from "../lib/format";
import { seriesKeys } from "../lib/series";

const METRIC_ROWS = [
  ["Total return", "total_return", (v) => formatPercent(v)],
  ["CAGR", "cagr", (v) => formatPercent(v)],
  ["Annual volatility", "annual_volatility", (v) => formatPercent(v, { signed: false })],
  ["Sharpe ratio", "sharpe_ratio", (v) => formatNumber(v)],
  ["Max drawdown", "max_drawdown", (v) => formatPercent(v, { signed: false })],
  ["Calmar ratio", "calmar_ratio", (v) => formatNumber(v)],
  ["Rebalances", "rebalances", (v) => formatNumber(v, { digits: 0 })],
  ["Avg turnover", "average_turnover", (v) => formatPercent(v, { signed: false, digits: 0 })],
  ["Transaction costs", "total_transaction_costs", (v) => formatMoney(v)],
  ["End value", "end_value", (v) => formatMoney(v)],
];

const cell = "px-3 py-2 text-sm";
const headCell = `${cell} text-left text-xs font-medium uppercase tracking-wide text-white/60`;

export function MetricsTable({ report }) {
  const keys = seriesKeys(report);
  const metricsFor = (key) => (key === "optimizer" ? report.metrics : report.benchmarks[key].metrics);
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse">
        <thead>
          <tr className="border-b border-white/10">
            <th className={headCell}>Metric</th>
            {keys.map((key) => (
              <th key={key} className={`${headCell} text-right`}>
                {seriesLabel(key)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {METRIC_ROWS.map(([label, field, format]) => (
            <tr key={field} className="border-b border-white/5">
              <td className={`${cell} text-white/80`}>{label}</td>
              {keys.map((key) => (
                <td key={key} className={`${cell} text-right tabular-nums text-white`}>
                  {format(metricsFor(key)[field])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
MetricsTable.propTypes = { report: PropTypes.object.isRequired };

function sortedWeights(weights) {
  return Object.entries(weights).sort((a, b) => b[1] - a[1]);
}

export function RebalanceLog({ result }) {
  const events = result.rebalances;
  if (!events.length) return <p className="text-sm text-white/60">No rebalance was executed.</p>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse">
        <thead>
          <tr className="border-b border-white/10">
            <th className={headCell}>Traded</th>
            <th className={`${headCell} text-right`}>Value</th>
            <th className={`${headCell} text-right`}>Change</th>
            <th className={`${headCell} text-right`}>Turnover</th>
            <th className={`${headCell} text-right`}>Cost</th>
            <th className={headCell}>Holdings</th>
          </tr>
        </thead>
        <tbody>
          {events.map((event, index) => {
            const previous = index === 0 ? result.start_value : events[index - 1].portfolio_value;
            const change = previous ? event.portfolio_value / previous - 1 : null;
            const weights = sortedWeights(event.weights);
            return (
              <tr key={event.execution_date} className="border-b border-white/5 align-top">
                <td className={`${cell} whitespace-nowrap text-white`}>{event.execution_date}</td>
                <td className={`${cell} text-right tabular-nums text-white`}>{formatMoney(event.portfolio_value)}</td>
                <td
                  className={`${cell} text-right tabular-nums ${change > 0 ? "text-green" : change < 0 ? "text-red" : "text-white"}`}
                >
                  {index === 0 ? "—" : formatPercent(change)}
                </td>
                <td className={`${cell} text-right tabular-nums text-white/80`}>
                  {formatPercent(event.turnover, { signed: false, digits: 0 })}
                </td>
                <td className={`${cell} text-right tabular-nums text-white/80`}>{formatMoney(event.transaction_cost)}</td>
                <td className={`${cell} text-white/80`}>
                  {weights.length === 0 ? (
                    <div>
                      <span className="text-yellow">Cash 100%</span>
                      {event.note && <p className="mt-1 text-xs text-white/50">{event.note}</p>}
                    </div>
                  ) : (
                    <details>
                      <summary className="cursor-pointer">
                        {weights
                          .slice(0, 4)
                          .map(([ticker, weight]) => `${ticker} ${formatPercent(weight, { signed: false, digits: 0 })}`)
                          .join(", ")}
                        {weights.length > 4 && ` +${weights.length - 4} more`}
                      </summary>
                      <ul className="mt-2 grid grid-cols-2 gap-x-6 gap-y-1 text-xs sm:grid-cols-3">
                        {weights.map(([ticker, weight]) => (
                          <li key={ticker} className="flex justify-between tabular-nums">
                            <span>{ticker}</span>
                            <span>{formatPercent(weight, { signed: false })}</span>
                          </li>
                        ))}
                      </ul>
                    </details>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
RebalanceLog.propTypes = { result: PropTypes.object.isRequired };

/** What to buy and sell to get from the current holdings to the optimized portfolio: sells first, then buys. */
export function TradesTable({ trades }) {
  const rows = Object.entries(trades ?? {}).sort(
    (a, b) => (a[1].action === b[1].action ? b[1].value - a[1].value : a[1].action === "sell" ? -1 : 1),
  );
  if (!rows.length) return <p className="text-sm text-white/60">No trades: the current holdings are already the answer.</p>;
  return (
    <table className="w-full border-collapse">
      <thead>
        <tr className="border-b border-white/10">
          <th className={headCell}>Ticker</th>
          <th className={headCell}>Action</th>
          <th className={`${headCell} text-right`}>Shares</th>
          <th className={`${headCell} text-right`}>Value</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(([ticker, trade]) => (
          <tr key={ticker} className="border-b border-white/5">
            <td className={`${cell} font-medium text-white`}>{ticker}</td>
            <td className={`${cell} font-medium ${trade.action === "sell" ? "text-red" : "text-green"}`}>
              {trade.action === "sell" ? "Sell" : "Buy"}
            </td>
            <td className={`${cell} text-right tabular-nums text-white/80`}>{formatNumber(trade.shares, { digits: 4 })}</td>
            <td className={`${cell} text-right tabular-nums text-white`}>{formatMoney(trade.value)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
TradesTable.propTypes = { trades: PropTypes.object };

export function AllocationTable({ portfolio, cash }) {
  const rows = Object.entries(portfolio).sort((a, b) => b[1].percent_weight - a[1].percent_weight);
  const showCash = cash && cash.value > 0;
  const max = Math.max(rows.length ? rows[0][1].percent_weight : 0, showCash ? cash.percent_weight : 0) || 100;
  return (
    <table className="w-full border-collapse">
      <thead>
        <tr className="border-b border-white/10">
          <th className={headCell}>Ticker</th>
          <th className={`${headCell} w-1/2`}>Weight</th>
          <th className={`${headCell} text-right`}>Value</th>
          <th className={`${headCell} text-right`}>Shares</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(([ticker, position]) => (
          <tr key={ticker} className="border-b border-white/5">
            <td className={`${cell} font-medium text-white`}>{ticker}</td>
            <td className={cell}>
              <div className="flex items-center gap-3">
                <div className="h-2 flex-1 overflow-hidden rounded bg-white/10">
                  <div className="h-full bg-cyan" style={{ width: `${(position.percent_weight / max) * 100}%` }} />
                </div>
                <span className="w-16 text-right tabular-nums text-white">{position.percent_weight.toFixed(2)}%</span>
              </div>
            </td>
            <td className={`${cell} text-right tabular-nums text-white`}>{formatMoney(position.value)}</td>
            <td className={`${cell} text-right tabular-nums text-white/80`}>{formatNumber(position.shares, { digits: 4 })}</td>
          </tr>
        ))}
        {showCash && (
          <tr className="border-b border-white/5">
            <td className={`${cell} font-medium text-yellow`}>Cash</td>
            <td className={cell}>
              <div className="flex items-center gap-3">
                <div className="h-2 flex-1 overflow-hidden rounded bg-white/10">
                  <div className="h-full bg-yellow" style={{ width: `${(cash.percent_weight / max) * 100}%` }} />
                </div>
                <span className="w-16 text-right tabular-nums text-white">{cash.percent_weight.toFixed(2)}%</span>
              </div>
            </td>
            <td className={`${cell} text-right tabular-nums text-white`}>{formatMoney(cash.value)}</td>
            <td className={`${cell} text-right tabular-nums text-white/40`}>—</td>
          </tr>
        )}
      </tbody>
    </table>
  );
}
AllocationTable.propTypes = {
  portfolio: PropTypes.object.isRequired,
  cash: PropTypes.shape({ value: PropTypes.number, percent_weight: PropTypes.number }),
};
