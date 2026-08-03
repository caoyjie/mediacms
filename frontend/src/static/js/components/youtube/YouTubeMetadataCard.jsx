import React from 'react';
import PropTypes from 'prop-types';

function formatDuration(seconds) {
    if (!Number.isFinite(seconds)) return '';
    const minutes = Math.floor(seconds / 60);
    const remainder = Math.floor(seconds % 60).toString().padStart(2, '0');
    return `${minutes}:${remainder}`;
}

export default function YouTubeMetadataCard({ metadata, title, onTitleChange, subtitleOptions = [], selectedSubtitles = [], onSubtitleChange, disabled = false }) {
    if (!metadata) return <p className="youtube-metadata-empty">Metadata is not available yet.</p>;
    return (
        <section className="youtube-metadata-card" aria-label="YouTube metadata">
            {metadata.thumbnail ? <img src={metadata.thumbnail} alt="YouTube video thumbnail" /> : null}
            <div className="youtube-metadata-fields">
                <label htmlFor="youtube-title">Title</label>
                <input id="youtube-title" type="text" value={title || metadata.title || ''} onChange={(event) => onTitleChange(event.target.value)} disabled={disabled} />
                <p>Duration: {formatDuration(Number(metadata.duration))}</p>
                <p>{metadata.description || 'No description available.'}</p>
                <fieldset>
                    <legend>Subtitles</legend>
                    {subtitleOptions.length ? subtitleOptions.map((option) => (
                        <label key={option.language}>
                            <input
                                type="checkbox"
                                checked={selectedSubtitles.includes(option.language)}
                                onChange={() => onSubtitleChange(option.language)}
                            />
                            {option.language === 'zh' ? 'Chinese' : option.language === 'en' ? 'English' : option.language}
                            {option.kind === 'automatic' ? ' (automatic)' : ''}
                        </label>
                    )) : <p>No subtitles available.</p>}
                </fieldset>
            </div>
        </section>
    );
}

YouTubeMetadataCard.propTypes = {
    metadata: PropTypes.shape({ title: PropTypes.string, description: PropTypes.string, duration: PropTypes.number, thumbnail: PropTypes.string }),
    title: PropTypes.string,
    onTitleChange: PropTypes.func.isRequired,
    disabled: PropTypes.bool,
    subtitleOptions: PropTypes.arrayOf(PropTypes.shape({ language: PropTypes.string, kind: PropTypes.string })),
    selectedSubtitles: PropTypes.arrayOf(PropTypes.string),
    onSubtitleChange: PropTypes.func,
};
