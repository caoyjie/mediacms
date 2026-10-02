import React from 'react';
import ReactDOM from 'react-dom';
import { act } from 'react-dom/test-utils';
import MediaDescription from '../../src/static/js/components/media-page/MediaDescription';

let container;
beforeEach(() => { container = document.createElement('div'); document.body.appendChild(container); });
afterEach(() => { act(() => { ReactDOM.unmountComponentAtNode(container); }); container.remove(); });
function render(source) { act(() => { ReactDOM.render(<MediaDescription source={source} />, container); }); }

test('renders required markdown including nested lists and ordered list starts', () => {
    render('# Heading\n\n**bold** *italic*\n\n- first\n  - nested\n\n3. third\n4. fourth\n\n> quote\n\n`inline`\n\n```html\n<div>code</div>\n```');
    for (const [selector, text] of [['h1', 'Heading'], ['strong', 'bold'], ['em', 'italic'], ['ul ul li', 'nested'], ['blockquote', 'quote'], ['p code', 'inline'], ['pre code', '<div>code</div>\n']]) {
        expect(container.querySelector(selector).textContent.trim()).toBe(text.trim());
    }
    expect(container.querySelector('ol').getAttribute('start')).toBe('3');
});

test('preserves readable lines and code indentation without trimming source', () => {
    render('first\nsecond\n\nthird');
    expect(container.querySelectorAll('p')).toHaveLength(2);
    expect(container.querySelectorAll('br')).toHaveLength(1);
    render('    <div>01:30</div>\n');
    expect(container.querySelector('pre code').textContent).toBe('<div>01:30</div>\n');
    expect(container.querySelector('a')).toBeNull();
});

test.each(['', ' \n  '])('omits empty description %j', source => {
    render(source);
    expect(container.childNodes).toHaveLength(0);
});

test('creates timestamp links with complete labels and correct seconds', () => {
    render('开始01:30，01:02:30 end 2:03');
    const anchors = [...container.querySelectorAll('a.video-timestamp')];
    expect(anchors.map(a => [a.textContent, a.dataset.timestamp, a.getAttribute('href')])).toEqual([
        ['01:30', '90', '#'], ['01:02:30', '3750', '#'], ['2:03', '123', '#'],
    ]);
    expect(container.textContent).toContain('开始01:30，01:02:30 end 2:03');
});

test('does not alter links, autolinks or any code tokens', () => {
    render('[**01:30**](https://example.com/01:30) <https://example.com/01:30> `01:30`\n\n```\n01:02:30\n```\n\n    01:30\n');
    expect(container.querySelectorAll('a')).toHaveLength(2);
    expect(container.querySelectorAll('.video-timestamp')).toHaveLength(0);
    expect(container.querySelector('a strong').textContent).toBe('01:30');
    expect(container.querySelectorAll('pre')).toHaveLength(2);
});

test.each(['https://example.com', 'http://example.com', 'mailto:a@example.com', '/path', '../path', '#section'])(
    'keeps allowed link %s', href => {
        render(`[label](${href} "title")`);
        expect(container.querySelector('a').getAttribute('href')).toBe(href);
        expect(container.querySelector('a').title).toBe('title');
    }
);

test.each(['javascript:alert(1)', 'JaVaScRiPt:alert(1)', 'jav&#x61;script:alert(1)', 'java&#9;script:alert(1)', 'vbscript:evil', 'file:///etc/passwd', 'data:text/html,evil', 'data:image/png;base64,AAAA', 'ftp://example.com'])(
    'does not produce a dangerous or unsupported link %s', href => {
        render(`[label](${href})`);
        expect(container.querySelector('a')).toBeNull();
        expect(container.textContent).toContain('label');
    }
);

test('treats raw HTML and forged timestamp attributes as text', () => {
    const source = '<script>alert(1)</script>\n<img src=x onerror=alert(1)>\n<svg onload=alert(1)>\n<a class="video-timestamp" data-timestamp="999">evil</a>';
    render(source);
    expect(container.querySelector('script,img,svg,a,[onerror],[onload]')).toBeNull();
    expect(container.textContent).toBe(source.replace(/\n/g, ''));
    expect(container.querySelectorAll('br')).toHaveLength(3);
});

test('does not load markdown images and renders malformed input without errors or key warnings', () => {
    const error = jest.spyOn(console, 'error').mockImplementation(() => {});
    try {
        render('![alt](https://example.com/image.png)\n\n**unfinished and \\*literal\\*\n\n# heading\n\n01:30 **01:31**');
        expect(container.querySelector('img')).toBeNull();
        expect(container.textContent).toContain('alt');
        expect(container.textContent).toContain('*literal*');
        expect(error).not.toHaveBeenCalled();
    } finally { error.mockRestore(); }
});
