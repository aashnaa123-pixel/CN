import React, { useEffect } from 'react';
import { Link } from 'react-router-dom';
import { LEGAL, POLICY_TEXT_VERSION } from '../config/site';
import { useConsent } from '../consent/ConsentContext';

function Unset({ what }: { what: string }) {
    return (
        <span className="border border-amber-300/60 text-amber-200 rounded px-1.5 py-0.5 text-[12px]">
            [not configured: {what}. The operator must set this before launch.]
        </span>
    );
}

function Val({ v, what }: { v: string | null; what: string }) {
    return v ? <>{v}</> : <Unset what={what} />;
}

function Page({ title, children }: { title: string; children: React.ReactNode }) {
    useEffect(() => { document.title = `${title} — NetRescue`; }, [title]);
    return (
        <main id="main" className="min-h-screen px-4 sm:px-6 md:px-8 pt-28 pb-20">
            <article className="max-w-3xl mx-auto text-[14px] leading-relaxed text-white/80 space-y-4">
                <h1 className="font-light tracking-[-0.02em] text-white" style={{ fontSize: 'clamp(30px, 6vw, 48px)' }}>{title}</h1>
                <p className="text-white/60 text-[13px]">
                    Policy text version {POLICY_TEXT_VERSION}. This text describes what this codebase does. It is not legal advice
                    and has not been reviewed by a lawyer; see &ldquo;Open items&rdquo; at the end.
                </p>
                {children}
            </article>
        </main>
    );
}

const H = ({ children }: { children: React.ReactNode }) =>
    <h2 className="text-white text-[18px] pt-4">{children}</h2>;
const UL = ({ children }: { children: React.ReactNode }) =>
    <ul className="list-disc pl-6 space-y-1.5">{children}</ul>;

function Operator() {
    return (
        <UL>
            <li>Operator: <Val v={LEGAL.operatorName} what="legal name of the operator" /></li>
            <li>Address: <Val v={LEGAL.operatorAddress} what="postal address" /></li>
            <li>Privacy contact: <Val v={LEGAL.contactEmail} what="contact e-mail" /></li>
            <li>Grievance Officer: <Val v={LEGAL.grievanceOfficer} what="grievance officer name" /></li>
        </UL>
    );
}

export function PrivacyPage() {
    return (
        <Page title="Privacy Policy">
            <H>Who is responsible</H>
            <Operator />

            <H>What this site collects</H>
            <p>
                NetRescue is a networking lab. It has no user accounts, no sign-up or contact forms, no payments, and no
                analytics or advertising scripts. This site&apos;s pages set no cookies. The data below is all that the code handles.
            </p>
            <UL>
                <li><strong>Your consent choice</strong> (two on/off flags) is saved in your browser&apos;s local storage under the key
                    <code> netrescue.consent.v1</code>. It never leaves your browser.</li>
                <li><strong>Files you choose in the Multipath lab.</strong> The browser sends the file to the NetRescue API, which splits
                    it into chunks and holds the bytes in memory for the transfer. They are dropped on reset, expiry or API shutdown and
                    are not written to the database. Only upload files you are entitled to use.</li>
                <li><strong>Network measurements.</strong> The Diagnostics lab runs ping, route trace, DNS and adapter queries on the
                    machine running the API and shows the results, which can include that machine&apos;s IP addresses, adapter names
                    and the hosts you ask it to probe. Lab runs, node definitions and audit records may be stored in the
                    PostgreSQL database configured by the operator.</li>
                <li><strong>Audit records.</strong> When an API call changes state, the API records who made it (the principal name
                    sent in the <code>X-NetRescue-Principal</code> request header, default &ldquo;anonymous&rdquo;), the role, the action and the time.</li>
                <li><strong>Server logs.</strong> The API process writes operational logs (timestamps, log level, messages). The
                    web server or hosting platform used by the operator may additionally log IP addresses and request details;
                    the operator must state this here once known.</li>
            </UL>

            <H>Third parties that receive your IP address</H>
            <p>
                Only if you opt in through <em>Cookie &amp; third-party preferences</em> in the footer: the decorative background videos
                (CloudFront) and the YouTube explainer videos (youtube-nocookie.com, Google). See the{' '}
                <Link to="/cookies" className="underline">Cookie &amp; third-party policy</Link>. Fonts and icons are served from this site.
            </p>

            <H>Why, and how long</H>
            <UL>
                <li>To run the experiments you request and to keep a record of administrative changes. We do not sell data and do not
                    use it for marketing; there is no marketing consent because there is no marketing.</li>
                <li>Uploaded bytes: held only while a transfer lab session exists. Database records and logs: kept until the operator deletes them.
                    <Unset what="retention period for database rows and logs" /></li>
            </UL>

            <H>Your rights and complaints</H>
            <p>
                You can ask the operator to tell you what personal data they hold about you, correct it, or erase it, and withdraw
                consent (the footer preferences link withdraws it for the optional content at any time). Write to the privacy contact above.
                Complaints go to the Grievance Officer above; if unresolved you may approach the Data Protection Board of India once it is
                operational for your case.
            </p>

            <H>Open items (for the operator and a qualified Indian lawyer)</H>
            <UL>
                <li>Confirm whether the DPDP Act, 2023 and its Rules apply to your deployment and which commencement dates are in force, and whether
                    you are a Data Fiduciary for visitors.</li>
                <li>Fill in the operator details, retention periods and hosting-log practices above.</li>
                <li>If you add accounts, forms, analytics or payments, this policy must be rewritten first.</li>
            </UL>
        </Page>
    );
}

