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

export function installMediaAuthorizationInterceptor(axios, { mediaDomain } = {}) {
    if (!axios || !axios.interceptors) return () => {};
    const interceptor = axios.interceptors.response.use(undefined, (error) => {
        const response = error && error.response;
        const config = error && error.config;
        const url = (config && config.url) || '';
        const protectedMedia = response && response.status === 403 && (
            url.startsWith('/media/') || (mediaDomain && url.startsWith(`https://${mediaDomain}/media/`))
        );
        if (!protectedMedia || !config || config._mediaAuthorizationRetry) {
            return Promise.reject(error);
        }
        config._mediaAuthorizationRetry = true;
        return refreshMediaAuthorization().then(() => axios(config));
    });
    return () => axios.interceptors.response.eject(interceptor);
}
