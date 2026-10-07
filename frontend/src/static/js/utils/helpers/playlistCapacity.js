export function parsePlaylistCapacityError(data) {
  const count = value => Number.isSafeInteger(value) && value >= 0;
  if (!data || data.code !== 'playlist_capacity_exceeded' || !count(data.limit) ||
      !Array.isArray(data.playlists) || !data.playlists.length || !data.playlists.every(p =>
        p && count(p.id) && typeof p.title === 'string' && count(p.current_count) &&
        count(p.requested_new_count) && count(p.remaining_capacity))) return null;
  return data;
}

// send(true) is read-only; the server checks again under a lock for send(false).
export async function addWithCapacityCheck(send) {
  const preview = await send(true);
  if (!preview || preview.allowed !== true) throw new Error('Invalid playlist capacity response');
  const result = await send(false);
  if (!result || !Number.isSafeInteger(result.added_count) || result.added_count < 0) {
    throw new Error('Invalid playlist addition response');
  }
  return result;
}
