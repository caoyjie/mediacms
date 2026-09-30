import React from 'react';
import ReactDOM from 'react-dom';
import { act } from 'react-dom/test-utils';
import { MediaProgressBar, MediaProgressDescription, useMediaProgress } from '../../src/static/js/components/list-item/MediaProgress';
import { getProgressService } from '../../src/static/js/utils/playback/ProgressService';

let container;
const media = { token: 'card-video', revision: 'revision' };
const flush = async () => { await act(async () => { await Promise.resolve(); }); };
function Card({ revision = 'revision' }) {
  const progress = useMediaProgress({ mediaId: 'card-video', contentRevision: revision });
  return <div><a aria-hidden="true"><MediaProgressBar progress={progress} /></a><MediaProgressDescription progress={progress} /></div>;
}
beforeEach(() => {
  window.MediaCMS = { site: { id: 'cards' }, user: { is: { anonymous: true } } };
  localStorage.clear();
  container = document.createElement('div');
  document.body.appendChild(container);
});
afterEach(() => { act(() => { ReactDOM.unmountComponentAtNode(container); }); container.remove(); });

test('unplayed cards have no bar, saved cards update and completed cards show a full bar', async () => {
  act(() => ReactDOM.render(<Card />, container));
  await flush();
  expect(container.querySelector('.media-progress-bar')).toBeNull();
  const writer = getProgressService().writer(media);
  await act(async () => { await writer.start(); await writer.save({ position: 480, duration: 1200 }); });
  expect(container.querySelector('.media-progress-bar').style.width).toBe('40%');
  const description = container.querySelector('.media-progress-description');
  expect(description.textContent).toContain('40%');
  expect(description.closest('[aria-hidden="true"]')).toBeNull();
  await act(async () => { await writer.save({ position: 1199, duration: 1200, ended: true }); });
  expect(container.querySelector('.media-progress-bar').style.width).toBe('100%');
  expect(description.textContent).toContain('Completed');
  act(() => ReactDOM.render(<Card revision="replaced" />, container));
  await flush();
  expect(container.querySelector('.media-progress-bar')).toBeNull();
});
