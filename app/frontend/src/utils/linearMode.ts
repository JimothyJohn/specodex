/**
 * Linear-mode display transforms.
 *
 * Catalog rows store rotary specs (RPM, Nm). When the user is sizing a
 * linear-motion build, the right readouts are linear speed (mm/s) and
 * thrust force (N). These two pure transforms map a single rotary
 * `ValueUnit` / `MinMaxUnit` to its linear-mode equivalent, given the
 * carriage's linear travel per motor revolution (mm/rev).
 *
 * Pulled out of `ProductList.tsx` so Build's Motor slot can call the
 * same math (todo/BUILD.md Phase 1 PR 1B — "Move the linear-mode
 * display transforms ... to a helper that Build's Motor slot will
 * consume in PR 1C"). Behaviour preserved bit-for-bit; the original
 * site now imports from here.
 */

/**
 * Convert an RPM `ValueUnit` / `MinMaxUnit` to linear speed (mm/s).
 *
 * Linear speed = (RPM / 60) * linearTravel(mm/rev).
 *
 * Anything that isn't a recognisable ValueUnit / MinMaxUnit shape — or
 * a zero `linearTravel` — is returned unchanged so the caller can
 * blanket-apply this across product rows.
 */
export function rpmToLinearSpeed(value: any, linearTravel: number): any {
  if (!value || !linearTravel) return value;
  if (typeof value === 'object' && 'value' in value && typeof value.value === 'number') {
    return { value: parseFloat(((value.value / 60) * linearTravel).toPrecision(4)), unit: 'mm/s' };
  }
  if (typeof value === 'object' && 'min' in value && 'max' in value) {
    return {
      min: value.min != null ? parseFloat(((value.min / 60) * linearTravel).toPrecision(4)) : value.min,
      max: value.max != null ? parseFloat(((value.max / 60) * linearTravel).toPrecision(4)) : value.max,
      unit: 'mm/s',
    };
  }
  return value;
}

/**
 * Convert a torque `ValueUnit` / `MinMaxUnit` (Nm) to thrust force (N).
 *
 * F = T * 2π / lead, with lead = linearTravel(mm) * 0.001 in metres.
 * Assumes 100% screw efficiency — simpler default; revisit if
 * real-world losses become material to the selection workflow.
 *
 * Anything that isn't a recognisable ValueUnit / MinMaxUnit shape — or
 * a zero `linearTravel` — is returned unchanged.
 */
export function torqueToThrust(value: any, linearTravel: number): any {
  if (!value || !linearTravel) return value;
  const factor = (2 * Math.PI) / (linearTravel * 0.001);
  if (typeof value === 'object' && 'value' in value && typeof value.value === 'number') {
    return { value: parseFloat((value.value * factor).toPrecision(4)), unit: 'N' };
  }
  if (typeof value === 'object' && 'min' in value && 'max' in value) {
    return {
      min: value.min != null ? parseFloat((value.min * factor).toPrecision(4)) : value.min,
      max: value.max != null ? parseFloat((value.max * factor).toPrecision(4)) : value.max,
      unit: 'N',
    };
  }
  return value;
}
