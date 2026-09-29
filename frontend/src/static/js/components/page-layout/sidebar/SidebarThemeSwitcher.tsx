import React from 'react';
import { useTheme } from '../../../utils/hooks/';
import { AppearanceLink } from '../AppearanceLink';

export const SidebarThemeSwitcher: React.FC = () => {
  const { themeModeSwitcher } = useTheme();
  return themeModeSwitcher.enabled && themeModeSwitcher.position === 'sidebar' ? (
    <div className="sidebar-theme-switcher appearance-navigation"><AppearanceLink /></div>
  ) : null;
};
