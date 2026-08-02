let refreshPromise = null;

export function refreshMediaAuthorization() {
    if (!refreshPromise) {
        refreshPromise = fetch('/api/v1/media-auth/bootstrap', {
            credentials: 'same-origin',
            headers: { Accept: 'application/json' },
        }).then((response) => {
            if (!response.ok) throw new Error(`Media authorization failed (${response.status})`);
            return response.json();
        }).finally(() => {
            refreshPromise = null;
        });
    }
    return refreshPromise;
}

export function retryAfterMediaAuthorization(request, { cacheBust = false } = {}) {
    return refreshMediaAuthorization().then(() => {
        if (!cacheBust) return request;
        const separator = request.includes('?') ? '&' : '?';
        return `${request}${separator}media_auth_refresh=${Date.now()}`;
    });
}
