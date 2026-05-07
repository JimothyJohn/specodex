/**
 * Product detail modal that shows full product information
 * Appears at the click location and expands
 */

import { useEffect, useRef } from 'react';
import { Product } from '../types/models';
import { formatPropertyLabel } from '../utils/formatting';
import { sanitizeUrl } from '../utils/sanitize';
import { useApp } from '../context/AppContext';
import {
  convertValueUnit,
  convertMinMaxUnit,
  displayUnit,
} from '../utils/unitConversion';
import CompatChecker from './CompatChecker';
import AddToProjectMenu from './AddToProjectMenu';
import ExternalLink from './ui/ExternalLink';
import { BUILD_SLOTS, BuildSlot } from '../utils/compat';

interface ProductDetailModalProps {
  product: Product | null;
  onClose: () => void;
  clickPosition: { x: number; y: number } | null;
}

export default function ProductDetailModal({ product, onClose, clickPosition }: ProductDetailModalProps) {
  const modalRef = useRef<HTMLDivElement>(null);
  const { unitSystem, build, addToBuild, removeFromBuild } = useApp();

  useEffect(() => {
    if (!product) return;

    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };

    const handleClickOutside = (e: MouseEvent) => {
      const target = e.target as Element | null;
      // Popovers (e.g. AddToProjectMenu) are portaled to document.body
      // so they sit outside modalRef in the DOM tree. Without this the
      // parent modal closes on every click inside the popover, killing
      // the popover before it can act on the click.
      if (target && target.closest?.('[data-portaled-popover]')) return;
      if (modalRef.current && !modalRef.current.contains(e.target as Node)) {
        onClose();
      }
    };

    document.addEventListener('keydown', handleEscape);
    document.addEventListener('mousedown', handleClickOutside);

    return () => {
      document.removeEventListener('keydown', handleEscape);
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, [product, onClose]);

  if (!product || !clickPosition) return null;

  const isNestedObject = (value: any): boolean => {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
    // Check if it's a simple value/unit or min/max/unit object
    if (('value' in value && 'unit' in value) || ('min' in value && 'max' in value && 'unit' in value)) {
      return false;
    }
    // It's a nested object like dimensions
    return true;
  };

  const formatValue = (value: any): { display: string; unit?: string } => {
    if (!value) return { display: '' };

    if (typeof value === 'object' && 'value' in value && 'unit' in value) {
      const c = convertValueUnit(value, unitSystem);
      return { display: String(c.value), unit: c.unit };
    }
    if (typeof value === 'object' && 'min' in value && 'max' in value && 'unit' in value) {
      const c = convertMinMaxUnit(value, unitSystem);
      return { display: `${c.min} - ${c.max}`, unit: c.unit };
    }
    if (Array.isArray(value)) {
      // Check if array contains objects with value/unit
      if (value.length > 0 && typeof value[0] === 'object' && value[0] !== null && 'value' in value[0] && 'unit' in value[0]) {
        const converted = value.map(item => convertValueUnit(item, unitSystem));
        const formattedValues = converted.map(item => String(item.value)).join(', ');
        const commonUnit = converted[0].unit;
        return { display: formattedValues, unit: commonUnit };
      }
      return { display: value.join(', ') };
    }
    return { display: String(value) };
  };

  const renderNestedObject = (value: any) => {
    const entries = Object.entries(value);

    // Check if there's a separate "unit" property at the same level
    const separateUnit = entries.find(([key, _]) => key.toLowerCase() === 'unit');
    const commonUnit = separateUnit ? (separateUnit[1] as string) : null;

    // If there's a separate unit property, filter it out from the entries
    const filteredEntries = commonUnit
      ? entries.filter(([key, _]) => key.toLowerCase() !== 'unit')
      : entries;

    // If no separate unit, check if all nested values have the same unit
    let finalUnit: string | null = commonUnit;
    if (!finalUnit) {
      const allUnits = filteredEntries
        .map(([_, v]: [string, any]) => {
          if (typeof v === 'object' && v !== null && 'unit' in v) return v.unit as string;
          return null;
        })
        .filter(Boolean) as string[];

      finalUnit = allUnits.length === filteredEntries.length && allUnits.every(u => u === allUnits[0])
        ? allUnits[0]
        : null;
    }

    return (
      <table className="spec-subtable">
        <tbody>
          {filteredEntries.map(([subKey, subValue]: [string, any]) => {
            // For shared-unit nested rows (e.g. dimensions: {width, height,
            // unit:"mm"}) the subValue is a bare number; convert it through
            // the parent unit so imperial display flips numerator and unit
            // together.
            let displayValue = subValue;
            let unitForCell: string | undefined;
            if (commonUnit && typeof subValue === 'number') {
              const c = convertValueUnit({ value: subValue, unit: commonUnit }, unitSystem);
              displayValue = c.value;
              unitForCell = c.unit;
            }
            const formatted = formatValue(displayValue);
            const subLabel = formatPropertyLabel(subKey);
            const cellUnit = unitForCell
              ?? (finalUnit ? displayUnit(finalUnit, unitSystem) : (formatted.unit || ''));

            return (
              <tr key={subKey} className="spec-subrow">
                <td className="spec-sublabel">{subLabel}</td>
                <td className="spec-subvalue">{formatted.display}</td>
                <td className="spec-subunit">{cellUnit}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    );
  };

  const groupSpecs = () => {
    const skipKeys = ['product_id', 'product_type', 'PK', 'SK', 'pk', 'sk', 'manufacturer', 'part_number', 'type', 'series', 'datasheet_url', 'pages', 'product_name'];

    // Define category groups for better organization
    const categories: Record<string, Array<{ key: string; label: string; value: any }>> = {
      'General': [],
      'Mechanical': [],
      'Performance': [],
      'Electrical': [],
      'Environmental': [],
      'Physical': [],
      'I/O & Connectivity': [],
      'Safety & Ratings': [],
      'Other': []
    };

    // Categorization rules
    const categorize = (key: string): string => {
      const electrical = ['voltage', 'current', 'power', 'resistance', 'inductance', 'phases'];
      const mechanical = ['torque', 'speed', 'inertia', 'poles'];
      const performance = ['rated', 'peak', 'constant', 'efficiency'];
      const physical = ['weight', 'dimensions', 'mounting', 'shaft', 'ip_rating'];
      const io = ['inputs', 'outputs', 'ethernet', 'fieldbus', 'encoder', 'feedback', 'control_modes'];
      const safety = ['safety', 'approvals', 'rating'];
      const environmental = ['temp', 'humidity', 'ambient', 'operating'];

      const lowerKey = key.toLowerCase();

      if (electrical.some(e => lowerKey.includes(e))) return 'Electrical';
      if (mechanical.some(m => lowerKey.includes(m))) return 'Mechanical';
      if (performance.some(p => lowerKey.includes(p))) return 'Performance';
      if (physical.some(p => lowerKey.includes(p))) return 'Physical';
      if (io.some(i => lowerKey.includes(i))) return 'I/O & Connectivity';
      if (safety.some(s => lowerKey.includes(s))) return 'Safety & Ratings';
      if (environmental.some(e => lowerKey.includes(e))) return 'Environmental';

      return 'Other';
    };

    Object.entries(product).forEach(([key, value]) => {
      if (skipKeys.includes(key)) return;

      const label = formatPropertyLabel(key);

      const category = categorize(key);
      categories[category].push({ key, label, value });
    });

    // Remove empty categories
    return Object.entries(categories).filter(([_, items]) => items.length > 0);
  };

  const groupedSpecs = groupSpecs();

  // Resolve datasheet URL — only linkable if it's an HTTP(S) URL
  const rawDatasheetUrl = typeof product.datasheet_url === 'string'
    ? product.datasheet_url
    : (product.datasheet_url as any)?.url ?? null;
  const datasheetUrl = rawDatasheetUrl?.startsWith('http') ? rawDatasheetUrl : null;
  return (
    <div className="product-detail-overlay">
      <div
        ref={modalRef}
        className="product-detail-modal"
        style={{
          transformOrigin: `${clickPosition.x}px ${clickPosition.y}px`,
        }}
      >
        <div className="product-detail-header">
          <div>
            <h2>{product.manufacturer || 'Unknown Manufacturer'}</h2>
            {datasheetUrl ? (
              <ExternalLink
                href={sanitizeUrl(datasheetUrl)}
                tooltip="View datasheet PDF"
                className="product-detail-part product-detail-part-link"
                onClick={(e) => e.stopPropagation()}
              >
                {product.part_number || 'N/A'}
              </ExternalLink>
            ) : (
              <p className="product-detail-part">{product.part_number || 'N/A'}</p>
            )}
            {'type' in product && product.type && <p className="product-detail-type">Type: {product.type}</p>}
            {'series' in product && product.series && <p className="product-detail-type">Series: {product.series}</p>}
          </div>
          <button className="product-detail-close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>

        <div className="product-detail-content">
          {groupedSpecs.map(([category, items]) => (
            <div key={category} className="spec-category">
              <h3 className="spec-category-title">{category}</h3>
              <table className="spec-table">
                <tbody>
                  {items.map(({ key, label, value }) => {
                    // Check if this is a nested object (like dimensions)
                    if (isNestedObject(value)) {
                      return (
                        <tr key={key} className="spec-row spec-row-nested">
                          <td className="spec-label">{label}</td>
                          <td className="spec-value-nested" colSpan={2}>
                            {renderNestedObject(value)}
                          </td>
                        </tr>
                      );
                    }

                    // Regular value rendering
                    const formatted = formatValue(value);
                    return (
                      <tr key={key} className="spec-row">
                        <td className="spec-label">{label}</td>
                        <td className="spec-value">{formatted.display}</td>
                        {formatted.unit && <td className="spec-unit">{formatted.unit}</td>}
                        {!formatted.unit && <td className="spec-unit"></td>}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ))}
          <CompatChecker product={product} />
          <AddToProjectMenu
            productRef={{
              product_type: product.product_type,
              product_id: product.product_id,
            }}
          />
          {(BUILD_SLOTS as readonly string[]).includes(product.product_type) && (() => {
            const slot = product.product_type as BuildSlot;
            const inSlot = build[slot];
            const isThisProduct = inSlot?.product_id === product.product_id;
            return (
              <div className="build-add-section">
                {isThisProduct ? (
                  <button
                    type="button"
                    className="build-add-btn build-add-btn-remove"
                    onClick={() => removeFromBuild(slot)}
                  >
                    Remove from build
                  </button>
                ) : (
                  <button
                    type="button"
                    className="build-add-btn"
                    onClick={() => addToBuild(product)}
                  >
                    {inSlot ? `Replace ${slot} in build` : `Add to build`}
                  </button>
                )}
              </div>
            );
          })()}
        </div>
      </div>
    </div>
  );
}
