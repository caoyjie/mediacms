import { shouldPersistPlaybackPosition } from './usePlaybackProgress';

describe('shouldPersistPlaybackPosition', () => {
    test('persists after a backward seek of at least five seconds', () => {
        expect(shouldPersistPlaybackPosition(120, 112, false)).toBe(true);
    });

    test('does not persist tiny non-completed updates', () => {
        expect(shouldPersistPlaybackPosition(120, 123, false)).toBe(false);
    });

    test('always persists completion', () => {
        expect(shouldPersistPlaybackPosition(120, 120, true)).toBe(true);
    });
});
