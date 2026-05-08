/**
 * Tests for ProjectsPage — empty state, list rendering, delete flow,
 * logged-out CTA.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { ReactNode } from 'react';
import { MemoryRouter } from 'react-router-dom';
import ProjectsPage from './ProjectsPage';
import type { Project } from '../types/projects';

const mockListProjects = vi.fn();
const mockCreateProject = vi.fn();
const mockAddProductToProject = vi.fn();
const mockRemoveProductFromProject = vi.fn();
const mockDeleteProject = vi.fn();

vi.mock('../api/client', () => ({
  apiClient: {
    setAuthToken: vi.fn(),
    listProjects: (...args: unknown[]) => mockListProjects(...args),
    createProject: (...args: unknown[]) => mockCreateProject(...args),
    addProductToProject: (...args: unknown[]) => mockAddProductToProject(...args),
    removeProductFromProject: (...args: unknown[]) => mockRemoveProductFromProject(...args),
    deleteProject: (...args: unknown[]) => mockDeleteProject(...args),
  },
}));

const mockUseAuth = vi.fn();
vi.mock('../context/AuthContext', () => ({
  useAuth: () => mockUseAuth(),
}));

import { ProjectsProvider } from '../context/ProjectsContext';
import { ConfirmProvider } from './ui/ConfirmDialog';

function project(over: Partial<Project> = {}): Project {
  const now = new Date().toISOString();
  return {
    id: 'p1',
    name: 'Cell A',
    owner_sub: 'sub-1',
    product_refs: [],
    created_at: now,
    updated_at: now,
    ...over,
  };
}

const wrap = (ui: ReactNode) =>
  render(
    <MemoryRouter>
      <ProjectsProvider>
        <ConfirmProvider>{ui}</ConfirmProvider>
      </ProjectsProvider>
    </MemoryRouter>,
  );

beforeEach(() => {
  vi.clearAllMocks();
  mockListProjects.mockResolvedValue([]);
});

describe('logged out', () => {
  it('shows the sign-in CTA, no list', () => {
    mockUseAuth.mockReturnValue({ user: null });
    wrap(<ProjectsPage />);
    expect(screen.getByText(/sign in to view/i)).toBeDefined();
    expect(mockListProjects).not.toHaveBeenCalled();
  });
});

describe('logged in', () => {
  beforeEach(() => {
    mockUseAuth.mockReturnValue({ user: { sub: 'sub-1', email: 'a@b.co', groups: [] } });
  });

  it('shows the empty state when the user has no projects', async () => {
    mockListProjects.mockResolvedValueOnce([]);
    await act(async () => {
      wrap(<ProjectsPage />);
    });
    expect(await screen.findByText(/no projects yet/i)).toBeDefined();
  });

  it('renders project cards sorted by updated_at desc', async () => {
    mockListProjects.mockResolvedValueOnce([
      project({ id: 'p1', name: 'Older', updated_at: '2026-01-01T00:00:00Z' }),
      project({ id: 'p2', name: 'Newer', updated_at: '2026-04-15T00:00:00Z' }),
    ]);

    await act(async () => {
      wrap(<ProjectsPage />);
    });

    const headings = await screen.findAllByRole('heading', { level: 3 });
    expect(headings.map(h => h.textContent)).toEqual(['Newer', 'Older']);
  });

  it('renders product count text per card', async () => {
    mockListProjects.mockResolvedValueOnce([
      project({
        id: 'p1',
        name: 'A',
        product_refs: [
          { product_type: 'motor', product_id: 'm-1' },
          { product_type: 'drive', product_id: 'd-1' },
        ],
      }),
      project({ id: 'p2', name: 'Empty', product_refs: [] }),
    ]);

    await act(async () => {
      wrap(<ProjectsPage />);
    });

    await screen.findByText('A');
    expect(screen.getByText('2 products')).toBeDefined();
    expect(screen.getByText('0 products')).toBeDefined();
  });

  it('delete confirms then calls the API; card disappears on success', async () => {
    mockListProjects.mockResolvedValueOnce([project({ id: 'p1', name: 'Doomed' })]);
    mockDeleteProject.mockResolvedValueOnce(undefined);

    await act(async () => {
      wrap(<ProjectsPage />);
    });

    await screen.findByText('Doomed');
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /delete/i }));
    });

    // ConfirmDialog opens — click its Delete button to confirm.
    const dialog = await screen.findByRole('dialog');
    await act(async () => {
      const confirmBtn = Array.from(dialog.querySelectorAll('button')).find(
        b => b.textContent === 'Delete',
      );
      fireEvent.click(confirmBtn!);
    });

    expect(mockDeleteProject).toHaveBeenCalledWith('p1');
    await waitFor(() => expect(screen.queryByText('Doomed')).toBeNull());
  });

  it('delete is a no-op when the user cancels confirm', async () => {
    mockListProjects.mockResolvedValueOnce([project({ id: 'p1', name: 'Safe' })]);

    await act(async () => {
      wrap(<ProjectsPage />);
    });

    await screen.findByText('Safe');
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /delete/i }));
    });

    // ConfirmDialog opens — click Cancel.
    const dialog = await screen.findByRole('dialog');
    await act(async () => {
      const cancelBtn = Array.from(dialog.querySelectorAll('button')).find(
        b => b.textContent === 'Cancel',
      );
      fireEvent.click(cancelBtn!);
    });

    expect(mockDeleteProject).not.toHaveBeenCalled();
    expect(screen.getByText('Safe')).toBeDefined();
  });

  it('surfaces an error when delete fails', async () => {
    mockListProjects.mockResolvedValueOnce([project({ id: 'p1', name: 'Cursed' })]);
    mockDeleteProject.mockRejectedValueOnce(new Error('Network down'));

    await act(async () => {
      wrap(<ProjectsPage />);
    });

    await screen.findByText('Cursed');
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /delete/i }));
    });

    const dialog = await screen.findByRole('dialog');
    await act(async () => {
      const confirmBtn = Array.from(dialog.querySelectorAll('button')).find(
        b => b.textContent === 'Delete',
      );
      fireEvent.click(confirmBtn!);
    });

    await waitFor(() => expect(screen.getByText(/network down/i)).toBeDefined());
    expect(screen.getByText('Cursed')).toBeDefined();
  });
});
