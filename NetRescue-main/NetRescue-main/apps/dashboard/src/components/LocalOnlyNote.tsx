import type { ReactNode } from 'react';
import { IS_REMOTE_API } from '../api/client';

/** Shown only against a non-local API: says what the hosted backend does not do for the visitor. */
export default function LocalOnlyNote({ children }: { children: ReactNode }) {
    if (!IS_REMOTE_API) return null;
    return (
        <p role="note" data-testid="local-only-note"
            className="border border-amber-300/30 bg-amber-300/5 rounded-lg px-3 py-2 text-[12px] text-amber-200/80">
            {children}
        </p>
    );
}
