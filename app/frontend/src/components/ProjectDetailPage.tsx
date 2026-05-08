/**
 * ProjectDetailPage — drill-in for one project.
 *
 * Shows the project's products in a compact table (manufacturer,
 * part_number, type, with a per-row Remove button), supports renaming
 * the project, and exposes Delete from the detail view.
 *
 * Product details are dereferenced from the live products feed at
 * mount time using `apiClient.getProduct(id, type)` per ref. A ref
 * that 404s renders as a "removed product" placeholder rather than
 * silently dropping — keeps the count accurate and gives the user
 * something to click "Remove" on.
 */

import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { useProjects } from '../context/ProjectsContext';
import { useConfirm } from './ui/ConfirmDialog';
import { apiClient } from '../api/client';
import type { Product, ProductType } from '../types/models';
import type { ProductRef } from '../types/projects';

interface ResolvedRef extends ProductRef {
  product?: Product;
  status: 'ok' | 'missing' | 'error';
}

async function resolveRef(ref: ProductRef): Promise<ResolvedRef> {
  try {
    const product = await apiClient.getProduct(ref.product_id, ref.product_type as ProductType);
    return { ...ref, product, status: 'ok' };
  } catch (err) {
    const msg = err instanceof Error ? err.message : '';
    if (msg.toLowerCase().includes('not found') || msg.includes('404')) {
      return { ...ref, status: 'missing' };
    }
    return { ...ref, status: 'error' };
  }
}

export default function ProjectDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { user } = useAuth();
  const {
    projects,
    loading,
    renameProject,
    deleteProject,
    removeProductFrom,
  } = useProjects();
  const confirm = useConfirm();

  const project = projects.find(p => p.id === id);

  const [resolved, setResolved] = useState<ResolvedRef[]>([]);
  const [resolving, setResolving] = useState(false);
  const [pendingRefId, setPendingRefId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const [editing, setEditing] = useState(false);
  const [draftName, setDraftName] = useState('');

  useEffect(() => {
    if (!project) return;
    let cancelled = false;
    setResolving(true);
    Promise.all(project.product_refs.map(resolveRef)).then(results => {
      if (!cancelled) {
        setResolved(results);
        setResolving(false);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [project]);

  if (!user) {
    return (
      <section className="projects-page projects-page-empty">
        <h2>Project</h2>
        <p>Sign in to view this project.</p>
      </section>
    );
  }

  if (!project) {
    return (
      <section className="projects-page">
        <button
          type="button"
          className="project-detail-back"
          onClick={() => navigate('/projects')}
        >
          ← Back to projects
        </button>
        {loading ? (
          <p className="projects-page-empty-msg">Loading…</p>
        ) : (
          <p className="projects-page-empty-msg">
            Project not found. It may have been deleted.
          </p>
        )}
      </section>
    );
  }

  const handleStartEdit = () => {
    setDraftName(project.name);
    setEditing(true);
    setActionError(null);
  };

  const handleSaveName = async () => {
    const trimmed = draftName.trim();
    if (!trimmed || trimmed === project.name) {
      setEditing(false);
      return;
    }
    setActionError(null);
    try {
      await renameProject(project.id, trimmed);
      setEditing(false);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Rename failed');
    }
  };

  const handleDelete = async () => {
    const ok = await confirm({
      title: 'Delete project?',
      body: `"${project.name}" and all its product references will be removed. This can't be undone.`,
      confirmLabel: 'Delete',
      confirmVariant: 'danger',
    });
    if (!ok) return;
    setActionError(null);
    try {
      await deleteProject(project.id);
      navigate('/projects');
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Delete failed');
    }
  };

  const handleRemove = async (ref: ProductRef) => {
    const key = `${ref.product_type}#${ref.product_id}`;
    const cleanRef: ProductRef = {
      product_type: ref.product_type,
      product_id: ref.product_id,
    };
    setActionError(null);
    setPendingRefId(key);
    try {
      await removeProductFrom(project.id, cleanRef);
      setResolved(prev =>
        prev.filter(
          r => !(r.product_type === ref.product_type && r.product_id === ref.product_id),
        ),
      );
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Remove failed');
    } finally {
      setPendingRefId(null);
    }
  };

  return (
    <section className="projects-page project-detail">
      <button
        type="button"
        className="project-detail-back"
        onClick={() => navigate('/projects')}
      >
        ← Back to projects
      </button>

      <header className="project-detail-header">
        {editing ? (
          <form
            className="project-detail-name-edit"
            onSubmit={e => {
              e.preventDefault();
              void handleSaveName();
            }}
          >
            <input
              type="text"
              value={draftName}
              onChange={e => setDraftName(e.target.value)}
              maxLength={120}
              aria-label="Project name"
              autoFocus
            />
            <button type="submit" className="project-detail-save">Save</button>
            <button
              type="button"
              className="project-detail-cancel"
              onClick={() => setEditing(false)}
            >
              Cancel
            </button>
          </form>
        ) : (
          <div className="project-detail-name-row">
            <h2 className="project-detail-name">{project.name}</h2>
            <button
              type="button"
              className="project-detail-rename"
              onClick={handleStartEdit}
            >
              Rename
            </button>
          </div>
        )}
        <p className="projects-page-subtitle">
          {project.product_refs.length}{' '}
          {project.product_refs.length === 1 ? 'product' : 'products'} · Updated{' '}
          {new Date(project.updated_at).toLocaleDateString()}
        </p>
      </header>

      {actionError && <p className="projects-page-error" role="alert">{actionError}</p>}

      {project.product_refs.length === 0 ? (
        <p className="projects-page-empty-msg">
          No products in this project yet. Open any product detail and use{' '}
          <strong>Add to project</strong>.
        </p>
      ) : resolving ? (
        <p className="projects-page-empty-msg">Loading products…</p>
      ) : (
        <table className="project-detail-table">
          <thead>
            <tr>
              <th>Manufacturer</th>
              <th>Part number</th>
              <th>Type</th>
              <th aria-label="Actions" />
            </tr>
          </thead>
          <tbody>
            {resolved.map(r => {
              const key = `${r.product_type}#${r.product_id}`;
              const busy = pendingRefId === key;
              if (r.status !== 'ok' || !r.product) {
                return (
                  <tr key={key} className="project-detail-row project-detail-row-missing">
                    <td colSpan={3}>
                      <em>
                        {r.status === 'missing' ? 'Removed product' : 'Failed to load'}
                      </em>{' '}
                      ({r.product_type} · {r.product_id})
                    </td>
                    <td>
                      <button
                        type="button"
                        className="project-detail-remove"
                        onClick={() => handleRemove(r)}
                        disabled={busy}
                      >
                        {busy ? '…' : 'Remove'}
                      </button>
                    </td>
                  </tr>
                );
              }
              const p = r.product;
              return (
                <tr key={key} className="project-detail-row">
                  <td>{p.manufacturer || '—'}</td>
                  <td>{p.part_number || '—'}</td>
                  <td>{p.product_type}</td>
                  <td>
                    <button
                      type="button"
                      className="project-detail-remove"
                      onClick={() => handleRemove(r)}
                      disabled={busy}
                    >
                      {busy ? '…' : 'Remove'}
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}

      <footer className="project-detail-footer">
        <button
          type="button"
          className="projects-card-delete"
          onClick={handleDelete}
        >
          Delete project
        </button>
      </footer>
    </section>
  );
}
