import React, { useContext } from 'react';
import { LinksContext } from '../../utils/contexts/LinksContext';
import './AppearanceLink.scss';

export function AppearanceLink() {
  const links = useContext(LinksContext);
  return (
    <a className="appearance-link" href={links.appearance}>
      <i className="material-icons" aria-hidden="true">palette</i>
      <span>Appearance</span>
      <i className="material-icons appearance-link-arrow" aria-hidden="true">chevron_right</i>
    </a>
  );
}
