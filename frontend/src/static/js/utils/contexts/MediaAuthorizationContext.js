import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
import UserContext from './UserContext';
import { refreshMediaAuthorization } from '../services/mediaAuthorization';

const MediaAuthorizationContext = createContext({ expiresAt: null, refresh: () => Promise.resolve() });

export function MediaAuthorizationProvider({ children }) {
    const user = useContext(UserContext);
    const [expiresAt, setExpiresAt] = useState(null);
    const timer = useRef(null);

    const refresh = useCallback(() => refreshMediaAuthorization().then((data) => {
        setExpiresAt(data.expires_at || null);
        return data;
    }), []);

    useEffect(() => {
        if (user.isAnonymous) return undefined;
        let active = true;
        refresh().catch(() => {});
        const schedule = () => {
            if (!active || !expiresAt) return;
            const delay = Math.max(30_000, (expiresAt * 1000) - Date.now() - 120_000);
            timer.current = window.setTimeout(() => refresh().then(schedule).catch(schedule), delay);
        };
        schedule();
        return () => {
            active = false;
            if (timer.current) window.clearTimeout(timer.current);
        };
    }, [user.isAnonymous, expiresAt, refresh]);

    return <MediaAuthorizationContext.Provider value={{ expiresAt, refresh }}>{children}</MediaAuthorizationContext.Provider>;
}

export function useMediaAuthorization() {
    return useContext(MediaAuthorizationContext);
}

export default MediaAuthorizationContext;
