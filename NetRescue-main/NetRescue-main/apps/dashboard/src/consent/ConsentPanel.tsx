import React, { useEffect, useRef } from 'react';
import { Link } from 'react-router-dom';
import { ConsentKey, useConsent } from './ConsentContext';

const OPTIONS: { key: ConsentKey; title: string; body: string }[] = [
    {
        key: 'youtube',
        title: 'YouTube explainer videos',
        body: 'Loads embedded videos from youtube-nocookie.com (Google). Google sees your IP address and browser details once a video loads.',
    },
];

/** Modal preferences dialog: focus moves in, is trapped, Esc closes, focus returns. */
export default function ConsentPanel() {
    const { consent, set, panelOpen, closePanel } = useConsent();
    const ref = useRef<HTMLDivElement>(null);

    useEffect(() => {
        if (!panelOpen) return;
        const previous = document.activeElement as HTMLElement | null;
        const root = ref.current!;
        const focusables = () =>
            Array.from(root.querySelectorAll<HTMLElement>('button, a[href], input'));
        focusables()[0]?.focus();
        const onKey = (e: KeyboardEvent) => {
            if (e.key === 'Escape') { e.preventDefault(); closePanel(); return; }
            if (e.key !== 'Tab') return;
            const f = focusables();
            if (!f.length) return;
            const first = f[0], last = f[f.length - 1];
            if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
            else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
        };
        document.addEventListener('keydown', onKey);
        return () => { document.removeEventListener('keydown', onKey); previous?.focus(); };
    }, [panelOpen, closePanel]);

    if (!panelOpen) return null;
    return (
        <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/80 px-4">
            <div ref={ref} role="dialog" aria-modal="true" aria-labelledby="consent-title"
                className="w-full max-w-lg rounded-lg border border-white/30 bg-black p-6">
                <h2 id="consent-title" className="text-[18px] mb-2">Third-party content</h2>
                <p className="text-[13px] text-white/70 leading-relaxed mb-5">
                    Everything below is off by default and optional; the lab tools work without it. Your choice is saved
                    in this browser only. Details are in the{' '}
                    <Link to="/cookies" onClick={closePanel} className="underline">Cookie &amp; third-party policy</Link>.
                </p>
                <div className="space-y-4">
                    {OPTIONS.map(o => (
                        <div key={o.key} className="flex items-start gap-3">
                            <input id={`consent-${o.key}`} type="checkbox" className="mt-1 h-4 w-4 shrink-0"
                                checked={consent[o.key]} onChange={e => set(o.key, e.target.checked)} />
                            <label htmlFor={`consent-${o.key}`} className="text-[13px]">
                                <span className="block text-white">{o.title}</span>
                                <span className="block text-white/70 leading-relaxed">{o.body}</span>
                            </label>
                        </div>
                    ))}
                </div>
                <div className="mt-6 flex justify-end">
                    <button onClick={closePanel}
                        className="px-4 py-2 rounded-lg border border-white/40 text-[13px] hover:bg-white/10">Done</button>
                </div>
            </div>
        </div>
    );
}
