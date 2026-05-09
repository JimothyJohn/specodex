import { useState, useEffect, useRef } from 'react';
import { DatasheetEntry } from '../types/models';
import { useApp } from '../context/AppContext';

interface DatasheetEditModalProps {
  datasheet: DatasheetEntry | null;
  onClose: () => void;
  clickPosition: { x: number; y: number } | null;
}

export default function DatasheetEditModal({ datasheet, onClose, clickPosition }: DatasheetEditModalProps) {
  const { updateProduct } = useApp();
  const modalRef = useRef<HTMLDivElement>(null);
  const [formData, setFormData] = useState<Partial<DatasheetEntry>>({});
  const [isSaving, setIsSaving] = useState(false);
  // Phase 4: noValidate forms surface validation errors via a themed
  // inline message instead of the OS-styled UA bubble.
  const [validationError, setValidationError] = useState<string | null>(null);

  useEffect(() => {
    if (datasheet) {
      setFormData({
        product_name: datasheet.product_name,
        manufacturer: datasheet.manufacturer,
        product_family: datasheet.product_family,
        url: datasheet.url,
      });
    }
  }, [datasheet]);

  useEffect(() => {
    if (!datasheet) return;

    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };

    const handleClickOutside = (e: MouseEvent) => {
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
  }, [datasheet, onClose]);

  const handleChange = (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
    const { name, value } = e.target;
    setFormData(prev => ({ ...prev, [name]: value }));
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!datasheet || !datasheet.product_id) return;

    // JS validation replaces UA `required` enforcement (Phase 4).
    if (!formData.product_name?.trim()) {
      setValidationError('Product name is required.');
      return;
    }
    if (!formData.url?.trim()) {
      setValidationError('Datasheet URL is required.');
      return;
    }
    try {
      new URL(formData.url.trim());
    } catch {
      setValidationError('Datasheet URL must be a valid URL.');
      return;
    }
    setValidationError(null);

    setIsSaving(true);
    try {
      await updateProduct(datasheet.product_id, formData, 'datasheet');
      onClose();
    } catch (error) {
      console.error('Failed to update datasheet:', error);
      alert('Failed to update datasheet');
    } finally {
      setIsSaving(false);
    }
  };

  if (!datasheet || !clickPosition) return null;

  return (
    <div className="product-detail-overlay">
      <div
        ref={modalRef}
        className="product-detail-modal"
        style={{
          transformOrigin: `${clickPosition.x}px ${clickPosition.y}px`,
          maxWidth: '500px'
        }}
      >
        <div className="product-detail-header">
          <h2>Edit Datasheet</h2>
          <button className="product-detail-close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>

        <div className="product-detail-content">
          <form noValidate onSubmit={handleSubmit} className="edit-form">
            {validationError && (
              <div role="alert" style={{ padding: '0.6rem 0.8rem', marginBottom: '1rem', background: '#b03232', color: '#fff', fontSize: '0.85rem' }}>
                {validationError}
              </div>
            )}
            <div className="form-group" style={{ marginBottom: '1.5rem' }}>
              <label htmlFor="product_name" style={{ display: 'block', marginBottom: '0.5rem', fontWeight: 500 }}>Product Name</label>
              <input
                type="text"
                id="product_name"
                name="product_name"
                value={formData.product_name || ''}
                onChange={handleChange}
                required
                className="form-input"
                style={{ width: '100%', padding: '0.75rem', borderRadius: '4px', border: '1px solid var(--border-color)' }}
              />
            </div>

            <div className="form-group" style={{ marginBottom: '1.5rem' }}>
              <label htmlFor="manufacturer" style={{ display: 'block', marginBottom: '0.5rem', fontWeight: 500 }}>Manufacturer</label>
              <input
                type="text"
                id="manufacturer"
                name="manufacturer"
                value={formData.manufacturer || ''}
                onChange={handleChange}
                className="form-input"
                style={{ width: '100%', padding: '0.75rem', borderRadius: '4px', border: '1px solid var(--border-color)' }}
              />
            </div>

            <div className="form-group" style={{ marginBottom: '1.5rem' }}>
              <label htmlFor="product_family" style={{ display: 'block', marginBottom: '0.5rem', fontWeight: 500 }}>Family</label>
              <input
                type="text"
                id="product_family"
                name="product_family"
                value={formData.product_family || ''}
                onChange={handleChange}
                className="form-input"
                style={{ width: '100%', padding: '0.75rem', borderRadius: '4px', border: '1px solid var(--border-color)' }}
              />
            </div>

            <div className="form-group" style={{ marginBottom: '1.5rem' }}>
              <label htmlFor="url" style={{ display: 'block', marginBottom: '0.5rem', fontWeight: 500 }}>Datasheet URL</label>
              <input
                type="url"
                id="url"
                name="url"
                value={formData.url || ''}
                onChange={handleChange}
                required
                className="form-input"
                style={{ width: '100%', padding: '0.75rem', borderRadius: '4px', border: '1px solid var(--border-color)' }}
              />
            </div>

            <div className="form-actions" style={{ marginTop: '2rem', display: 'flex', justifyContent: 'flex-end', gap: '1rem' }}>
              <button type="button" onClick={onClose} className="btn-secondary" style={{ padding: '0.75rem 1.5rem' }}>
                Cancel
              </button>
              <button type="submit" className="btn-primary" disabled={isSaving} style={{ padding: '0.75rem 1.5rem' }}>
                {isSaving ? 'Saving...' : 'Save Changes'}
              </button>
            </div>
          </form>
        </div>
      </div>
    </div>
  );
}
