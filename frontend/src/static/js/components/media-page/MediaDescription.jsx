import React, { useMemo } from 'react';
import MarkdownIt from 'markdown-it';

const markdown = new MarkdownIt({ html: false, breaks: true, linkify: false, typographer: false })
    .disable(['image', 'table', 'strikethrough']);
const defaultValidateLink = markdown.validateLink;
markdown.validateLink = url => {
    if (!defaultValidateLink(url) || /[\u0000-\u0020\u007f]|%(?:0[0-9a-f]|1[0-9a-f]|7f)/i.test(url)) return false;
    const scheme = /^([a-z][a-z\d+.-]*):/i.exec(url);
    return !scheme || /^(https?|mailto)$/i.test(scheme[1]);
};

const tags = new Set(['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'strong', 'em', 'ul', 'ol', 'li', 'blockquote', 'a']);

function renderTimestampText(text, keyPrefix) {
    const nodes = [];
    const pattern = /(?:\d{1,2}:)?\d{1,2}:\d{2}/g;
    let end = 0;
    let match;
    while ((match = pattern.exec(text))) {
        nodes.push(text.slice(end, match.index));
        const seconds = match[0].split(':').reduce((total, part) => total * 60 + Number(part), 0);
        nodes.push(
            <a key={`${keyPrefix}-${match.index}`} href="#" className="video-timestamp" data-timestamp={seconds}>
                {match[0]}
            </a>
        );
        end = pattern.lastIndex;
    }
    nodes.push(text.slice(end));
    return nodes;
}

function renderTokens(tokens, keyPrefix, insideLink = false) {
    const root = { children: [], insideLink };
    const stack = [root];
    tokens.forEach((token, index) => {
        const parent = stack[stack.length - 1];
        const key = `${keyPrefix}-${index}`;
        if (token.nesting === 1) {
            stack.push({ token, key, children: [], insideLink: parent.insideLink || token.tag === 'a' });
        } else if (token.nesting === -1) {
            const frame = stack.pop();
            const { token: opening, children } = frame;
            const props = { key: frame.key };
            if (opening.tag === 'a') {
                const href = opening.attrGet('href');
                if (href && markdown.validateLink(href)) props.href = href;
                props.title = opening.attrGet('title') || undefined;
            } else if (opening.tag === 'ol' && opening.attrGet('start')) {
                props.start = Number(opening.attrGet('start'));
            }
            stack[stack.length - 1].children.push(
                opening.hidden || !tags.has(opening.tag)
                    ? <React.Fragment key={frame.key}>{children}</React.Fragment>
                    : React.createElement(opening.tag, props, children)
            );
        } else if (token.type === 'inline') {
            parent.children.push(...renderTokens(token.children || [], key, parent.insideLink));
        } else if (token.type === 'text') {
            parent.children.push(...(parent.insideLink ? [token.content] : renderTimestampText(token.content, key)));
        } else if (token.type === 'code_inline') {
            parent.children.push(<code key={key}>{token.content}</code>);
        } else if (token.type === 'fence' || token.type === 'code_block') {
            parent.children.push(<pre key={key}><code>{token.content}</code></pre>);
        } else if (token.type === 'softbreak' || token.type === 'hardbreak') {
            parent.children.push(<br key={key} />);
        } else if (token.type === 'hr') {
            parent.children.push(<hr key={key} />);
        } else {
            parent.children.push(token.content);
        }
    });
    return root.children;
}

export default function MediaDescription({ source = '' }) {
    const content = useMemo(() => source.trim() ? renderTokens(markdown.parse(source, {}), 'description') : null, [source]);
    return content ? <div className="media-content-description">{content}</div> : null;
}
