/**
 * Supercategory layer over the flat `product_type` literal.
 *
 * Two-level taxonomy: supercategories (e.g. "Linear Motion") group
 * subcategories (`linear_actuator`, `electric_cylinder`). The supercategory
 * is **derived metadata** — `product_type` remains the only discriminator
 * the DB and LLM see. See `todo/CATAGORIES.md` for the full design.
 *
 * **Append-only.** Renaming or deleting an entry needs a code search for
 * every consumer; supercategory IDs leak into URL paths (e.g. `/actuators`
 * routes off `linear_motion`).
 */
import type { ProductTypeLiteral } from './generated';

export type Supercategory =
  | 'linear_motion'
  | 'rotary_motion'
  | 'drives_control'
  | 'switching'
  | 'robotics';

export interface SupercategorySpec {
  id: Supercategory;
  display_name: string;
  // One-sentence "what is this?" copy. Surfaces as the page subhead
  // and the tile description on dashboards.
  description: string;
  // The "selection question" a user actually has in their head.
  // Drives subhead copy when more discoverable than `description`.
  selection_question: string;
  subcategories: ProductTypeLiteral[];
}

export const SUPERCATEGORIES: Record<Supercategory, SupercategorySpec> = {
  linear_motion: {
    id: 'linear_motion',
    display_name: 'Linear Motion',
    description: 'Devices that translate a payload along a single axis.',
    selection_question: 'How do I move this load this far?',
    subcategories: ['linear_actuator', 'electric_cylinder'],
  },
  rotary_motion: {
    id: 'rotary_motion',
    display_name: 'Rotary Motion',
    description: 'Devices that produce torque or angular position.',
    selection_question: 'What torque, speed, and inertia do I need?',
    subcategories: ['motor', 'gearhead'],
  },
  drives_control: {
    id: 'drives_control',
    display_name: 'Drives & Control',
    description: 'Power electronics that command motors and actuators.',
    selection_question: 'How do I drive and supervise this motor?',
    subcategories: ['drive'],
  },
  switching: {
    id: 'switching',
    display_name: 'Switching',
    description: 'Electromechanical and solid-state switching devices.',
    selection_question: 'How do I make and break this circuit?',
    subcategories: ['contactor'],
  },
  robotics: {
    id: 'robotics',
    display_name: 'Robotics',
    description: 'Multi-axis articulated systems.',
    selection_question: 'What payload, reach, and repeatability do I need?',
    subcategories: ['robot_arm'],
  },
};

/** Reverse lookup: what supercategory does this subcategory belong to? */
export function supercategoryFor(
  subcategory: ProductTypeLiteral,
): Supercategory | null {
  for (const spec of Object.values(SUPERCATEGORIES)) {
    if ((spec.subcategories as readonly string[]).includes(subcategory)) {
      return spec.id;
    }
  }
  return null;
}

/** All supercategories in display order. */
export const SUPERCATEGORY_ORDER: Supercategory[] = [
  'linear_motion',
  'rotary_motion',
  'drives_control',
  'switching',
  'robotics',
];