export function TermsPage() {
    return (
        <Page title="Terms of Use">
            <H>Operator</H>
            <Operator />
            <H>What this is</H>
            <p>
                NetRescue is an educational and research tool for experimenting with failure detection, multipath transfer, routing,
                TCP behaviour and network diagnostics. Many results are produced by a seeded simulator; pages label results as real,
                simulated or mixed. Do not rely on them for production network decisions.
            </p>
            <H>Your responsibilities</H>
            <UL>
                <li>Only probe, trace or send traffic to hosts and networks you own or are authorised to test.</li>
                <li>Only upload files you have the right to use, and do not upload sensitive personal data.</li>
                <li>Do not attempt to disrupt, overload or gain unauthorised access to the service or others&apos; systems.</li>
            </UL>
            <H>Payments, refunds and cancellation</H>
            <p>This site is free and has no paid plans, subscriptions or payment processing, so there is nothing to refund or cancel.</p>
            <H>Intellectual property</H>
            <p>
                The project&apos;s licence is not yet declared in the repository: <Unset what="software licence" />. Third-party
                content (YouTube videos, background videos) belongs to its owners and is shown only as embedded or linked.
            </p>
            <H>No warranty and liability</H>
            <p>
                The service is provided &ldquo;as is&rdquo;, without warranties of any kind, to the extent the law allows. Nothing here limits
                rights you have under law that cannot be excluded, including under the Consumer Protection Act, 2019 where it applies.
            </p>
            <H>Changes, termination, disputes</H>
            <p>
                The operator may change or suspend the service and these terms. Governing law and forum:{' '}
                <Unset what="governing law and jurisdiction, to be set with legal advice" />.
            </p>
        </Page>
    );
}

export function CookiesPage() {
    const { openPanel, consent } = useConsent();
    return (
        <Page title="Cookie & Third-party Policy">
            <H>Cookies</H>
            <p>This site sets no cookies of its own and uses no analytics, advertising or tracking technology.</p>
            <H>Browser storage</H>
            <p>One local-storage entry, <code>netrescue.consent.v1</code>, remembers your choices below. It is strictly functional and stays on your device.</p>
            <H>Third-party content</H>
            <UL>
                <li><strong>Background videos</strong> are loaded from a CloudFront-hosted address (Amazon), which sees your IP address and browser details when they are requested. They are skipped if your system asks for reduced motion.</li>
                <li><strong>YouTube explainer videos</strong> via youtube-nocookie.com. Currently {consent.youtube ? 'enabled' : 'off'}. YouTube may still
                    store data on your device or read it once the player loads; see Google&apos;s policies.</li>
                <li><strong>Reference links</strong> to RFCs, Microsoft Learn and man pages are ordinary links; nothing loads until you click.</li>
            </UL>
            <button onClick={openPanel}
                className="mt-2 px-4 py-2 rounded-lg border border-white/40 text-[13px] hover:bg-white/10">
                Change third-party preferences
            </button>
        </Page>
    );
}
