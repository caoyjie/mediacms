import React, { useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import { translateString } from '../utils/helpers/translate';
import './PlaylistCapacityDialog.scss';

export interface PlaylistCapacityError {
  code: string;
  limit: number;
  playlists: Array<{id: number; title: string; current_count: number; requested_new_count: number; remaining_capacity: number}>;
}

export function PlaylistCapacityDialog({ error, onClose }: {error: PlaylistCapacityError | null; onClose: () => void}) {
  const buttonRef = useRef<HTMLButtonElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  const isOpen = !!error;
  useEffect(() => {
    if (!isOpen) return;
    const previous = document.activeElement as HTMLElement | null;
    buttonRef.current?.focus();
    const keydown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' || event.key === 'Tab') {
        event.preventDefault();
        event.stopImmediatePropagation();
        if (event.key === 'Escape') closeRef.current();
        else buttonRef.current?.focus();
      }
    };
    const focus = (event: FocusEvent) => {
      if (event.target !== buttonRef.current) buttonRef.current?.focus();
    };
    document.addEventListener('keydown', keydown, true);
    document.addEventListener('focusin', focus);
    return () => {
      document.removeEventListener('keydown', keydown, true);
      document.removeEventListener('focusin', focus);
      if (previous?.isConnected) previous.focus();
    };
  }, [isOpen]);
  if (!error) return null;
  const count = (template: string, value: number) => translateString(template).replace('{count}', String(value));
  return createPortal(
    <div className="playlist-capacity-overlay" onClick={event => event.stopPropagation()} onMouseDown={event => event.stopPropagation()}>
      <div className="playlist-capacity-dialog" role="alertdialog" aria-modal="true" aria-labelledby="playlist-capacity-title" aria-describedby="playlist-capacity-description">
        <h2 id="playlist-capacity-title">{translateString('Playlist capacity exceeded')}</h2>
        <p id="playlist-capacity-description">{translateString('Nothing was added. Select fewer items or remove existing items, then try again.')}</p>
        <ul>{error.playlists.map(p => <li key={p.id}>
          <strong>{p.title}</strong>
          <p>{count('Limit: {count}', error.limit)} · {count('Current: {count}', p.current_count)} · {count('New: {count}', p.requested_new_count)} · {count('Remaining: {count}', p.remaining_capacity)}</p>
        </li>)}</ul>
        <button type="button" ref={buttonRef} onClick={onClose}>{translateString('Back to selection')}</button>
      </div>
    </div>, document.body
  );
}
