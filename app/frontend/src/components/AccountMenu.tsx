/**
 * AccountMenu — header dropdown for auth state.
 *
 * Logged out: a single "Sign in" button that opens the AuthModal.
 * Logged in:  a dropdown with the user's email + Logout (and an
 *             "ADMIN" pill if the Cognito group is set).
 *
 * Sits in the header's options strip alongside the unit/density/theme
 * toggles. Stays compact — auth is plumbing, not the headline.
 */

import { useEffect, useRef, useState } from 'react';
import { useAuth } from '../context/AuthContext';
import AuthModal from './AuthModal';
import Tooltip from './ui/Tooltip';

export default function AccountMenu() {
  const { user, isAdmin, logout } = useAuth();
  const [modalOpen, setModalOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!menuOpen) return;
    const onClick = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(false);
      }
    };
    const onEsc = (e: KeyboardEvent) => { if (e.key === 'Escape') setMenuOpen(false); };
    document.addEventListener('mousedown', onClick);
    document.addEventListener('keydown', onEsc);
    return () => {
      document.removeEventListener('mousedown', onClick);
      document.removeEventListener('keydown', onEsc);
    };
  }, [menuOpen]);

  if (!user) {
    return (
      <>
        <button
          type="button"
          className="account-menu-signin"
          onClick={() => setModalOpen(true)}
        >
          Sign in
        </button>
        <AuthModal open={modalOpen} onClose={() => setModalOpen(false)} />
      </>
    );
  }

  return (
    <div className="account-menu" ref={menuRef}>
      <button
        type="button"
        className="account-menu-trigger"
        aria-haspopup="menu"
        aria-expanded={menuOpen}
        onClick={() => setMenuOpen(o => !o)}
      >
        <Tooltip content={user.email || 'Account'}>
          <span className="account-menu-email">{user.email || 'Account'}</span>
        </Tooltip>
        {isAdmin && <span className="account-menu-admin-pill">ADMIN</span>}
      </button>
      {menuOpen && (
        <div className="account-menu-dropdown" role="menu">
          <div className="account-menu-dropdown-header">{user.email}</div>
          <button
            type="button"
            role="menuitem"
            className="account-menu-dropdown-item"
            onClick={() => { setMenuOpen(false); logout(); }}
          >
            Sign out
          </button>
        </div>
      )}
    </div>
  );
}
