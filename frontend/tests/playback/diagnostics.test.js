import { tracePlayback } from '../../src/static/js/utils/playback/ProgressDiagnostics';

afterEach(() => {
  delete window.__playbackDebug;
  window.history.replaceState({}, '', '/');
});

test('playback diagnostics are disabled on ordinary watch pages', () => {
  window.history.replaceState({}, '', '/view?m=example');
  tracePlayback('lookup-result', { saved: { position: 152 } });
  expect(window.__playbackDebug).toBeUndefined();
});

test('opt-in diagnostics keep a bounded chronological record', () => {
  window.history.replaceState({}, '', '/view?m=example&playbackDebug=1');
  for (let i = 0; i < 105; i++) tracePlayback('event', { index: i });
  expect(window.__playbackDebug).toHaveLength(100);
  expect(window.__playbackDebug[0].index).toBe(5);
  expect(window.__playbackDebug[99]).toEqual({ ms: expect.any(Number), event: 'event', index: 104 });
});
