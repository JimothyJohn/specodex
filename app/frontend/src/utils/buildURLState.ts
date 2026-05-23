/**
 * Build's URL state codec — the bookmarkable/shareable serialisation of
 * a `BuildRequirements` (todo/BUILD.md Part 2 "Schema location &
 * serialisation").
 *
 * Build's full requirement state encodes into the URL so configurations
 * are shareable, and so the future Wizard handoff is just a redirect to
 * `/build?...` with the LLM's derived params pre-populated — no separate
 * handoff API.
 *
 * Param keys (compact-but-legible, 2-3 chars):
 *   ml = motion_class       or = orientation      pm = payload_mass
 *   st = stroke             mt = move_time        dw = dwell_time
 *   up = units_preference
 *
 * `ValueUnit` fields encode as `<number><unit>` with no separator
 * (`5kg`, `200mm`, `0.2s`). Null fields are omitted entirely — "blank =
 * no constraint applied" carries through to the URL.
 *
 * Parsing is total and never throws: the URL is user-controlled, so any
 * absent, malformed, or out-of-vocabulary value falls back to the
 * empty-form null (or, for `units_preference`, the "metric" default).
 */

import type { ValueUnit } from '../types/generated';
import {
  emptyBuildRequirements,
  type BuildRequirements,
  type LinearOrientation,
  type MotionClass,
  type UnitsPreference,
} from '../types/buildRequirements';

const PARAM = {
  motion_class: 'ml',
  orientation: 'or',
  payload_mass: 'pm',
  stroke: 'st',
  move_time: 'mt',
  dwell_time: 'dw',
  units_preference: 'up',
} as const;

const MOTION_CLASSES: readonly MotionClass[] = ['linear', 'rotary'];
const ORIENTATIONS: readonly LinearOrientation[] = ['horizontal', 'vertical'];
const UNITS: readonly UnitsPreference[] = ['metric', 'imperial'];

/** `{ value: 0.2, unit: 's' }` -> `"0.2s"`. */
function encodeValueUnit(vu: ValueUnit): string {
  return `${vu.value}${vu.unit ?? ''}`;
}

/**
 * `"0.2s"` -> `{ value: 0.2, unit: 's' }`. Returns null when no finite
 * leading number can be read, so a junk param drops the field rather
 * than poisoning the form.
 */
function decodeValueUnit(raw: string): ValueUnit | null {
  const match = raw.trim().match(/^(-?(?:\d+\.?\d*|\.\d+))(.*)$/);
  if (!match) {
    return null;
  }
  const value = Number.parseFloat(match[1]);
  if (!Number.isFinite(value)) {
    return null;
  }
  return { value, unit: match[2].trim() };
}

function decodeEnum<T extends string>(
  raw: string | null,
  allowed: readonly T[],
): T | null {
  return raw !== null && (allowed as readonly string[]).includes(raw)
    ? (raw as T)
    : null;
}

/**
 * Serialise a BuildRequirements into URL params. Null fields are
 * omitted; `units_preference` always emits since it has a default and
 * is display state worth preserving across a shared link.
 */
export function serialiseBuildRequirementsToURL(
  requirements: BuildRequirements,
): URLSearchParams {
  const params = new URLSearchParams();
  const { motion_class, orientation, payload_mass, units_preference } =
    requirements;
  const profile = requirements.motion_profiles[0];

  if (motion_class !== null) {
    params.set(PARAM.motion_class, motion_class);
  }
  if (orientation !== null) {
    params.set(PARAM.orientation, orientation);
  }
  if (payload_mass !== null) {
    params.set(PARAM.payload_mass, encodeValueUnit(payload_mass));
  }
  if (profile?.stroke != null) {
    params.set(PARAM.stroke, encodeValueUnit(profile.stroke));
  }
  if (profile?.move_time != null) {
    params.set(PARAM.move_time, encodeValueUnit(profile.move_time));
  }
  if (profile?.dwell_time != null) {
    params.set(PARAM.dwell_time, encodeValueUnit(profile.dwell_time));
  }
  params.set(PARAM.units_preference, units_preference);

  return params;
}

/**
 * Parse URL params back into a BuildRequirements. Total and lossy by
 * design: a param that is absent or fails to decode yields the
 * empty-form null. URL state is the authoritative hydration source on
 * Build mount (it wins over localStorage), so it must always produce a
 * usable form state.
 */
export function parseURLToBuildRequirements(
  params: URLSearchParams,
): BuildRequirements {
  const base = emptyBuildRequirements();

  const payloadRaw = params.get(PARAM.payload_mass);
  const strokeRaw = params.get(PARAM.stroke);
  const moveTimeRaw = params.get(PARAM.move_time);
  const dwellTimeRaw = params.get(PARAM.dwell_time);

  return {
    motion_class: decodeEnum<MotionClass>(
      params.get(PARAM.motion_class),
      MOTION_CLASSES,
    ),
    orientation: decodeEnum<LinearOrientation>(
      params.get(PARAM.orientation),
      ORIENTATIONS,
    ),
    payload_mass:
      payloadRaw !== null ? decodeValueUnit(payloadRaw) : base.payload_mass,
    motion_profiles: [
      {
        stroke: strokeRaw !== null ? decodeValueUnit(strokeRaw) : null,
        move_time:
          moveTimeRaw !== null ? decodeValueUnit(moveTimeRaw) : null,
        dwell_time:
          dwellTimeRaw !== null ? decodeValueUnit(dwellTimeRaw) : null,
      },
    ],
    units_preference:
      decodeEnum<UnitsPreference>(params.get(PARAM.units_preference), UNITS) ??
      base.units_preference,
  };
}
