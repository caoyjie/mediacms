import React from 'react';
import ReactDOM from 'react-dom';
import { act } from 'react-dom/test-utils';
import VideoJSEmbed from '../../src/static/js/components/VideoJS/VideoJSEmbed';
import MediaDescription from '../../src/static/js/components/media-page/MediaDescription';

test('description timestamps seek through the real player bridge', () => {
    jest.useFakeTimers();
    const container = document.createElement('div');
    document.body.appendChild(container);
    let position = 0;
    window.videojsPlayers = { 'video-main': {
        duration: () => 7200,
        currentTime: value => { position = value; },
    } };
    try {
        act(() => { ReactDOM.render(<>
            <VideoJSEmbed siteUrl="http://localhost" data={{}} />
            <MediaDescription source={'01:30 **01:02:30** [link](#section) `01:30`'} />
        </>, container); });
        const stamps = container.querySelectorAll('.video-timestamp');
        act(() => { stamps[0].click(); });
        expect(position).toBe(90);
        act(() => { stamps[1].click(); });
        expect(position).toBe(3750);
        act(() => { container.querySelector('a[href="#section"]').click(); container.querySelector('code').click(); });
        expect(position).toBe(3750);
    } finally {
        act(() => { ReactDOM.unmountComponentAtNode(container); });
        container.remove();
        document.querySelectorAll('script[src*="video-js.js"],link[href*="video-js.css"]').forEach(node => node.remove());
        delete window.videojsPlayers;
        delete window.MEDIA_DATA;
        jest.useRealTimers();
    }
});
