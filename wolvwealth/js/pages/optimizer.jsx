import PropTypes from "prop-types";
import { useCallback, useState } from "react";
import { Tabs } from "../components/ui";
import BacktestTab from "../features/BacktestTab";
import OptimizeTab from "../features/OptimizeTab";

const TABS = [
  { id: "optimize", label: "Optimize" },
  { id: "backtest", label: "Backtest" },
];

export default function OptimizerPage({ config }) {
  const [tab, setTab] = useState("optimize");
  const [usesRemaining, setUsesRemaining] = useState(config.usesRemaining);
  const consumeUse = useCallback(() => setUsesRemaining((uses) => Math.max(0, uses - 1)), []);

  return (
    <main className="mx-auto w-full max-w-screen-xl px-4">
      <div className="mb-4 flex flex-wrap items-end justify-between gap-2">
        <h1 className="text-2xl font-semibold text-white ubuntu">Portfolio optimizer</h1>
        <p className="text-sm text-white/60">
          <span className="tabular-nums text-white">{usesRemaining.toLocaleString("en-US")}</span> uses remaining ·
          prices through <span className="text-white">{config.lastPriceDate}</span>
        </p>
      </div>
      <Tabs tabs={TABS} active={tab} onChange={setTab} />
      {/* Both tabs stay mounted so a running backtest keeps polling while the user looks at the other tab. */}
      <div hidden={tab !== "optimize"}>
        <OptimizeTab config={config} onUseConsumed={consumeUse} />
      </div>
      <div hidden={tab !== "backtest"}>
        <BacktestTab config={config} onUseConsumed={consumeUse} />
      </div>
    </main>
  );
}
OptimizerPage.propTypes = { config: PropTypes.object.isRequired };
