import { useState, useEffect, useMemo } from 'react';
import { useApp } from '../context/AppContext';
import { DatasheetEntry } from '../types/models';
import { FilterCriterion } from '../types/filters';
import { useColumnResize } from '../utils/hooks';
import DatasheetFilterBar from './DatasheetFilterBar';
import DatasheetEditModal from './DatasheetEditModal';
import Dropdown from './Dropdown';
import ExternalLink from './ui/ExternalLink';
import { sanitizeUrl } from '../utils/sanitize';

export default function DatasheetList() {
  const { products, loadProducts, loading, error, deleteProduct } = useApp();
  const [filters, setFilters] = useState<FilterCriterion[]>([]);
  const [sorts, setSorts] = useState<{ attribute: string; direction: 'asc' | 'desc' }[]>([]);
  const [itemsPerPage, setItemsPerPage] = useState<number>(25);
  const [currentPage, setCurrentPage] = useState<number>(1);
  const [selectedDatasheet, setSelectedDatasheet] = useState<DatasheetEntry | null>(null);
  const [clickPosition, setClickPosition] = useState<{ x: number; y: number } | null>(null);

  const { columnWidths, startResize } = useColumnResize({
    product_name: 250,
    product_type: 120,
    product_family: 150,
    manufacturer: 150,
    actions: 80,
  });

  useEffect(() => {
    loadProducts('datasheet');
  }, [loadProducts]);

  const datasheetProducts = useMemo(() => {
    return products.filter((p): p is DatasheetEntry => p.product_type === 'datasheet');
  }, [products]);

  const filteredProducts = useMemo(() => {
    let result = [...datasheetProducts];

    // Apply filters
    if (filters.length > 0) {
      result = result.filter(product => {
        return filters.every(filter => {
          const value = (product as any)[filter.attribute];
          
          if (value === undefined || value === null) return false;

          const op = filter.operator as string;
          switch (op) {
            case 'equals':
            case '=':
              return String(value).toLowerCase() === String(filter.value).toLowerCase();
            case 'contains':
              return String(value).toLowerCase().includes(String(filter.value).toLowerCase());
            case '>':
              return Number(value) > Number(filter.value);
            case '<':
              return Number(value) < Number(filter.value);
            case '>=':
              return Number(value) >= Number(filter.value);
            case '<=':
              return Number(value) <= Number(filter.value);
            case '!=':
              return String(value).toLowerCase() !== String(filter.value).toLowerCase();
            default:
              return true;
          }
        });
      });
    }

    return result;
  }, [datasheetProducts, filters]);

  // Sorting Logic
  const sortedProducts = useMemo(() => {
    if (sorts.length === 0) return filteredProducts;

    return [...filteredProducts].sort((a, b) => {
      for (const sort of sorts) {
        const valueA = (a as any)[sort.attribute];
        const valueB = (b as any)[sort.attribute];

        if (valueA === valueB) continue;
        if (valueA === null || valueA === undefined) return 1;
        if (valueB === null || valueB === undefined) return -1;

        const comparison = String(valueA).localeCompare(String(valueB), undefined, { numeric: true });
        return sort.direction === 'asc' ? comparison : -comparison;
      }
      return 0;
    });
  }, [filteredProducts, sorts]);

  // Pagination
  const totalPages = Math.ceil(sortedProducts.length / itemsPerPage);
  const paginatedProducts = sortedProducts.slice(
    (currentPage - 1) * itemsPerPage,
    currentPage * itemsPerPage
  );

  const handleDelete = async (id: string, componentType?: string) => {
    if (window.confirm('Are you sure you want to delete this datasheet?')) {
      await deleteProduct(id, 'datasheet', componentType);
    }
  };

  const handleColumnSort = (attribute: string) => {
    setSorts(prev => {
      const existing = prev.find(s => s.attribute === attribute);
      if (existing) {
        if (existing.direction === 'asc') {
          return prev.map(s => s.attribute === attribute ? { ...s, direction: 'desc' } : s);
        } else {
          return prev.filter(s => s.attribute !== attribute);
        }
      } else {
        return [...prev, { attribute, direction: 'asc' }];
      }
    });
  };

  const getSortIndicator = (attribute: string) => {
    const sort = sorts.find(s => s.attribute === attribute);
    if (!sort) return null;
    return sort.direction === 'asc' ? ' ↑' : ' ↓';
  };

  const headerStyle = {
    padding: '0.75rem', 
    fontWeight: 600, 
    fontSize: '0.85rem', 
    textTransform: 'uppercase' as const, 
    letterSpacing: '0.05em', 
    color: 'var(--text-secondary)',
    cursor: 'pointer',
    userSelect: 'none' as const
  };

  return (
    <div className="page-split-layout">
      <aside className="filter-sidebar">
        <DatasheetFilterBar
          filters={filters}
          datasheets={datasheetProducts}
          onFiltersChange={setFilters}
        />
      </aside>

      <main className="results-main">
        <div className="results-header">
          <div className="results-header-left">
            <span className="results-count">
              {sortedProducts.length} Datasheets
            </span>
            {loading && (
              <span style={{ marginLeft: '0.8rem', opacity: 0.6, fontSize: '0.85rem' }}>Loading...</span>
            )}
          </div>
          <div className="results-header-right">
             <div className="pagination-controls">
              <label className="pagination-label">Show:</label>
              <Dropdown<number>
                value={itemsPerPage}
                onChange={setItemsPerPage}
                ariaLabel="Items per page"
                className="pagination-select"
                options={[10, 25, 50].map((n) => ({ value: n, label: String(n) }))}
              />
            </div>
          </div>
        </div>

        {error && (
          <div className="error-message" style={{ margin: '0.5rem 0' }}>{error}</div>
        )}

        <div className="datasheet-table-container" style={{ maxWidth: '1000px', margin: '0 auto' }}>
          <table className="datasheet-table" style={{ width: '100%', borderCollapse: 'collapse', marginTop: '1rem', tableLayout: 'fixed' }}>
            <colgroup>
              <col style={{ width: columnWidths['product_name'] }} />
              <col style={{ width: columnWidths['product_type'] }} />
              <col style={{ width: columnWidths['product_family'] }} />
              <col style={{ width: columnWidths['manufacturer'] }} />
              <col style={{ width: columnWidths['actions'] }} />
            </colgroup>
            <thead>
              <tr style={{ borderBottom: '2px solid var(--border-color)', textAlign: 'left' }}>
                <th style={{ ...headerStyle, position: 'relative' }} onClick={() => handleColumnSort('product_name')}>
                  Product Name{getSortIndicator('product_name')}
                  <div className="col-resize-handle" onMouseDown={(e) => startResize('product_name', e)} />
                </th>
                <th style={{ ...headerStyle, position: 'relative' }} onClick={() => handleColumnSort('product_type')}>
                  Product Type{getSortIndicator('product_type')}
                  <div className="col-resize-handle" onMouseDown={(e) => startResize('product_type', e)} />
                </th>
                <th style={{ ...headerStyle, position: 'relative' }} onClick={() => handleColumnSort('product_family')}>
                  Family{getSortIndicator('product_family')}
                  <div className="col-resize-handle" onMouseDown={(e) => startResize('product_family', e)} />
                </th>
                <th style={{ ...headerStyle, position: 'relative' }} onClick={() => handleColumnSort('manufacturer')}>
                  Manufacturer{getSortIndicator('manufacturer')}
                  <div className="col-resize-handle" onMouseDown={(e) => startResize('manufacturer', e)} />
                </th>
                <th style={headerStyle}>Status</th>
                <th style={{ ...headerStyle, cursor: 'default', textAlign: 'right' }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {paginatedProducts.map(datasheet => (
                <tr 
                  key={datasheet.product_id || datasheet.url} 
                  style={{ borderBottom: '1px solid var(--border-color)', cursor: 'pointer' }}
                  className="datasheet-row hover-highlight"
                  onClick={(e) => {
                    setClickPosition({ x: e.clientX, y: e.clientY });
                    setSelectedDatasheet(datasheet);
                  }}
                >
                  <td style={{ padding: '0.5rem 0.75rem', fontSize: '0.9rem', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    <ExternalLink
                      href={sanitizeUrl(datasheet.url)}
                      tooltip={`Open ${datasheet.product_name} datasheet PDF`}
                      style={{ fontWeight: 500, color: 'var(--accent-primary)', textDecoration: 'none' }}
                      onClick={(e) => e.stopPropagation()}
                    >
                      {datasheet.product_name}
                    </ExternalLink>
                  </td>
                  <td style={{ padding: '0.5rem 0.75rem', fontSize: '0.9rem', textTransform: 'capitalize', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{datasheet.component_type || '-'}</td>
                  <td style={{ padding: '0.5rem 0.75rem', fontSize: '0.9rem', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{datasheet.product_family || '-'}</td>
                  <td style={{ padding: '0.5rem 0.75rem', fontSize: '0.9rem', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{datasheet.manufacturer || 'Unknown'}</td>
                  <td style={{ padding: '0.5rem 0.75rem', fontSize: '0.9rem' }}>
                    {datasheet.is_scraped ? (
                      <span style={{ color: '#10B981', fontWeight: 500, display: 'inline-flex', alignItems: 'center', gap: '0.25rem' }}>
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg>
                        Scraped
                      </span>
                    ) : (
                      <span style={{ color: '#F59E0B', fontWeight: 500, display: 'inline-flex', alignItems: 'center', gap: '0.25rem' }}>
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10"></circle></svg>
                        Pending
                      </span>
                    )}
                  </td>
                  <td style={{ padding: '0.5rem 0.75rem', textAlign: 'right' }}>
                    <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '0.5rem' }}>
                      <button
                        className="btn-icon delete"
                        onClick={(e) => {
                          e.stopPropagation();
                          handleDelete(datasheet.product_id || '', datasheet.component_type);
                        }}
                        title="Delete Datasheet"
                        style={{ padding: '0.25rem', display: 'flex', alignItems: 'center', justifyContent: 'center' }}
                      >
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                          <polyline points="3 6 5 6 21 6"></polyline>
                          <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
                          <line x1="10" y1="11" x2="10" y2="17"></line>
                          <line x1="14" y1="11" x2="14" y2="17"></line>
                        </svg>
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          
          {paginatedProducts.length === 0 && (
            <div style={{ padding: '2rem', textAlign: 'center', color: 'var(--text-secondary)' }}>
              No datasheets found matching your filters.
            </div>
          )}
        </div>

        {totalPages > 1 && (
          <div className="pagination-nav">
            <button 
              className="pagination-btn"
              disabled={currentPage === 1}
              onClick={() => setCurrentPage(p => p - 1)}
            >
              ← Previous
            </button>
            <span className="pagination-info">Page {currentPage} of {totalPages}</span>
            <button 
              className="pagination-btn"
              disabled={currentPage === totalPages}
              onClick={() => setCurrentPage(p => p + 1)}
            >
              Next →
            </button>
          </div>
        )}
      </main>

      <DatasheetEditModal
        datasheet={selectedDatasheet}
        onClose={() => {
          setSelectedDatasheet(null);
          setClickPosition(null);
        }}
        clickPosition={clickPosition}
      />
    </div>
  );
}
