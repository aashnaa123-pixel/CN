/**
 * Operator details for the legal pages.
 *
 * Every field is `null` until the operator who actually runs this site fills it
 * in. Nothing here is invented: a `null` renders as a visible "not configured"
 * notice on the policy pages, so an unfinished deployment cannot look finished.
 * Values come from build-time env vars (VITE_LEGAL_*).
 */
const env = import.meta.env as Record<string, string | undefined>;
const pick = (v: string | undefined) => (v && v.trim() ? v.trim() : null);

export const LEGAL = {
    operatorName: pick(env.VITE_LEGAL_OPERATOR_NAME),
    operatorAddress: pick(env.VITE_LEGAL_OPERATOR_ADDRESS),
    contactEmail: pick(env.VITE_LEGAL_CONTACT_EMAIL),
    grievanceOfficer: pick(env.VITE_LEGAL_GRIEVANCE_OFFICER),
} as const;

/** Version date of the policy text shipped in this codebase. */
export const POLICY_TEXT_VERSION = '2026-10-25';
