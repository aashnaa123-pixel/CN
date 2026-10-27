import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';

/**
 * Optional third-party content. Both categories are OFF until the visitor turns
 * them on, and nothing is requested from those hosts before then.
 *
 *  - backgroundVideo: decorative looping videos served from a CloudFront host.
 *  - youtube:         the explanatory YouTube embeds (youtube-nocookie.com).
 */
export type ConsentKey = 'backgroundVideo' | 'youtube';
export type ConsentState = Record<ConsentKey, boolean>;

const STORAGE_KEY = 'netrescue.consent.v1';
const DEFAULTS: ConsentState = { backgroundVideo: false, youtube: false };

function load(): ConsentState {
    try {
        const raw = window.localStorage.getItem(STORAGE_KEY);
        if (!raw) return { ...DEFAULTS };
        const p = JSON.parse(raw);
        return { backgroundVideo: p?.backgroundVideo === true, youtube: p?.youtube === true };
    } catch {
        return { ...DEFAULTS };
    }
}

interface Ctx {
    consent: ConsentState;
    set: (k: ConsentKey, v: boolean) => void;
    panelOpen: boolean;
    openPanel: () => void;
    closePanel: () => void;
}

const ConsentCtx = createContext<Ctx | null>(null);

export function ConsentProvider({ children }: { children: React.ReactNode }) {
    const [consent, setConsent] = useState<ConsentState>(DEFAULTS);
    const [panelOpen, setPanelOpen] = useState(false);

    useEffect(() => { setConsent(load()); }, []);

    const set = useCallback((k: ConsentKey, v: boolean) => {
        setConsent(prev => {
            const next = { ...prev, [k]: v };
            try { window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next)); } catch { /* storage blocked */ }
            return next;
        });
    }, []);
    const openPanel = useCallback(() => setPanelOpen(true), []);
    const closePanel = useCallback(() => setPanelOpen(false), []);

    const value = useMemo<Ctx>(
        () => ({ consent, set, panelOpen, openPanel, closePanel }),
        [consent, set, panelOpen, openPanel, closePanel],
    );
    return <ConsentCtx.Provider value={value}>{children}</ConsentCtx.Provider>;
}

const FALLBACK: Ctx = {
    consent: DEFAULTS, set: () => {}, panelOpen: false, openPanel: () => {}, closePanel: () => {},
};

/** Outside a provider everything stays off, which is the safe default. */
export function useConsent(): Ctx {
    return useContext(ConsentCtx) ?? FALLBACK;
}
