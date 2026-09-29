import React from 'react';
import { useTheme } from '../../utils/hooks/';
import { themePresets } from '../../utils/settings/themePresets';
import './ThemePicker.scss';

export function ThemePicker() {
  const { currentThemeMode, changeThemeMode } = useTheme();

  return (
    <div className="theme-picker">
      {[false, true].map((classic) => (
      <fieldset className={classic ? 'theme-collection theme-collection-classic' : 'theme-collection'} key={String(classic)}>
        <legend>{classic ? 'The classics' : 'Soft gradients'}<span>{classic ? 'Always in style.' : 'Four ways to set the mood.'}</span></legend>
        <div className="theme-picker-grid">
        {themePresets.filter((theme) => Boolean(theme.classic) === classic).map((theme) => (
          <label className="theme-picker-option" key={theme.id}>
            <input
              type="radio"
              name="appearance-theme"
              value={theme.id}
              aria-label={theme.name}
              checked={currentThemeMode === theme.id}
              onChange={() => changeThemeMode(theme.id)}
            />
            <span className="theme-picker-card">
              <span
                className="theme-picker-preview"
                aria-hidden="true"
                style={{ '--preview-start': theme.colors[0], '--preview-mid': theme.colors[1], '--preview-end': theme.colors[2], '--preview-accent': theme.accent }}
              >
                <span className={`theme-preview-window${theme.dark ? ' theme-preview-dark' : ''}`}>
                  <span className="theme-preview-toolbar"><b /><b /><b /><em /></span>
                  <span className="theme-preview-layout">
                    <span className="theme-preview-sidebar"><b /><b /><b /><b /></span>
                    <span className="theme-preview-content"><b /><span><i /><i /><i /></span><em /></span>
                  </span>
                </span>
              </span>
              <span className="theme-picker-label">
                <span><strong>{theme.name}</strong><small>{theme.description}</small></span>
                <span className="theme-picker-check" aria-hidden="true">{currentThemeMode === theme.id ? '✓' : ''}</span>
              </span>
            </span>
          </label>
        ))}
        </div>
      </fieldset>
      ))}
    </div>
  );
}
