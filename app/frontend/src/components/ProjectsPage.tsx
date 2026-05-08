/**
 * ProjectsPage — list of the signed-in user's projects.
 *
 * Step 3 scope: list view with name, product count, timestamps, and a
 * delete control per project. Drill-into-a-project (rename, list of
 * products with remove buttons) is step 4.
 */

import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { useProjects } from '../context/ProjectsContext';
import type { Project } from '../types/projects';
import { useConfirm } from './ui/ConfirmDialog';

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString(undefined, {
      year: 'numeric', month: 'short', day: 'numeric',
    });
  } catch {
    return iso;
  }
}

function ProjectCard({
  project,
  busy,
  onOpen,
  onDelete,
}: {
  project: Project;
  busy: boolean;
  onOpen: () => void;
  onDelete: () => void;
}) {
  const count = project.product_refs.length;
  // Card is clickable but the Delete button is a nested interactive
  // element; stop propagation there so the row navigation doesn't fire.
  const handleKey = (e: React.KeyboardEvent<HTMLElement>) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      onOpen();
    }
  };
  return (
    <article
      className="projects-card projects-card-clickable"
      role="button"
      tabIndex={0}
      aria-label={`Open project ${project.name}`}
      onClick={onOpen}
      onKeyDown={handleKey}
    >
      <header className="projects-card-header">
        <h3 className="projects-card-name">{project.name}</h3>
        <span className="projects-card-count" aria-label={`${count} products`}>
          {count} {count === 1 ? 'product' : 'products'}
        </span>
      </header>
      <dl className="projects-card-meta">
        <div>
          <dt>Created</dt>
          <dd>{formatDate(project.created_at)}</dd>
        </div>
        <div>
          <dt>Updated</dt>
          <dd>{formatDate(project.updated_at)}</dd>
        </div>
      </dl>
      <div className="projects-card-actions">
        <button
          type="button"
          className="projects-card-delete"
          onClick={e => {
            e.stopPropagation();
            onDelete();
          }}
          disabled={busy}
        >
          {busy ? 'Deleting…' : 'Delete'}
        </button>
      </div>
    </article>
  );
}

export default function ProjectsPage() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const { projects, loading, error, deleteProject } = useProjects();
  const confirm = useConfirm();
  const [busyId, setBusyId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  if (!user) {
    return (
      <section className="projects-page projects-page-empty">
        <h2>Projects</h2>
        <p>Sign in to view and manage your projects.</p>
      </section>
    );
  }

  const sorted = [...projects].sort(
    (a, b) => b.updated_at.localeCompare(a.updated_at),
  );

  const handleDelete = async (project: Project) => {
    const ok = await confirm({
      title: 'Delete project?',
      body: `"${project.name}" and all its product references will be removed. This can't be undone.`,
      confirmLabel: 'Delete',
      confirmVariant: 'danger',
    });
    if (!ok) return;
    setActionError(null);
    setBusyId(project.id);
    try {
      await deleteProject(project.id);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Delete failed');
    } finally {
      setBusyId(null);
    }
  };

  return (
    <section className="projects-page">
      <header className="projects-page-header">
        <h2>Projects</h2>
        <p className="projects-page-subtitle">
          Saved collections of products. Add to a project from any product detail.
        </p>
      </header>

      {error && <p className="projects-page-error" role="alert">{error}</p>}
      {actionError && <p className="projects-page-error" role="alert">{actionError}</p>}

      {loading && projects.length === 0 ? (
        <p className="projects-page-empty-msg">Loading…</p>
      ) : sorted.length === 0 ? (
        <p className="projects-page-empty-msg">
          No projects yet. Open any product, click <strong>Add to project</strong>,
          and create one.
        </p>
      ) : (
        <div className="projects-grid">
          {sorted.map(p => (
            <ProjectCard
              key={p.id}
              project={p}
              busy={busyId === p.id}
              onOpen={() => navigate(`/projects/${p.id}`)}
              onDelete={() => handleDelete(p)}
            />
          ))}
        </div>
      )}
    </section>
  );
}
