import { createProgressAdapter } from '../../src/static/js/utils/playback/ProgressAdapter';

const data = { media_type: 'video', url: 'http://localhost/view?m=video', content_revision: 'revision' };
test('only standalone watch-page video and audio get a progress adapter', () => {
  expect(createProgressAdapter({ data, inEmbed: false, isWatchPage: true })).not.toBeNull();
  expect(createProgressAdapter({ data: { ...data, media_type: 'audio' }, inEmbed: false, isWatchPage: true })).not.toBeNull();
  expect(createProgressAdapter({ data, inEmbed: true, isWatchPage: true })).toBeNull();
  expect(createProgressAdapter({ data, inEmbed: false, isWatchPage: false })).toBeNull();
  expect(createProgressAdapter({ data: { ...data, media_type: 'image' }, inEmbed: false, isWatchPage: true })).toBeNull();
});
