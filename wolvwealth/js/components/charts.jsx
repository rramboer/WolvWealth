// Equity and drawdown charts for a backtest report.
import PropTypes from "prop-types";
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { formatMoney, formatPercent, seriesLabel } from "../lib/format";
import { drawdownRows, equityRows, seriesKeys } from "../lib/series";

const PALETTE = ["#67e8f9", "#ffff6b", "#42d36e", "#ff715b", "#ffffff"];
const GRID = "rgba(255,255,255,0.08)";
const AXIS = "rgba(255,255,255,0.6)";

const colorFor = (index) => PALETTE[index % PALETTE.length];
const yearMonth = (date) => date.slice(0, 7);

function tooltipStyle() {
  return { backgroundColor: "#111111", border: "1px solid rgba(255,255,255,0.2)", borderRadius: 4 };
}

function SeriesChart({ rows, keys, valueFormatter, yFormatter, yDomain }) {
  return (
    <ResponsiveContainer width="100%" height={320}>
      <LineChart data={rows} margin={{ top: 8, right: 16, left: 8, bottom: 0 }}>
        <CartesianGrid stroke={GRID} vertical={false} />
        <XAxis dataKey="date" tickFormatter={yearMonth} stroke={AXIS} tick={{ fontSize: 12 }} minTickGap={48} />
        <YAxis tickFormatter={yFormatter} stroke={AXIS} tick={{ fontSize: 12 }} width={84} domain={yDomain} />
        <Tooltip
          contentStyle={tooltipStyle()}
          labelStyle={{ color: "#ffffff" }}
          formatter={(value, name) => [valueFormatter(value), seriesLabel(name)]}
        />
        <Legend formatter={seriesLabel} />
        {keys.map((key, index) => (
          <Line
            key={key}
            type="monotone"
            dataKey={key}
            stroke={colorFor(index)}
            strokeWidth={key === "optimizer" ? 2 : 1.25}
            dot={false}
            isAnimationActive={false}
            connectNulls
          />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}
SeriesChart.propTypes = {
  rows: PropTypes.array.isRequired,
  keys: PropTypes.arrayOf(PropTypes.string).isRequired,
  valueFormatter: PropTypes.func.isRequired,
  yFormatter: PropTypes.func.isRequired,
  yDomain: PropTypes.array,
};

export function EquityChart({ report }) {
  return (
    <SeriesChart
      rows={equityRows(report)}
      keys={seriesKeys(report)}
      valueFormatter={(value) => formatMoney(value)}
      yFormatter={(value) => formatMoney(value, { digits: 0 })}
      yDomain={["auto", "auto"]}
    />
  );
}
EquityChart.propTypes = { report: PropTypes.object.isRequired };

export function DrawdownChart({ report }) {
  return (
    <SeriesChart
      rows={drawdownRows(report)}
      keys={seriesKeys(report)}
      valueFormatter={(value) => formatPercent(value, { signed: false })}
      yFormatter={(value) => formatPercent(value, { signed: false, digits: 0 })}
      yDomain={["auto", 0]}
    />
  );
}
DrawdownChart.propTypes = { report: PropTypes.object.isRequired };
