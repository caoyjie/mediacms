import React from 'react';
import ReactDOM from 'react-dom';
import { act } from 'react-dom/test-utils';
import ViewerInfoContent from '../../src/static/js/components/media-page/ViewerInfoContent';

let mockSummary = '';
jest.mock('../../src/static/js/utils/contexts/', () => ({ SiteContext: { _currentValue: { url: 'http://localhost' } } }));
jest.mock('../../src/static/js/utils/hooks/', () => ({ useUser: () => ({ userCan: {} }), usePopup: () => [{}] }));
jest.mock('../../src/static/js/utils/stores/', () => ({
    PageStore: { get: key => ({
        'config-enabled': { taxonomies: {} }, 'config-options': { pages: { media: {} } },
        'config-media-item': { displayAuthor: false },
    }[key]) },
    MediaPageStore: { get: key => key === 'media-summary' ? mockSummary : [], on: jest.fn(), removeListener: jest.fn() },
}));
jest.mock('../../src/static/js/utils/actions/', () => ({ PageActions: {}, MediaPageActions: {} }));
jest.mock('../../src/static/js/utils/helpers/', () => ({
    formatInnerLink: value => value, inEmbeddedApp: () => false, translateString: value => value,
}));
jest.mock('../../src/static/js/components/_shared/', () => ({ PopupMain: () => null }));
jest.mock('../../src/static/js/components/comments/Comments', () => () => null);

let container;
beforeEach(() => { mockSummary = ''; container = document.createElement('div'); document.body.appendChild(container); });
afterEach(() => { act(() => { ReactDOM.unmountComponentAtNode(container); }); container.remove(); });
function render(description) {
    act(() => { ReactDOM.render(<ViewerInfoContent description={description} author={{ url: '/', thumb: '' }} />, container); });
}
test('renders markdown directly on ordinary media pages without a summary', () => {
    render('# Heading\n\n**notes**');
    expect(container.querySelector('.media-content-description h1').textContent).toBe('Heading');
    expect(container.querySelector('strong').textContent).toBe('notes');
    expect(container.querySelector('.load-more')).toBeNull();
});
test('preserves leading indentation in description source', () => {
    render('    <div>01:30</div>\n');
    expect(container.querySelector('pre code').textContent).toBe('<div>01:30</div>\n');
    expect(container.querySelector('.video-timestamp')).toBeNull();
});
test('hides blank descriptions', () => {
    render(' \n ');
    expect(container.querySelector('.media-content-description')).toBeNull();
});
test('retains the existing optional summary expansion behavior', () => {
    mockSummary = 'Short introduction';
    render('**Full notes**');
    expect(container.querySelector('.media-content-description')).toBeNull();
    act(() => { container.querySelector('.load-more').click(); });
    expect(container.querySelector('.media-content-description strong').textContent).toBe('Full notes');
    expect(container.querySelector('.media-content-summary').textContent).toBe('Short introduction');
    act(() => { container.querySelector('.load-more').click(); });
    expect(container.querySelector('.media-content-description')).toBeNull();
});
