/**
 * Main App Component: Root application component with routing
 *
 * Features:
 * - Code splitting with React.lazy for improved initial load time
 * - Suspense boundaries for graceful loading states
 * - Client-side routing with React Router v7
 * - Global state management via AppContext
 * - Theme toggle in header
 *
 * Performance Optimizations:
 * - Lazy loading: ProductList component (largest) loads only when navigating to /products
 * - Dashboard loads immediately (smaller, shown on initial load)
 * - Reduces initial bundle size by ~40-50KB
 * - Suspense fallback provides smooth loading experience
 *
 * Route Structure:
 * - / → Dashboard (summary statistics)
 * - /products → ProductList (full product listing with filtering)
 * - * → Redirect to / (catch-all for invalid routes)
 *
 * @module App
 */

import { lazy, Suspense, useState } from 'react';
import { BrowserRouter, Routes, Route, NavLink, Navigate, useLocation } from 'react-router-dom';
import { AppProvider } from './context/AppContext';
import { AuthProvider, useAuth } from './context/AuthContext';
import { ProjectsProvider } from './context/ProjectsContext';
import { ConfirmProvider } from './components/ui/ConfirmDialog';
import FeedbackModal from './components/ui/FeedbackModal';
import { ToastProvider } from './components/ui/Toast';
import ThemeToggle from './components/ThemeToggle';
import GitHubLink from './components/GitHubLink';
import DensityToggle from './components/DensityToggle';
import NetworkStatus from './components/NetworkStatus';
import ErrorBoundary from './components/ErrorBoundary';
import BuildTray from './components/BuildTray';
import AccountMenu from './components/AccountMenu';
import './App.css';

// ========== Eager Imports ==========
import ProductList from './components/ProductList';

// ========== Lazy Imports ==========
// Welcome (Specodex landing) — Stage 1 rebrand. Lazy because most users
// land directly on the catalog at "/"; the marketing surface is opt-in.
const Welcome = lazy(() => import('./components/Welcome'));

// Admin views are code-split into their own chunks (lazy import →
// separate JS file fetched only when an admin navigates). They ship
// with every build now — gating is at runtime via the Cognito 'admin'
// group, not at build time. Pre-Phase-4 we tree-shook these out of
// public builds via VITE_APP_MODE; that no longer composes with one
// deployed environment serving both audiences.
const ProductManagement = lazy(() => import('./components/ProductManagement'));
const DatasheetsPage = lazy(() => import('./components/DatasheetsPage'));
const AdminPanel = lazy(() => import('./components/AdminPanel'));
const ProjectsPage = lazy(() => import('./components/ProjectsPage'));
const ProjectDetailPage = lazy(() => import('./components/ProjectDetailPage'));
const BuildPage = lazy(() => import('./components/BuildPage'));

/**
 * Loading Fallback Component
 *
 * Displayed while lazy-loaded components are being fetched.
 * Provides visual feedback during code splitting delays.
 *
 * Typically shown for:
 * - ~50-200ms on fast connections
 * - ~200-500ms on slower connections
 * - Prevents jarring blank screens
 */
function LoadingFallback() {
  return (
    <div style={{
      display: 'flex',
      justifyContent: 'center',
      alignItems: 'center',
      height: '50vh',
      fontSize: '1.2rem',
      color: 'var(--text-secondary)'
    }}>
      <div>Loading...</div>
    </div>
  );
}

