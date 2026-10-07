import React from 'react';
import ReactDOM from 'react-dom';
import { act } from 'react-dom/test-utils';
import { EventEmitter } from 'events';
import { PlaylistsSelection } from '../static/js/components/playlists-selection/PlaylistsSelection';
import { PopupContent } from '../static/js/components/_shared/popup/PopupContent';
import { MediaPageStore } from '../static/js/utils/stores/';
import { PageActions } from '../static/js/utils/actions/';
jest.mock('../static/js/components/playlists-selection/PlaylistsSelection.scss', () => ({}));
jest.mock('../static/js/components/PlaylistCapacityDialog.scss', () => ({}));
jest.mock('../static/js/components/_shared/popup/Popup.scss', () => ({}));
jest.mock('../static/js/utils/stores/', () => {
  const E = require('events');
  const store = new E(); store.get = key => key === 'playlists' ? [{playlist_id:'p',title:'Course',media_list:[]}] : 'm';
  store.isPlaylistAdditionPending=()=>false;
  return {MediaPageStore:store,PageStore:new E()};
});
jest.mock('../static/js/utils/actions/', () => ({PageActions:{addNotification:jest.fn()},MediaPageActions:{addMediaToPlaylist:jest.fn()}}));
jest.mock('../static/js/components/_shared', () => ({CircleIconButton:({children,...props})=><button {...props}>{children}</button>,MaterialIcon:()=>null}));
jest.mock('../static/js/components/playlist-form/PlaylistCreationForm', () => ({PlaylistCreationForm:()=>null}));
const full={code:'playlist_capacity_exceeded',limit:100,playlists:[{id:1,title:'Course',current_count:100,requested_new_count:1,remaining_capacity:0}]};
let root, popup;
beforeEach(() => {
  root=document.createElement('div');document.body.appendChild(root); popup=React.createRef();
  act(() => {ReactDOM.render(<PopupContent contentRef={popup}><PlaylistsSelection /></PopupContent>,root);});
  act(() => popup.current.tryToShow());
});
afterEach(() => {act(() => {ReactDOM.unmountComponentAtNode(root);}); root.remove(); jest.clearAllMocks();});

test('single capacity dialog preserves parent popup on mouse and Escape dismissal', () => {
  act(() => { MediaPageStore.emit('media_playlist_addition_failed',full); });
  const dialog=document.querySelector('[role="alertdialog"]');
  expect(dialog.textContent).toContain('Course');
  expect(root.querySelector('input').checked).toBe(false);
  expect(root.querySelector('input').disabled).toBe(true);
  act(() => {dialog.querySelector('button').dispatchEvent(new MouseEvent('mousedown',{bubbles:true}));});
  expect(root.querySelector('.saveto-popup')).not.toBeNull();
  act(() => {document.activeElement.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',keyCode:27,bubbles:true}));});
  expect(document.querySelector('[role="alertdialog"]')).toBeNull();
  expect(root.querySelector('.saveto-popup')).not.toBeNull();
  expect(root.querySelector('input').disabled).toBe(false);
  expect(PageActions.addNotification).not.toHaveBeenCalled();
});

test('malformed capacity errors use ordinary failure notification', () => {
  jest.useFakeTimers();
  act(() => {MediaPageStore.emit('media_playlist_addition_failed',{code:'playlist_capacity_exceeded'}); jest.runAllTimers();});
  expect(document.querySelector('[role="alertdialog"]')).toBeNull();
  expect(PageActions.addNotification).toHaveBeenCalledWith("Media's addition to playlist failed",'playlistMediaAdditionFail');
  jest.useRealTimers();
});
