// Small presentational building blocks shared by both tabs.
import PropTypes from "prop-types";

export function Card({ title, children, className = "" }) {
  return (
    <section className={`rounded-lg border border-white/10 bg-gray p-5 ${className}`}>
      {title && <h2 className="mb-4 text-lg font-semibold text-white">{title}</h2>}
      {children}
    </section>
  );
}
Card.propTypes = { title: PropTypes.node, children: PropTypes.node, className: PropTypes.string };

export function Tabs({ tabs, active, onChange }) {
  return (
    <div role="tablist" className="mb-6 flex gap-2 border-b border-white/10">
      {tabs.map((tab) => {
        const selected = tab.id === active;
        return (
          <button
            key={tab.id}
            role="tab"
            type="button"
            aria-selected={selected}
            onClick={() => onChange(tab.id)}
            className={`-mb-px border-b-2 px-4 py-2 text-sm font-medium transition ${
              selected ? "border-cyan text-white" : "border-transparent text-white/60 hover:text-white"
            }`}
          >
            {tab.label}
          </button>
        );
      })}
    </div>
  );
}
Tabs.propTypes = {
  tabs: PropTypes.arrayOf(PropTypes.shape({ id: PropTypes.string, label: PropTypes.node })).isRequired,
  active: PropTypes.string.isRequired,
  onChange: PropTypes.func.isRequired,
};

const inputClass =
  "w-full rounded border border-white/20 bg-gray-dark px-3 py-2 text-sm text-white outline-none focus:border-cyan disabled:opacity-50";

export function Field({ label, hint, error, children }) {
  return (
    <div>
      <label className="block">
        <span className="mb-1 block text-xs font-medium uppercase tracking-wide text-white/70">{label}</span>
        {children}
      </label>
      {error ? (
        <p className="mt-1 text-xs text-red" role="note">
          {error}
        </p>
      ) : (
        hint && <p className="mt-1 text-xs text-white/50">{hint}</p>
      )}
    </div>
  );
}
Field.propTypes = {
  label: PropTypes.node.isRequired,
  hint: PropTypes.node,
  error: PropTypes.node,
  children: PropTypes.node,
};

export function TextInput(props) {
  return <input {...props} className={inputClass} />;
}

export function NumberInput({ value, onChange, ...props }) {
  return (
    <input
      type="number"
      value={value}
      onChange={(event) => onChange(event.target.value === "" ? "" : Number(event.target.value))}
      className={inputClass}
      {...props}
    />
  );
}
NumberInput.propTypes = { value: PropTypes.oneOfType([PropTypes.number, PropTypes.string]), onChange: PropTypes.func };

export function Select({ options, ...props }) {
  return (
    <select {...props} className={inputClass}>
      {options.map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  );
}
Select.propTypes = { options: PropTypes.arrayOf(PropTypes.shape({ value: PropTypes.string, label: PropTypes.node })) };

export function Button({ children, variant = "primary", ...props }) {
  const styles =
    variant === "primary"
      ? "bg-blue text-white hover:bg-blue/80"
      : "border border-white/20 text-white hover:bg-white/10";
  return (
    <button
      type="button"
      {...props}
      className={`rounded px-5 py-2 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-50 ${styles}`}
    >
      {children}
    </button>
  );
}
Button.propTypes = { children: PropTypes.node, variant: PropTypes.oneOf(["primary", "secondary"]) };

export function Alert({ kind = "error", children }) {
  const styles = {
    error: "border-red/40 bg-red/10 text-red",
    warning: "border-yellow/40 bg-yellow/10 text-yellow",
    info: "border-cyan/40 bg-cyan/10 text-cyan",
  }[kind];
  return (
    <div role={kind === "error" ? "alert" : "status"} className={`rounded border px-4 py-3 text-sm ${styles}`}>
      {children}
    </div>
  );
}
Alert.propTypes = { kind: PropTypes.oneOf(["error", "warning", "info"]), children: PropTypes.node };

export function ProgressBar({ fraction, label, detail }) {
  const percent = Math.round(Math.min(1, Math.max(0, fraction ?? 0)) * 100);
  return (
    <div>
      <div className="mb-1 flex justify-between text-sm text-white">
        <span>{label}</span>
        <span className="tabular-nums text-white/70">{percent}%</span>
      </div>
      <div
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent}
        className="h-2 w-full overflow-hidden rounded bg-white/10"
      >
        <div className="h-full bg-cyan transition-all duration-500" style={{ width: `${percent}%` }} />
      </div>
      {detail && <p className="mt-1 text-xs text-white/50">{detail}</p>}
    </div>
  );
}
ProgressBar.propTypes = { fraction: PropTypes.number, label: PropTypes.node, detail: PropTypes.node };
