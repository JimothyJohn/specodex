/**
 * ErrorBoundary — Phase 7 of FRONTEND_TESTING.md.
 *
 * Right now a render-time crash anywhere inside the app shows React's
 * white page in dev (or whatever the boundary's fallback is in prod).
 * These tests confirm the boundary actually catches, and that the
 * "Try Again" recovery returns the children when given a fresh,
 * non-throwing child.
 *
 * componentDidCatch logs to console.error in production code; the test
 * silences that to keep the test output clean.
 */

import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import type { JSX } from 'react';
import ErrorBoundary from './ErrorBoundary';

function Boom({ message = 'kapow' }: { message?: string }): JSX.Element {
  throw new Error(message);
}

const consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});

afterEach(() => {
  consoleErrorSpy.mockClear();
});

describe('ErrorBoundary', () => {
  it('renders the child when no error is thrown', () => {
    render(
      <ErrorBoundary>
        <p>healthy child</p>
      </ErrorBoundary>,
    );
    expect(screen.getByText('healthy child')).toBeInTheDocument();
    expect(screen.queryByText(/something went wrong/i)).toBeNull();
  });

  it('renders the default fallback with the error message when a child throws', () => {
    render(
      <ErrorBoundary>
        <Boom message="kapow" />
      </ErrorBoundary>,
    );
    expect(screen.getByText(/something went wrong/i)).toBeInTheDocument();
    expect(screen.getByText('kapow')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /try again/i })).toBeInTheDocument();
  });

  it('renders a custom fallback when one is provided', () => {
    render(
      <ErrorBoundary fallback={<div role="alert">custom fallback</div>}>
        <Boom />
      </ErrorBoundary>,
    );
    expect(screen.getByRole('alert')).toHaveTextContent('custom fallback');
    expect(screen.queryByText(/something went wrong/i)).toBeNull();
    expect(screen.queryByRole('button', { name: /try again/i })).toBeNull();
  });

  it('Try Again clears the error and re-renders the children', () => {
    // Toggle which child the boundary holds. First render: throws.
    // After clicking Try Again, the boundary clears its hasError state;
    // we then re-render with a healthy child to simulate the user
    // having fixed the upstream issue.
    const { rerender } = render(
      <ErrorBoundary>
        <Boom />
      </ErrorBoundary>,
    );
    expect(screen.getByText(/something went wrong/i)).toBeInTheDocument();

    rerender(
      <ErrorBoundary>
        <p>recovered child</p>
      </ErrorBoundary>,
    );
    fireEvent.click(screen.getByRole('button', { name: /try again/i }));
    expect(screen.getByText('recovered child')).toBeInTheDocument();
    expect(screen.queryByText(/something went wrong/i)).toBeNull();
  });
});
