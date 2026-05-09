/**
 * "Add to project" menu — a popover with one checkbox per project
 * plus an inline "Create new project" row. Toggling a checkbox or
 * creating a new project hits the API immediately; the popover stays
 * open so a user can add the same product to several projects without
 * re-opening it.
 *
 * Anonymous users see a static CTA in place of the menu — disabled,
 * no popover, prompts them to sign in via the header.
 */

import {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  KeyboardEvent,
} from 'react';
import { createPortal } from 'react-dom';
import { useAuth } from '../context/AuthContext';
import { useProjects } from '../context/ProjectsContext';
import type { ProductRef } from '../types/projects';

interface AddToProjectMenuProps {
  productRef: ProductRef;
}

interface PopoverRect {
  top: number;
  left: number;
  width: number;
  maxHeight: number;
  placement: 'below' | 'above';
}

const POPOVER_GAP = 4;
const POPOVER_PAD = 8;
const POPOVER_MIN_WIDTH = 240;

export default function AddToProjectMenu({ productRef }: AddToProjectMenuProps) {
  const { user } = useAuth();
  const {
    projects,
    loading,
    error,
    addProductTo,
    removeProductFrom,
    createProject,
    isInProject,
  } = useProjects();

  const [open, setOpen] = useState(false);
  const [rect, setRect] = useState<PopoverRect | null>(null);
  const [pendingId, setPendingId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [newName, setNewName] = useState('');
  const [creating, setCreating] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);
  const reactId = useId();
  const popoverId = `add-to-project-${reactId}`;

  const sortedProjects = useMemo(
    () => [...projects].sort((a, b) => a.name.localeCompare(b.name)),
    [projects],
  );

  const computeRect = useCallback((): PopoverRect | null => {
    const trigger = triggerRef.current;
    if (!trigger) return null;
    const tRect = trigger.getBoundingClientRect();
    const viewportH = window.innerHeight;
    const spaceBelow = viewportH - tRect.bottom - POPOVER_PAD;
    const spaceAbove = tRect.top - POPOVER_PAD;
    const placeAbove = spaceBelow < 220 && spaceAbove > spaceBelow;
    const maxHeight = Math.max(160, Math.min(360, placeAbove ? spaceAbove : spaceBelow));
    return {
      top: placeAbove ? tRect.top - POPOVER_GAP : tRect.bottom + POPOVER_GAP,
      left: tRect.left,
      width: Math.max(POPOVER_MIN_WIDTH, tRect.width),
      maxHeight,
      placement: placeAbove ? 'above' : 'below',
    };
  }, []);

  useLayoutEffect(() => {
    if (!open) return;
    setRect(computeRect());
    const handler = () => setRect(computeRect());
    window.addEventListener('resize', handler);
    window.addEventListener('scroll', handler, true);
    return () => {
      window.removeEventListener('resize', handler);
      window.removeEventListener('scroll', handler, true);
    };
  }, [open, computeRect]);

  useEffect(() => {
    if (!open) return;
    const handlePointer = (e: MouseEvent) => {
      const target = e.target as Node;
      if (
        triggerRef.current?.contains(target) ||
        popoverRef.current?.contains(target)
      ) {
        return;
      }
      setOpen(false);
    };
    document.addEventListener('mousedown', handlePointer);
    return () => document.removeEventListener('mousedown', handlePointer);
  }, [open]);

  const handleTriggerKey = (e: KeyboardEvent<HTMLButtonElement>) => {
    if (e.key === 'Enter' || e.key === ' ' || e.key === 'ArrowDown') {
      e.preventDefault();
      setOpen(v => !v);
    } else if (e.key === 'Escape' && open) {
      e.preventDefault();
      setOpen(false);
    }
  };

  const handleToggle = async (projectId: string) => {
    if (pendingId) return;
    setActionError(null);
    setPendingId(projectId);
    try {
      if (isInProject(projectId, productRef)) {
        await removeProductFrom(projectId, productRef);
      } else {
        await addProductTo(projectId, productRef);
      }
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Action failed');
    } finally {
      setPendingId(null);
    }
  };

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = newName.trim();
    if (!trimmed || creating) return;
    setActionError(null);
    setCreating(true);
    try {
      const project = await createProject(trimmed);
      // Newly created projects start empty — auto-add this product so
      // the user doesn't have to click a checkbox they just created.
      await addProductTo(project.id, productRef);
      setNewName('');
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Could not create project');
    } finally {
      setCreating(false);
    }
  };

  if (!user) {
    return (
      <div className="add-to-project-section">
        <p className="add-to-project-cta">
          <span className="add-to-project-cta-icon" aria-hidden="true">+</span>
          Sign in to save this to a project.
        </p>
      </div>
    );
  }

  return (
    <div className="add-to-project-section">
      <button
        ref={triggerRef}
        type="button"
        className="add-to-project-trigger"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? popoverId : undefined}
        onClick={() => setOpen(v => !v)}
        onKeyDown={handleTriggerKey}
      >
        <span>Add to project</span>
        <span className="add-to-project-caret" aria-hidden="true">▾</span>
      </button>

      {open && rect &&
        createPortal(
          <div
            ref={popoverRef}
            id={popoverId}
            role="dialog"
            aria-label="Add to project"
            className="add-to-project-popover"
            data-portaled-popover="add-to-project"
            data-placement={rect.placement}
            style={{
              position: 'fixed',
              top: rect.placement === 'above' ? undefined : rect.top,
              bottom: rect.placement === 'above' ? window.innerHeight - rect.top : undefined,
              left: rect.left,
              width: rect.width,
              maxHeight: rect.maxHeight,
            }}
          >
            {loading && projects.length === 0 ? (
              <p className="add-to-project-empty">Loading…</p>
            ) : error ? (
              <p className="add-to-project-error">{error}</p>
            ) : sortedProjects.length === 0 ? (
              <p className="add-to-project-empty">No projects yet — create one below.</p>
            ) : (
              <ul className="add-to-project-list" role="list">
                {sortedProjects.map(p => {
                  const checked = isInProject(p.id, productRef);
                  const busy = pendingId === p.id;
                  return (
                    <li key={p.id} className="add-to-project-item">
                      <label className="add-to-project-row">
                        <input
                          type="checkbox"
                          checked={checked}
                          disabled={busy}
                          onChange={() => handleToggle(p.id)}
                        />
                        <span className="add-to-project-name">{p.name}</span>
                        <span className="add-to-project-count" aria-label={`${p.product_refs.length} products`}>
                          {p.product_refs.length}
                        </span>
                      </label>
                    </li>
                  );
                })}
              </ul>
            )}

            <form noValidate onSubmit={handleCreate} className="add-to-project-create">
              <input
                type="text"
                className="add-to-project-input"
                placeholder="New project name"
                value={newName}
                onChange={e => setNewName(e.target.value)}
                disabled={creating}
                aria-label="New project name"
                maxLength={120}
              />
              <button
                type="submit"
                className="add-to-project-create-btn"
                disabled={creating || !newName.trim()}
              >
                {creating ? 'Creating…' : 'Create'}
              </button>
            </form>

            {actionError && (
              <p className="add-to-project-error" role="alert">
                {actionError}
              </p>
            )}
          </div>,
          document.body,
        )}
    </div>
  );
}
