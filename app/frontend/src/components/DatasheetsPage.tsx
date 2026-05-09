import { useState, useRef } from 'react';
import { useApp } from '../context/AppContext';
import DatasheetList from './DatasheetList';
import Dropdown from './Dropdown';

export default function DatasheetsPage() {
  const { createDatasheet, categories } = useApp();
  const [showAddModal, setShowAddModal] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const [formData, setFormData] = useState({
    product_name: '',
    product_family: '',
    manufacturer: '',
    product_type: '',
    new_product_type: '',
    url: '',
    pages: ''
  });
  const [status, setStatus] = useState<{ type: 'success' | 'error', message: string } | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setStatus(null);

    // JS validation replaces UA `required` enforcement (Phase 4 — the
    // form has noValidate, so the browser no longer checks anything).
    if (!formData.product_name.trim()) {
      setStatus({ type: 'error', message: 'Product name is required' });
      return;
    }
    if (!formData.manufacturer.trim()) {
      setStatus({ type: 'error', message: 'Manufacturer is required' });
      return;
    }
    const finalProductType = formData.product_type === 'new'
      ? formData.new_product_type.toLowerCase().trim()
      : formData.product_type;
    if (!finalProductType) {
      setStatus({ type: 'error', message: 'Product type is required' });
      return;
    }
    if (!formData.url.trim()) {
      setStatus({ type: 'error', message: 'Datasheet URL is required' });
      return;
    }
    try {
      // The URL constructor throws on malformed inputs — same coverage
      // as the dropped `type="url"` UA validation, with our wording.
      new URL(formData.url.trim());
    } catch {
      setStatus({ type: 'error', message: 'Datasheet URL must be a valid URL' });
      return;
    }

    setIsSubmitting(true);
    try {
      const pagesArray = formData.pages
        ? formData.pages.split(',').map(p => parseInt(p.trim())).filter(n => !isNaN(n))
        : [];

      const payload = {
        product_type: finalProductType, // Use selected or new type
        product_name: formData.product_name,
        product_family: formData.product_family || undefined,
        manufacturer: formData.manufacturer,
        url: formData.url,
        pages: pagesArray.length > 0 ? pagesArray : undefined,
      };

      await createDatasheet(payload as any);

      setStatus({ type: 'success', message: 'Datasheet submitted successfully!' });
      setFormData({
        product_name: '',
        product_family: '',
        manufacturer: '',
        product_type: '',
        new_product_type: '',
        url: '',
        pages: ''
      });
      setTimeout(() => {
        setShowAddModal(false);
        setStatus(null);
      }, 1500);
    } catch (error) {
      console.error('Error submitting datasheet:', error);
      setStatus({ type: 'error', message: error instanceof Error ? error.message : 'Failed to submit' });
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleChange = (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
    const { name, value } = e.target;
    setFormData(prev => ({ ...prev, [name]: value }));
  };

  // Calculate modal position based on button
  const modalStyle = showAddModal && buttonRef.current 
    ? {
        top: buttonRef.current.getBoundingClientRect().bottom + 10,
        right: window.innerWidth - buttonRef.current.getBoundingClientRect().right,
      }
    : { top: 80, right: 32 };

  return (
    <div className="query-page" style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      <div className="page-header" style={{ 
        padding: '1rem 2rem', 
        borderBottom: '1px solid var(--border-color)',
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        backgroundColor: 'var(--bg-primary)'
      }}>
        <h1 style={{ fontSize: '1.5rem', fontWeight: 600 }}>Datasheets</h1>
        <button 
          ref={buttonRef}
          className="btn-primary"
          onClick={() => setShowAddModal(!showAddModal)}
          style={{
            padding: '0.5rem 1rem',
            backgroundColor: 'var(--accent-color)',
            color: 'white',
            border: 'none',
            borderRadius: '4px',
            cursor: 'pointer',
            fontWeight: 500
          }}
        >
          + Add Datasheet
        </button>
      </div>

      <div style={{ flex: 1, overflow: 'hidden' }}>
        <DatasheetList />
      </div>

      {/* Add Datasheet Modal - Popover Style */}
      {showAddModal && (
        <>
          <div 
            className="modal-backdrop" 
            onClick={() => setShowAddModal(false)}
            style={{
              position: 'fixed',
              top: 0,
              left: 0,
              right: 0,
              bottom: 0,
              zIndex: 99,
              backgroundColor: 'transparent' // Transparent backdrop
            }}
          />
          <div 
            className="add-datasheet-modal" 
            style={{ 
              position: 'fixed',
              zIndex: 100,
              ...modalStyle,
              width: '400px',
              backgroundColor: 'var(--card-bg)',
              border: '1px solid var(--border-color)',
              borderRadius: '8px',
              boxShadow: '0 4px 20px var(--shadow-lg)',
              padding: '1.5rem',
              animation: 'fadeIn 0.2s ease-out'
            }}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1.5rem' }}>
              <h3 style={{ margin: 0 }}>Add New Datasheet</h3>
              <button
                onClick={() => setShowAddModal(false)}
                aria-label="Close"
                style={{ background: 'none', border: 'none', fontSize: '1.5rem', cursor: 'pointer', color: 'var(--danger)', opacity: 0.75, padding: '0 0.25rem', lineHeight: 1, borderRadius: 3 }}
              >
                ×
              </button>
            </div>
            
            <form noValidate onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
              <div className="form-group">
                <label htmlFor="product_name" style={{ display: 'block', marginBottom: '0.4rem', fontSize: '0.9rem', fontWeight: 500 }}>Product Name *</label>
                <input
                  type="text"
                  id="product_name"
                  name="product_name"
                  value={formData.product_name}
                  onChange={handleChange}
                  required
                  className="form-input"
                  style={{ width: '100%', padding: '0.6rem', borderRadius: '4px', border: '1px solid var(--border-color)' }}
                />
              </div>

              <div className="form-group">
                <label htmlFor="product_family" style={{ display: 'block', marginBottom: '0.4rem', fontSize: '0.9rem', fontWeight: 500 }}>Product Family</label>
                <input
                  type="text"
                  id="product_family"
                  name="product_family"
                  value={formData.product_family}
                  onChange={handleChange}
                  className="form-input"
                  style={{ width: '100%', padding: '0.6rem', borderRadius: '4px', border: '1px solid var(--border-color)' }}
                />
              </div>

              <div className="form-group">
                <label htmlFor="manufacturer" style={{ display: 'block', marginBottom: '0.4rem', fontSize: '0.9rem', fontWeight: 500 }}>Manufacturer *</label>
                <input
                  type="text"
                  id="manufacturer"
                  name="manufacturer"
                  value={formData.manufacturer}
                  onChange={handleChange}
                  required
                  className="form-input"
                  style={{ width: '100%', padding: '0.6rem', borderRadius: '4px', border: '1px solid var(--border-color)' }}
                />
              </div>

              <div className="form-group">
                <label htmlFor="product_type" style={{ display: 'block', marginBottom: '0.4rem', fontSize: '0.9rem', fontWeight: 500 }}>Type *</label>
                <div style={{ marginBottom: formData.product_type === 'new' ? '0.5rem' : 0 }}>
                  <Dropdown<string>
                    id="product_type"
                    name="product_type"
                    ariaLabel="Product type"
                    fullWidth
                    value={formData.product_type}
                    onChange={(v) => setFormData(prev => ({ ...prev, product_type: v }))}
                    placeholder="Select Type"
                    options={[
                      { value: '', label: 'Select Type' },
                      ...categories.map((cat) => ({ value: cat.type, label: cat.display_name })),
                      { value: 'new', label: '+ Create New Type' },
                    ]}
                  />
                </div>
                
                {formData.product_type === 'new' && (
                  <input
                    type="text"
                    name="new_product_type"
                    value={formData.new_product_type}
                    onChange={handleChange}
                    placeholder="Enter new type name"
                    required
                    className="form-input"
                    style={{ width: '100%', padding: '0.6rem', borderRadius: '4px', border: '1px solid var(--border-color)' }}
                  />
                )}
              </div>

              <div className="form-group">
                <label htmlFor="url" style={{ display: 'block', marginBottom: '0.4rem', fontSize: '0.9rem', fontWeight: 500 }}>Datasheet URL *</label>
                <input
                  type="url"
                  id="url"
                  name="url"
                  value={formData.url}
                  onChange={handleChange}
                  required
                  className="form-input"
                  style={{ width: '100%', padding: '0.6rem', borderRadius: '4px', border: '1px solid var(--border-color)' }}
                />
              </div>

              <div className="form-group">
                <label htmlFor="pages" style={{ display: 'block', marginBottom: '0.4rem', fontSize: '0.9rem', fontWeight: 500 }}>Pages (Optional)</label>
                <input
                  type="text"
                  id="pages"
                  name="pages"
                  value={formData.pages}
                  onChange={handleChange}
                  placeholder="e.g. 3, 4, 5"
                  className="form-input"
                  style={{ width: '100%', padding: '0.6rem', borderRadius: '4px', border: '1px solid var(--border-color)' }}
                />
              </div>

              {status && (
                <div className={`alert alert-${status.type}`} style={{ padding: '0.75rem', borderRadius: '4px', fontSize: '0.9rem', backgroundColor: status.type === 'success' ? 'var(--success)' : 'var(--danger)', color: 'white' }}>
                  {status.message}
                </div>
              )}

              <div className="modal-actions" style={{ display: 'flex', justifyContent: 'flex-end', gap: '1rem', marginTop: '1rem' }}>
                <button 
                  type="button" 
                  className="btn-cancel"
                  onClick={() => setShowAddModal(false)}
                  style={{ padding: '0.6rem 1.2rem', background: 'transparent', border: '1px solid var(--border-color)', borderRadius: '4px', cursor: 'pointer' }}
                >
                  Cancel
                </button>
                <button 
                  type="submit" 
                  className="btn-confirm"
                  disabled={isSubmitting}
                  style={{ padding: '0.6rem 1.2rem', background: 'var(--accent-primary)', color: 'white', border: 'none', borderRadius: '4px', cursor: 'pointer' }}
                >
                  {isSubmitting ? 'Adding...' : 'Add Datasheet'}
                </button>
              </div>
            </form>
          </div>
        </>
      )}
    </div>
  );
}
