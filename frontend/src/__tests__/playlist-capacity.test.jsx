import React from 'react';
import ReactDOM from 'react-dom';
import { act } from 'react-dom/test-utils';
import { PlaylistCapacityDialog } from '../static/js/components/PlaylistCapacityDialog';
import { parsePlaylistCapacityError, addWithCapacityCheck } from '../static/js/utils/helpers/playlistCapacity';
jest.mock('../static/js/components/PlaylistCapacityDialog.scss', () => ({}), { virtual: true });

const error = { code: 'playlist_capacity_exceeded', limit: 2, playlists: [
  { id: 1, title: '<b>Course</b>', current_count: 1, requested_new_count: 2, remaining_capacity: 1 },
  { id: 2, title: 'Other', current_count: 2, requested_new_count: 1, remaining_capacity: 0 },
] };
let root;
beforeEach(() => { root = document.createElement('div'); document.body.appendChild(root); });
afterEach(() => { act(() => { ReactDOM.unmountComponentAtNode(root); }); root.remove(); });

test('validates error payload and rejects malformed numeric fields', () => {
  expect(parsePlaylistCapacityError(error)).toEqual(error);
  for (const value of [null, {}, {...error, limit: '2'}, {...error, playlists: []},
    {...error, playlists: [{...error.playlists[0], current_count: -1}]}]) {
    expect(parsePlaylistCapacityError(value)).toBeNull();
  }
});

test('dialog lists capacity details safely and traps/restores focus', () => {
  const trigger = document.createElement('button'); root.appendChild(trigger); trigger.focus();
  const close = jest.fn();
  act(() => { ReactDOM.render(<PlaylistCapacityDialog error={error} onClose={close} />, root); });
  const dialog = document.querySelector('[role="alertdialog"]');
  expect(dialog.textContent).toContain('<b>Course</b>');
  expect(dialog.querySelector('b')).toBeNull();
  expect(dialog.textContent).toContain('Other');
  expect(dialog.textContent).toContain('Limit: 2');
  expect(dialog.textContent).toContain('Current: 1');
  expect(dialog.textContent).toContain('New: 2');
  expect(dialog.textContent).toContain('Remaining: 1');
  expect(document.activeElement).toBe(dialog.querySelector('button'));
  act(() => { document.activeElement.dispatchEvent(new KeyboardEvent('keydown', {key: 'Tab', bubbles: true, cancelable: true})); });
  expect(document.activeElement).toBe(dialog.querySelector('button'));
  act(() => { document.activeElement.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true})); });
  expect(close).toHaveBeenCalledTimes(1);
});

test('focus returns to the trigger when dialog closes', () => {
  const trigger = document.createElement('button'); document.body.appendChild(trigger); trigger.focus();
  act(() => { ReactDOM.render(<PlaylistCapacityDialog error={error} onClose={() => {}} />, root); });
  act(() => { ReactDOM.render(<PlaylistCapacityDialog error={null} onClose={() => {}} />, root); });
  expect(document.activeElement).toBe(trigger);
  trigger.remove();
});

test('preflight rejection never performs a write; stale capacity rejection propagates', async () => {
  const failure = Object.assign(new Error('full'), {response: {data: error}});
  const denied = jest.fn().mockRejectedValue(failure);
  await expect(addWithCapacityCheck(denied)).rejects.toBe(failure);
  expect(denied.mock.calls).toEqual([[true]]);
  const stale = jest.fn().mockResolvedValueOnce({allowed: true}).mockRejectedValueOnce(failure);
  await expect(addWithCapacityCheck(stale)).rejects.toBe(failure);
  expect(stale.mock.calls).toEqual([[true], [false]]);
});

test('requires affirmative preflight and reports actual additions including zero', async () => {
  const malformed = jest.fn().mockResolvedValue({});
  await expect(addWithCapacityCheck(malformed)).rejects.toThrow();
  expect(malformed).toHaveBeenCalledTimes(1);
  for (const added_count of [0, 1]) {
    const send = jest.fn().mockResolvedValueOnce({allowed: true}).mockResolvedValueOnce({added_count});
    expect(await addWithCapacityCheck(send)).toEqual({added_count});
  }
});
