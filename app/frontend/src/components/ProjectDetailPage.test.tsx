/**
 * Tests for ProjectDetailPage — drill-in view: rename, delete, remove
 * product, missing-product placeholder, logged-out CTA.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { ReactNode } from 'react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import ProjectDetailPage from './ProjectDetailPage';
import type { Project } from '../types/projects';
import type { Motor } from '../types/models';

const mockListProjects = vi.fn();
const mockCreateProject = vi.fn();
const mockAddProductToProject = vi.fn();
const mockRemoveProductFromProject = vi.fn();
const mockDeleteProject = vi.fn();
const mockRenameProject = vi.fn();
const mockGetProduct = vi.fn();

vi.mock('../api/client', () => ({
  apiClient: {
    setAuthToken: vi.fn(),
    listProjects: (...args: unknown[]) => mockListProjects(...args),
    createProject: (...args: unknown[]) => mockCreateProject(...args),
    addProductToProject: (...args: unknown[]) => mockAddProductToProject(...args),
    removeProductFromProject: (...args: unknown[]) => mockRemoveProductFromProject(...args),
    deleteProject: (...args: unknown[]) => mockDeleteProject(...args),
    renameProject: (...args: unknown[]) => mockRenameProject(...args),
    getProduct: (...args: unknown[]) => mockGetProduct(...args),
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

function motor(over: Partial<Motor> = {}): Motor {
  return {
    PK: 'PRODUCT#MOTOR',
    SK: 'PRODUCT#m-1',
    product_id: 'm-1',
    product_type: 'motor',
    manufacturer: 'Acme',
    part_number: 'AC-100',
    product_name: 'AC-100',
    ...over,
  } as Motor;
}

const wrap = (ui: ReactNode, projectId = 'p1') =>
  render(
    <MemoryRouter initialEntries={[`/projects/${projectId}`]}>
      <ProjectsProvider>
        <ConfirmProvider>
        <Routes>
          <Route path="/projects/:id" element={ui} />
          <Route path="/projects" element={<div>Projects index</div>} />
        </Routes>
        </ConfirmProvider>
      </ProjectsProvider>
    </MemoryRouter>,
  );

beforeEach(() => {
  vi.clearAllMocks();
  mockListProjects.mockResolvedValue([]);
});

describe('logged out', () => {
  it('shows the sign-in CTA', () => {
    mockUseAuth.mockReturnValue({ user: null });
    wrap(<ProjectDetailPage />);
    expect(screen.getByText(/sign in to view this project/i)).toBeDefined();
  });
});

describe('logged in', () => {
  beforeEach(() => {
    mockUseAuth.mockReturnValue({ user: { sub: 'sub-1', email: 'a@b.co', groups: [] } });
  });

  it('shows "not found" when the project is not in context', async () => {
    mockListProjects.mockResolvedValueOnce([]);
    await act(async () => {
      wrap(<ProjectDetailPage />);
    });
    await waitFor(() => expect(screen.getByText(/project not found/i)).toBeDefined());
  });

  it('renders the empty state when the project has no products', async () => {
    mockListProjects.mockResolvedValueOnce([project({ id: 'p1', name: 'Empty' })]);
    await act(async () => {
      wrap(<ProjectDetailPage />);
    });
    await screen.findByRole('heading', { name: 'Empty' });
    expect(screen.getByText(/no products in this project yet/i)).toBeDefined();
  });

  it('renders products fetched per ref', async () => {
    mockListProjects.mockResolvedValueOnce([
      project({
        id: 'p1',
        name: 'Robot',
        product_refs: [{ product_type: 'motor', product_id: 'm-1' }],
      }),
    ]);
    mockGetProduct.mockResolvedValueOnce(motor());

    await act(async () => {
      wrap(<ProjectDetailPage />);
    });

    await screen.findByRole('heading', { name: 'Robot' });
    await waitFor(() => expect(screen.getByText('Acme')).toBeDefined());
    expect(screen.getByText('AC-100')).toBeDefined();
    expect(mockGetProduct).toHaveBeenCalledWith('m-1', 'motor');
  });

  it('shows "Removed product" placeholder for a 404 ref', async () => {
    mockListProjects.mockResolvedValueOnce([
      project({
        id: 'p1',
        name: 'With Stale Ref',
        product_refs: [{ product_type: 'motor', product_id: 'gone' }],
      }),
    ]);
    mockGetProduct.mockRejectedValueOnce(new Error('Request failed with status 404'));

    await act(async () => {
      wrap(<ProjectDetailPage />);
    });

    await waitFor(() => expect(screen.getByText(/removed product/i)).toBeDefined());
  });

  it('rename: typing + submit calls renameProject', async () => {
    mockListProjects.mockResolvedValueOnce([project({ id: 'p1', name: 'Old' })]);
    mockRenameProject.mockResolvedValueOnce(project({ id: 'p1', name: 'New' }));

    await act(async () => {
      wrap(<ProjectDetailPage />);
    });

    await screen.findByRole('heading', { name: 'Old' });
    fireEvent.click(screen.getByRole('button', { name: /rename/i }));

    const input = screen.getByLabelText(/project name/i);
    fireEvent.change(input, { target: { value: 'New' } });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /^save$/i }));
    });

    expect(mockRenameProject).toHaveBeenCalledWith('p1', 'New');
  });

  it('rename: empty trimmed name does not call API', async () => {
    mockListProjects.mockResolvedValueOnce([project({ id: 'p1', name: 'Old' })]);
    await act(async () => {
      wrap(<ProjectDetailPage />);
    });
    await screen.findByRole('heading', { name: 'Old' });
    fireEvent.click(screen.getByRole('button', { name: /rename/i }));
    fireEvent.change(screen.getByLabelText(/project name/i), { target: { value: '   ' } });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /^save$/i }));
    });
    expect(mockRenameProject).not.toHaveBeenCalled();
  });

  it('remove product calls API and removes the row', async () => {
    mockListProjects.mockResolvedValueOnce([
      project({
        id: 'p1',
        name: 'P',
        product_refs: [{ product_type: 'motor', product_id: 'm-1' }],
      }),
    ]);
    mockGetProduct.mockResolvedValueOnce(motor());
    mockRemoveProductFromProject.mockResolvedValueOnce(
      project({ id: 'p1', name: 'P', product_refs: [] }),
    );

    await act(async () => {
      wrap(<ProjectDetailPage />);
    });

    await waitFor(() => expect(screen.getByText('Acme')).toBeDefined());
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /remove/i }));
    });

    expect(mockRemoveProductFromProject).toHaveBeenCalledWith('p1', {
      product_type: 'motor',
      product_id: 'm-1',
    });
    await waitFor(() => expect(screen.queryByText('Acme')).toBeNull());
  });

  it('delete project navigates back to /projects on success', async () => {
    mockListProjects.mockResolvedValueOnce([project({ id: 'p1', name: 'Doomed' })]);
    mockDeleteProject.mockResolvedValueOnce(undefined);

    await act(async () => {
      wrap(<ProjectDetailPage />);
    });

    await screen.findByRole('heading', { name: 'Doomed' });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /delete project/i }));
    });

    // ConfirmDialog opens — click Delete to confirm.
    const dialog = await screen.findByRole('dialog');
    await act(async () => {
      const confirmBtn = Array.from(dialog.querySelectorAll('button')).find(
        b => b.textContent === 'Delete',
      );
      fireEvent.click(confirmBtn!);
    });

    expect(mockDeleteProject).toHaveBeenCalledWith('p1');
    await waitFor(() => expect(screen.getByText('Projects index')).toBeDefined());
  });
});
