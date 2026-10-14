'use client';

import { Moon, Sun } from 'lucide-react';

export function ThemeToggle({ className = '' }: { className?: string }) {
  function toggleTheme() {
    const currentTheme = document.documentElement.dataset.theme === 'light' ? 'light' : 'dark';
    const nextTheme = currentTheme === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = nextTheme;
    window.localStorage.setItem('relay-theme', nextTheme);
  }

  return (
    <button
      className={`theme-toggle ${className}`}
      type="button"
      onClick={toggleTheme}
      aria-label="Switch theme"
      title="Switch theme"
    >
      <Sun className="theme-icon-dark" size={15} />
      <Moon className="theme-icon-light" size={15} />
    </button>
  );
}