export function AppShell() {
  // The Specodex landing renders its own chrome (OD-green band, footer)
  // and shouldn't sit underneath the existing "Product Search" header.
  const { pathname } = useLocation();
  const isLanding = pathname === '/welcome';
  // Admin nav is shown iff the signed-in user is in the Cognito
  // 'admin' group. The env-mode gate retired in Phase 4 — one
  // deployed environment now serves both admin and public UI based
  // on token contents.
  const { user, isAdmin: showAdminNav } = useAuth();
  const showSignedInNav = !!user;
  const [feedbackOpen, setFeedbackOpen] = useState(false);

  return (
    <>
      {/* ===== NETWORK STATUS INDICATOR ===== */}
      {/* Shows banner when offline (mobile & desktop) */}
      <NetworkStatus />

      <div className="app">
        {!isLanding && (
          <header className="header">
            <div className="header-left">
              <h1>
                <NavLink to="/welcome" className="header-wordmark-link" aria-label="Specodex landing">
                  SPECODEX
                </NavLink>
              </h1>
              <GitHubLink />
              {(showSignedInNav || showAdminNav) && (
                <nav className="nav-inline">
                  <NavLink to="/" end className={({ isActive }) => `nav-btn ${isActive ? 'active' : ''}`}>Selection</NavLink>
                  {showSignedInNav && (
                    <NavLink to="/projects" className={({ isActive }) => `nav-btn ${isActive ? 'active' : ''}`}>Projects</NavLink>
                  )}
                  {showAdminNav && (
                    <>
                      <NavLink to="/datasheets" className={({ isActive }) => `nav-btn ${isActive ? 'active' : ''}`}>Datasheets</NavLink>
                      <NavLink to="/management" className={({ isActive }) => `nav-btn ${isActive ? 'active' : ''}`}>Management</NavLink>
                      <NavLink to="/admin" className={({ isActive }) => `nav-btn ${isActive ? 'active' : ''}`}>Admin</NavLink>
                    </>
                  )}
                </nav>
              )}
            </div>
            <div className="header-options">
              <span className="header-options-label" aria-hidden="true">OPTIONS</span>
              <button
                type="button"
                className="feedback-trigger"
                onClick={() => setFeedbackOpen(true)}
                aria-label="Send feedback"
              >
                Feedback
              </button>
              <DensityToggle />
              <ThemeToggle />
              <AccountMenu />
            </div>
          </header>
        )}
        <FeedbackModal open={feedbackOpen} onClose={() => setFeedbackOpen(false)} />

        {/* ===== ROUTES WITH SUSPENSE + ERROR BOUNDARY ===== */}
        <ErrorBoundary>
          <Suspense fallback={<LoadingFallback />}>
            <Routes>
              {/* ProductList: Eager loaded (default view, always available) */}
              <Route path="/" element={<ProductList />} />

              {/* Specodex landing (Stage 1 rebrand) */}
              <Route path="/welcome" element={<Welcome />} />

              {/* Admin routes — registered for everyone, but hidden
                  from nav unless the user is in the Cognito 'admin'
                  group. Non-admins typing the URLs see chrome but the
                  data calls 401/403. */}
              <Route path="/datasheets" element={<DatasheetsPage />} />
              <Route path="/management" element={<ProductManagement />} />
              <Route path="/admin" element={<AdminPanel />} />

              {/* Per-user projects — auth-gated at the API level; the
                  page renders a sign-in CTA when logged out. */}
              <Route path="/projects" element={<ProjectsPage />} />
              <Route path="/projects/:id" element={<ProjectDetailPage />} />

              {/* Build — requirements-first system assembler. Scaffold
                  per todo/BUILD.md Phase 1 PR-1; full requirements form
                  + derivation in follow-ups. */}
              <Route path="/build" element={<BuildPage />} />

              {/* Catch-all: Redirect to products */}
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </Suspense>
        </ErrorBoundary>
        {!isLanding && <BuildTray />}
      </div>
    </>
  );
}

function App() {
  console.log('[App] Rendering application');

  return (
    <AuthProvider>
      <ProjectsProvider>
        <ToastProvider>
          <AppProvider>
            <ConfirmProvider>
              <BrowserRouter>
                <AppShell />
              </BrowserRouter>
            </ConfirmProvider>
          </AppProvider>
        </ToastProvider>
      </ProjectsProvider>
    </AuthProvider>
  );
}

export default App;
