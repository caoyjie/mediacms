import React from 'react';
import { Page } from './Page';
import { ThemePicker } from '../components/page-layout/ThemePicker';
import { useTheme } from '../utils/hooks/useTheme';
import { themePresets } from '../utils/settings/themePresets';
import './AppearancePage.scss';

export function AppearancePage() {
  const { currentThemeMode, themeModeSwitcher } = useTheme();
  const current = themePresets.find((theme) => theme.id === currentThemeMode);

  return (
    <Page id="appearance">
      <main className="appearance-page">
        <header className="appearance-heading">
          <div>
            <p className="appearance-eyebrow">YOUR SPACE, YOUR COLORS</p>
            <h1>Appearance<span aria-hidden="true">.</span></h1>
            <p className="appearance-intro">A softer backdrop for everything you love to watch.</p>
          </div>
          <div className="appearance-current" role="status" aria-live="polite">
            <span className="appearance-current-dot" aria-hidden="true" />
            {current.name} applied
          </div>
        </header>
        {themeModeSwitcher.enabled ? (
          <>
            <ThemePicker />
            <p className="appearance-note"><i className="material-icons" aria-hidden="true">check_circle_outline</i>Changes apply instantly and are remembered in this browser.</p>
          </>
        ) : <p>Appearance is managed by this site's administrator.</p>}
      </main>
    </Page>
  );
}
