import React from 'react';
import ReactDOM from 'react-dom';
import { act, Simulate } from 'react-dom/test-utils';
import { BulkActionPlaylistModal } from '../static/js/components/BulkActionPlaylistModal';
jest.mock('../static/js/components/BulkActionPlaylistModal.scss', () => ({}));
jest.mock('../static/js/components/PlaylistCapacityDialog.scss', () => ({}), {virtual: true});
jest.mock('../static/js/utils/helpers/', () => ({translateString: s => s}));
const full = {code:'playlist_capacity_exceeded', limit:100, playlists:[{id:2,title:'Full',current_count:100,requested_new_count:1,remaining_capacity:0}]};
const playlists = [{id:1,friendly_token:'old',title:'Old'}, {id:2,friendly_token:'full',title:'Full'}];
const reply = (data, ok=true) => Promise.resolve({ok, json:async () => data});
let root, props;
beforeEach(() => { root=document.createElement('div'); document.body.appendChild(root); props={isOpen:true,selectedMediaIds:['m'],username:'owner',csrfToken:'csrf',onCancel:jest.fn(),onSuccess:jest.fn(),onError:jest.fn()}; global.fetch=jest.fn(); });
afterEach(() => { act(() => { ReactDOM.unmountComponentAtNode(root); }); root.remove(); delete global.fetch; });
const button = text => [...root.querySelectorAll('button')].find(el => el.textContent === text);
async function setup() {
  fetch.mockImplementation((url, opts) => reply(opts ? {results:[playlists[0]]} : {results:playlists}));
  await act(async () => { ReactDOM.render(<BulkActionPlaylistModal {...props} />,root); });
  act(() => Simulate.click(root.querySelector('.playlist-item .add-btn:not(:disabled)')));
  act(() => Simulate.click(root.querySelector('.remove-btn'))); // remove old membership after adding new
  fetch.mockClear();
}

test.each([false,true])('bulk capacity failure (stale=%s) retains selection, blocks writes/removals and shows no success', async stale => {
  await setup();
  fetch.mockImplementation((url, opts) => {
    const body=JSON.parse(opts.body);
    return stale && body.dry_run ? reply({allowed:true}) : reply(full,false);
  });
  await act(async () => { Simulate.click(button('Proceed')); });
  expect(document.querySelector('[role="alertdialog"]').textContent).toContain('Full');
  expect(props.onSuccess).not.toHaveBeenCalled(); expect(props.onCancel).not.toHaveBeenCalled();
  expect(fetch.mock.calls.map(([,o]) => JSON.parse(o.body).dry_run)).toEqual(stale ? [true,false] : [true]);
  expect(fetch.mock.calls.every(([,o]) => JSON.parse(o.body).action==='add_to_playlist')).toBe(true);
  expect(root.querySelector('.remove-btn').closest('.playlist-item').textContent).toContain('Full');
  act(() => Simulate.click(document.querySelector('[role="alertdialog"] button')));
  expect(document.querySelector('[role="alertdialog"]')).toBeNull();
  expect(button('Proceed').disabled).toBe(false);
});

test('pending request disables submission; successful addition and removal reports success', async () => {
  await setup(); let release;
  fetch.mockImplementationOnce(() => new Promise(resolve => { release=resolve; }));
  act(() => Simulate.click(button('Proceed')));
  expect(button('Processing...').disabled).toBe(true);
  fetch.mockImplementation(() => reply({added_count:0}));
  await act(async () => { release(await reply({allowed:true})); });
  expect(fetch.mock.calls.map(([,o]) => JSON.parse(o.body).action)).toEqual(['add_to_playlist','add_to_playlist','remove_from_playlist']);
  expect(props.onSuccess).toHaveBeenCalledWith('Successfully updated playlist membership');
  expect(props.onCancel).toHaveBeenCalledTimes(1);
});

test('removal failure never reports overall success', async () => {
  await setup();
  const log=jest.spyOn(console,'error').mockImplementation(() => {});
  fetch.mockImplementation((url, opts) => { const b=JSON.parse(opts.body); return b.action==='remove_from_playlist' ? reply({},false) : reply(b.dry_run ? {allowed:true} : {added_count:1}); });
  await act(async () => Simulate.click(button('Proceed')));
  expect(props.onSuccess).not.toHaveBeenCalled(); expect(props.onError).toHaveBeenCalled();
  log.mockRestore();
});
