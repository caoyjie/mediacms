import React, { useMemo } from 'react';
import { common, createLowlight } from 'lowlight';

const highlighter = createLowlight(common);

function renderHighlightNodes(nodes, prefix) {
    return nodes.map((node, index) => {
        if (node.type === 'text') return node.value;
        // Only create spans and text: code is never inserted as HTML.
        const key = `${prefix}-${index}`;
        return (
            <span key={key} className={(node.properties?.className || []).join(' ')}>
                {renderHighlightNodes(node.children || [], key)}
            </span>
        );
    });
}

export default function DescriptionCode({ source, info = '' }) {
    const language = info.trim().split(/\s+/)[0].toLowerCase();
    const highlighted = useMemo(() => {
        if (!language || !highlighter.registered(language)) return null;
        try {
            return renderHighlightNodes(highlighter.highlight(language, source).children, 'code');
        } catch {
            // A grammar failure must not hide the author's code or break the page.
            return null;
        }
    }, [source, language]);

    return (
        <pre><code className={highlighted ? `hljs language-${language}` : undefined}>{highlighted || source}</code></pre>
    );
}
