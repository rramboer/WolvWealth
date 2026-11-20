// "Optimize for" picker plus the selected objective's one parameter, shared by both tabs.
import PropTypes from "prop-types";
import { OBJECTIVES, findObjective } from "../lib/objectives";
import { Field, NumberInput, Select } from "./ui";

export function ObjectiveFields({ form, errors, update }) {
  const objective = findObjective(form.objective);
  const { param } = objective;
  return (
    <>
      <Field label="Optimize for" hint={objective.description}>
        <Select
          value={objective.value}
          options={OBJECTIVES}
          onChange={(event) => update("objective")(event.target.value)}
        />
      </Field>
      {param && (
        <Field label={param.label} error={errors[param.key]} hint={param.hint}>
          <NumberInput
            value={form[param.key]}
            min={param.min}
            max={param.max}
            step={param.step}
            onChange={update(param.key)}
          />
        </Field>
      )}
    </>
  );
}
ObjectiveFields.propTypes = {
  form: PropTypes.object.isRequired,
  errors: PropTypes.object.isRequired,
  update: PropTypes.func.isRequired,
};
