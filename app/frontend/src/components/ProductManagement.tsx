import { useState } from 'react';
import Dropdown from './Dropdown';
import './ProductManagement.css';

interface DeleteResult {
  deleted: number;
  failed: number;
}

export default function ProductManagement() {
  const [activeTab, setActiveTab] = useState<'partNumber' | 'manufacturer' | 'name' | 'deduplicate'>('partNumber');
  const [inputValue, setInputValue] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [showModal, setShowModal] = useState(false);
  const [result, setResult] = useState<DeleteResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [dedupStats, setDedupStats] = useState<any>(null);
  
  const [manufacturers, setManufacturers] = useState<string[]>([]);
  const [names, setNames] = useState<string[]>([]);
  const [isFetchingOptions, setIsFetchingOptions] = useState(false);

  const fetchOptions = async (type: 'manufacturer' | 'name') => {
    setIsFetchingOptions(true);
    try {
      const endpoint = type === 'manufacturer' ? '/api/products/manufacturers' : '/api/products/names';
      const response = await fetch(endpoint);
      const data = await response.json();
      
      if (data.success) {
        if (type === 'manufacturer') setManufacturers(data.data);
        else setNames(data.data);
      }
    } catch (err) {
      console.error(`Error fetching ${type} options:`, err);
    } finally {
      setIsFetchingOptions(false);
    }
  };

  const handleTabChange = (tab: 'partNumber' | 'manufacturer' | 'name' | 'deduplicate') => {
    setActiveTab(tab);
    setInputValue('');
    setResult(null);
    setMessage(null);
    setError(null);
    setDedupStats(null);
    
    if (tab === 'manufacturer') fetchOptions('manufacturer');
    if (tab === 'name') fetchOptions('name');
  };

  const getEndpoint = () => {
    switch (activeTab) {
      case 'partNumber': return `/api/products/part-number/${encodeURIComponent(inputValue)}`;
      case 'manufacturer': return `/api/products/manufacturer/${encodeURIComponent(inputValue)}`;
      case 'name': return `/api/products/name/${encodeURIComponent(inputValue)}`;
      case 'deduplicate': return '/api/products/deduplicate';
    }
  };

  const getLabel = () => {
    switch (activeTab) {
      case 'partNumber': return 'Part Number';
      case 'manufacturer': return 'Manufacturer';
      case 'name': return 'Product Name';
      case 'deduplicate': return 'Deduplication';
    }
  };

  const getPlaceholder = () => {
    switch (activeTab) {
      case 'partNumber': return 'e.g. 123-456-789';
      case 'manufacturer': return 'Select a manufacturer...';
      case 'name': return 'Select a product name...';
      case 'deduplicate': return '';
    }
  };

  const handleDeleteClick = (e: React.FormEvent) => {
    e.preventDefault();
    if (activeTab !== 'deduplicate' && !inputValue.trim()) return;
    setShowModal(true);
  };

  const handleDeduplicateDryRun = async () => {
    setIsLoading(true);
    setError(null);
    setMessage(null);
    setDedupStats(null);
    
    try {
      const response = await fetch('/api/products/deduplicate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ confirm: false })
      });
      
      const data = await response.json();
      
      if (!response.ok) throw new Error(data.error || 'Failed to run deduplication');
      
      setDedupStats(data.data);
      if (data.data.found === 0) {
        setMessage("No duplicates found.");
      } else {
        setMessage(`Found ${data.data.found} duplicates.`);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'An unknown error occurred');
    } finally {
      setIsLoading(false);
    }
  };

  const confirmDelete = async () => {
    setShowModal(false);
    setIsLoading(true);
    setError(null);
    setMessage(null);
    setResult(null);

    try {
      const endpoint = activeTab === 'deduplicate' ? '/api/products/deduplicate' : getEndpoint();
      const method = activeTab === 'deduplicate' ? 'POST' : 'DELETE';
      const body = activeTab === 'deduplicate' ? JSON.stringify({ confirm: true }) : undefined;
      const headers = activeTab === 'deduplicate' ? { 'Content-Type': 'application/json' } : undefined;

      const response = await fetch(endpoint, { method, headers, body });
      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.error || 'Failed to delete products');
      }

      if (activeTab === 'deduplicate') {
        setDedupStats(data.data);
        setMessage(`Successfully deleted ${data.data.deleted} duplicates.`);
      } else {
        setResult(data.data);
        setMessage(data.message);
        setInputValue(''); // Clear input on success
        
        // Refresh options if we deleted by manufacturer or name
        if (activeTab === 'manufacturer') fetchOptions('manufacturer');
        if (activeTab === 'name') fetchOptions('name');
      }
      
    } catch (err) {
      setError(err instanceof Error ? err.message : 'An unknown error occurred');
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="product-management">
      <div className="management-container">
        <h2>Product Management</h2>
        
        <section className="management-card">
          <h3>Delete Products</h3>
          
          <div className="criteria-selector">
            <label htmlFor="criteriaSelect">Deletion Criteria:</label>
            <Dropdown<'partNumber' | 'manufacturer' | 'name' | 'deduplicate'>
              id="criteriaSelect"
              value={activeTab}
              onChange={handleTabChange}
              ariaLabel="Deletion criteria"
              className="dropdown-select"
              options={[
                { value: 'partNumber', label: 'By Part Number' },
                { value: 'manufacturer', label: 'By Manufacturer' },
                { value: 'name', label: 'By Product Name' },
                { value: 'deduplicate', label: 'Find & Delete Duplicates' },
              ]}
            />
          </div>

          {activeTab === 'deduplicate' ? (
            <div className="deduplicate-section">
              <p className="description">
                Scan the database for duplicate products (same Part Number, Name, Manufacturer) and remove them.
                <br />
                <span className="warning">Warning: This action is irreversible.</span>
              </p>
              
              <div className="action-buttons">
                <button 
                  type="button" 
                  className="btn-secondary" 
                  onClick={handleDeduplicateDryRun}
                  disabled={isLoading}
                >
                  {isLoading ? 'Scanning...' : 'Scan for Duplicates'}
                </button>
                
                <button 
                  type="button" 
                  className="btn-delete" 
                  onClick={() => setShowModal(true)}
                  disabled={isLoading}
                >
                  Delete Duplicates
                </button>
              </div>
              
              {dedupStats && (
                 <div className="result-stats">
                    <h4>Scan Results</h4>
                    <p>Duplicates Found: <strong>{dedupStats.found}</strong></p>
                    <p>Duplicates Deleted: <strong>{dedupStats.deleted}</strong></p>
                    
                    {dedupStats.duplicate_part_numbers && dedupStats.duplicate_part_numbers.length > 0 && (
                      <div className="duplicate-list-container" style={{ marginTop: '1rem' }}>
                        <h5>Duplicate Part Numbers:</h5>
                        <ul className="duplicate-list" style={{ 
                          maxHeight: '200px', 
                          overflowY: 'auto', 
                          border: '1px solid #ddd', 
                          padding: '0.5rem', 
                          listStyle: 'none',
                          marginTop: '0.5rem',
                          borderRadius: '4px'
                        }}>
                          {dedupStats.duplicate_part_numbers.map((pn: string, i: number) => (
                            <li key={i} style={{ padding: '2px 0', borderBottom: '1px solid #f0f0f0' }}>{pn}</li>
                          ))}
                        </ul>
                      </div>
                    )}
                 </div>
              )}
            </div>
          ) : (
            <>
              <p className="description">
                Delete all product records matching the specific {getLabel().toLowerCase()}.
                <br />
                <span className="warning">Warning: This action is irreversible.</span>
              </p>

              <form noValidate onSubmit={handleDeleteClick} className="delete-form">
                <div className="form-group">
                  <label htmlFor="inputValue">{getLabel()}</label>
                  
                  {activeTab === 'partNumber' ? (
                    <input
                      id="inputValue"
                      type="text"
                      value={inputValue}
                      onChange={(e) => setInputValue(e.target.value)}
                      placeholder={getPlaceholder()}
                      disabled={isLoading}
                    />
                  ) : (
                      <Dropdown<string>
                        id="inputValue"
                        value={inputValue}
                        onChange={setInputValue}
                        disabled={isLoading || isFetchingOptions}
                        className="dropdown-select"
                        fullWidth
                        ariaLabel={getLabel()}
                        placeholder={isFetchingOptions ? 'Loading...' : getPlaceholder()}
                        options={[
                          { value: '', label: isFetchingOptions ? 'Loading...' : getPlaceholder() },
                          ...(activeTab === 'manufacturer' ? manufacturers : names).map((opt) => ({
                            value: opt,
                            label: opt,
                          })),
                        ]}
                      />
                  )}
                </div>

                <button 
                  type="submit" 
                  className="btn-delete" 
                  disabled={!inputValue.trim() || isLoading}
                >
                  {isLoading ? 'Deleting...' : 'Delete Products'}
                </button>
              </form>
            </>
          )}

          {error && <div className="alert alert-error">{error}</div>}
          
          {message && (
            <div className="alert alert-success">
              {message}
            </div>
          )}

          {result && activeTab !== 'deduplicate' && (
            <div className="result-stats">
              <p>Deleted: <strong>{result.deleted}</strong></p>
              <p>Failed: <strong>{result.failed}</strong></p>
            </div>
          )}
        </section>
      </div>

      {showModal && (
        <div className="modal-overlay">
          <div className="modal-content">
            <h3>Confirm Deletion</h3>
            {activeTab === 'deduplicate' ? (
                <p>
                  Are you sure you want to delete <strong>ALL duplicate products</strong> found in the database?
                  <br />
                  We will keep one instance of each product and remove the rest.
                </p>
            ) : (
                <p>
                  Are you sure you want to delete all products with <strong>{getLabel()}</strong> matching:
                  <br />
                  <span className="highlight">"{inputValue}"</span>?
                </p>
            )}
            <p className="warning-text">This action cannot be undone.</p>
            
            <div className="modal-actions">
              <button 
                className="btn-cancel" 
                onClick={() => setShowModal(false)}
              >
                Cancel
              </button>
              <button 
                className="btn-confirm-delete" 
                onClick={confirmDelete}
              >
                Yes, Delete
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
