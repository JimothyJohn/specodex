/**
 * Tests for ThemeToggle component
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import ThemeToggle from './ThemeToggle';

describe('ThemeToggle', () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.removeAttribute('data-theme');
  });

  it('renders a button', () => {
    render(<ThemeToggle />);
    expect(screen.getByRole('button')).toBeDefined();
  });

  it('defaults to dark mode', () => {
    render(<ThemeToggle />);
    const button = screen.getByRole('button');
    expect(button.getAttribute('aria-label')).toBe('Switch to light mode');
  });

  it('toggles to light mode on click', () => {
    render(<ThemeToggle />);
    const button = screen.getByRole('button');

    fireEvent.click(button);

    expect(button.getAttribute('aria-label')).toBe('Switch to dark mode');
    expect(localStorage.getItem('theme')).toBe('light');
    expect(document.documentElement.getAttribute('data-theme')).toBe('light');
  });

  it('toggles back to dark mode on second click', () => {
    render(<ThemeToggle />);
    const button = screen.getByRole('button');

    fireEvent.click(button); // to light
    fireEvent.click(button); // back to dark

    expect(button.getAttribute('aria-label')).toBe('Switch to light mode');
    expect(localStorage.getItem('theme')).toBe('dark');
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark');
  });

  it('restores saved theme from localStorage', () => {
    localStorage.setItem('theme', 'light');
    render(<ThemeToggle />);
    const button = screen.getByRole('button');
    expect(button.getAttribute('aria-label')).toBe('Switch to dark mode');
  });

  it('exposes the tooltip label via aria-label (themed Tooltip replaces native title)', () => {
    // Native `title=` was migrated to the app-native Tooltip component
    // (STYLE.md Phase 1). The accessible label still lives on the button
    // via aria-label so screen readers and the tooltip content stay in
    // sync.
    render(<ThemeToggle />);
    const button = screen.getByRole('button');
    expect(button.getAttribute('aria-label')).toBe('Switch to light mode');
    expect(button.getAttribute('title')).toBeNull();
  });
});
