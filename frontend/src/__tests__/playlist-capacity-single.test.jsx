import axios from 'axios';
import store from '../static/js/utils/stores/MediaPageStore';
jest.mock('axios');
jest.mock('../static/js/utils/settings/config.js', () => ({ config: () => ({api:{playlists:'/api/v1/playlists'}}) }));
jest.mock('../static/js/utils/stores/PageStore.js', () => ({}));
jest.mock('../static/js/utils/helpers', () => ({
  ...jest.requireActual('../static/js/utils/helpers/requests'), exportStore:s=>s, csrfToken:()=> 'csrf'
}));
const full={code:'playlist_capacity_exceeded', limit:100,playlists:[{id:1,title:'Full',current_count:100,requested_new_count:1,remaining_capacity:0}]};
const action={type:'ADD_MEDIA_TO_PLAYLIST',playlist_id:'p',media_id:'m'};
const flush=async () => { for(let i=0;i<12;i++) await Promise.resolve(); };
beforeEach(() => { axios.put.mockReset(); store.removeAllListeners(); });

test.each([false,true])('single (stale=%s) forwards structured failure and clears pending state', async stale => {
  const failed=jest.fn(), succeeded=jest.fn();
  store.on('media_playlist_addition_failed',failed); store.on('media_playlist_addition_completed',succeeded);
  if(stale) axios.put.mockResolvedValueOnce({data:{allowed:true}});
  axios.put.mockRejectedValue({response:{data:full}});
  store.actions_handler(action);
  expect(store.isPlaylistAdditionPending('p')).toBe(true);
  store.actions_handler(action); // duplicate click must not send another preflight
  await flush();
  expect(axios.put.mock.calls.map(([,body]) => body.dry_run)).toEqual(stale?[true,false]:[true]);
  expect(failed).toHaveBeenCalledWith(full); expect(succeeded).not.toHaveBeenCalled();
  expect(store.isPlaylistAdditionPending('p')).toBe(false);
});

test('single updates members once only after actual success', async () => {
  store.playlistsResponse({data:{count:0,results:[]}});
  store.actions_handler({type:'APPEND_NEW_PLAYLIST',playlist_data:{playlist_id:'p',media_list:[]}});
  const succeeded=jest.fn(); store.on('media_playlist_addition_completed',succeeded);
  axios.put.mockResolvedValueOnce({data:{allowed:true}}).mockResolvedValueOnce({data:{added_count:0}});
  store.actions_handler(action); await flush();
  expect(succeeded).toHaveBeenCalledWith('p',0);
  expect(store.get('playlists')[0].media_list).toEqual(['m']);
  expect(store.isPlaylistAdditionPending('p')).toBe(false);
});
