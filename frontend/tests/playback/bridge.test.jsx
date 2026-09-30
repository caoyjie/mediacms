import React from 'react';
import ReactDOM from 'react-dom';
import { act } from 'react-dom/test-utils';
import VideoJSEmbed from '../../src/static/js/components/VideoJS/VideoJSEmbed';

const data = { media_type: 'audio', url: 'http://localhost/view?m=bridge-audio', content_revision: 'revision' };
test('metadata re-renders keep saving through the already mounted player', async () => {
  window.history.replaceState({}, '', '/view?m=bridge-audio');
  window.MediaCMS = { site: { id: 'bridge' }, user: { is: { anonymous: true } } };
  localStorage.clear();
  const container = document.createElement('div');
  document.body.appendChild(container);
  const events = {};
  const player = {
    time: 0, duration: () => 30, readyState: () => 4, paused: () => true, seeking: () => false,
    seekable: () => ({ length: 1, start: () => 0, end: () => 30 }),
    currentTime(value) { if (value !== undefined) this.time = value; return this.time; },
    on(name, fn) { (events[name] ||= new Set()).add(fn); },
    off(name, fn) { events[name]?.delete(fn); },
    emit(name) { [...(events[name] || [])].forEach(fn => fn()); },
  };
  act(() => { ReactDOM.render(<VideoJSEmbed data={data} inEmbed={false} />, container); });
  await act(async () => {
    window.MEDIA_DATA.playbackProgress.attach(player, null, () => player.emit('playing'));
    for (let i = 0; i < 10; i++) await Promise.resolve();
  });
  act(() => { ReactDOM.render(<VideoJSEmbed data={{ ...data, views: 2 }} inEmbed={false} />, container); });
  await act(async () => {
    player.time = 9;
    player.emit('timeupdate');
    player.emit('pause');
    await Promise.resolve();
  });
  const saved = JSON.parse(localStorage.getItem('MediaCMS[bridge][guest-playback-v1][bridge-audio]'));
  // Unmount even on a failed assertion so the interval never leaks between tests.
  act(() => { ReactDOM.unmountComponentAtNode(container); });
  container.remove();
  expect(saved?.position).toBe(9);
});
