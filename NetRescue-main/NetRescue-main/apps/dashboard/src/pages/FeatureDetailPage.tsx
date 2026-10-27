import { useConsent } from '../consent/ConsentContext';
import React, { useEffect, useState, useRef } from 'react';
import { useParams, Link, useNavigate } from 'react-router-dom';
import { motion } from 'framer-motion';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import VideoBackground from '../components/VideoBackground';
import NetRescueLogo from '../components/NetRescueLogo';
import { ScrambleIn, ScrambleText } from '../components/ScrambleText';
import { getFeature, getAdjacent, features } from '../data/features';
import type { FeatureSection } from '../data/features';
import { ExperimentWorkflow, FeatureModePanel } from '../labs/ui';
import { FEATURE_MODE, FEATURE_WORKFLOW } from '../labs/workflows';
import NotFoundPage from './NotFoundPage';

function YouTubeEmbed({ videoId, title }: { videoId: string; title: string }) {
    const { consent, set } = useConsent();
    return (
        <div className="relative w-full aspect-video rounded-lg overflow-hidden border border-white/10 bg-black">
            {consent.youtube ? (
                <iframe
                    className="absolute inset-0 w-full h-full"
                    src={`https://www.youtube-nocookie.com/embed/${videoId}`}
                    title={title}
                    allow="encrypted-media; picture-in-picture; web-share"
                    referrerPolicy="strict-origin-when-cross-origin"
                    allowFullScreen
                />
            ) : (
                <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 p-4 text-center">
                    <p className="text-[13px] text-white/70 max-w-sm">
                        This video is hosted by YouTube (Google). Loading it shares your IP address with Google.
                    </p>
                    <button onClick={() => set('youtube', true)}
                        className="px-4 py-2 rounded-lg border border-white/40 text-[13px] hover:bg-white/10">
                        Load video: {title}
                    </button>
                </div>
            )}
        </div>
    );
}

export default function FeatureDetailPage() {
    const { id } = useParams<{ id: string }>();
    const navigate = useNavigate();
    const feature = getFeature(id || '');
    const { prev, next } = getAdjacent(id || '');
    const [activeSection, setActiveSection] = useState('purpose');
    const sectionRefs = useRef<Record<string, HTMLElement | null>>({});

    useEffect(() => {
        if (feature) document.title = `${feature.title} — NetRescue`;
    }, [feature]);

    // IntersectionObserver for sidebar active state
    useEffect(() => {
        if (!feature) return;
        const observers: IntersectionObserver[] = [];
        feature.sections.forEach(s => {
            const el = sectionRefs.current[s.id];
            if (!el) return;
            const observer = new IntersectionObserver(
                entries => {
                    entries.forEach(entry => {
                        if (entry.isIntersecting) setActiveSection(s.id);
                    });
                },
                { threshold: 0.3, rootMargin: '-100px 0px -60% 0px' }
            );
            observer.observe(el);
            observers.push(observer);
        });
        return () => observers.forEach(o => o.disconnect());
    }, [feature, id]);

    if (!feature) return <NotFoundPage />;

    return (
        <motion.main
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            transition={{ type: 'spring', stiffness: 260, damping: 26, duration: 0.35 }}
        >
            {/* Sticky header */}
            <header className="fixed top-0 left-0 right-0 z-40 h-16 flex items-center justify-between px-4 sm:px-6 md:px-8 bg-black/60 backdrop-blur-md border-b border-white/10">
                <div className="flex items-center gap-3">
                    <Link to="/" className="text-white/70 hover:text-white transition-colors"><NetRescueLogo size={18} /></Link>
                    <span className="text-white/40 text-[13px]">
                        <Link to="/" className="hover:text-white/60">Home</Link>
                        <span className="mx-1">/</span>
                        <Link to="/features" className="hover:text-white/60">Features</Link>
                        <span className="mx-1">/</span>
                        <span className="text-white/70">{feature.title}</span>
                    </span>
                </div>
                <div className="flex items-center gap-3">
                    <button
                        onClick={() => prev && navigate(`/features/${prev.id}`)}
                        disabled={!prev}
                        className="text-white/40 hover:text-white disabled:opacity-20 transition-colors"
                        aria-label="Previous feature"
                    >
                        <ChevronLeft size={20} />
                    </button>
                    <button
                        onClick={() => next && navigate(`/features/${next.id}`)}
                        disabled={!next}
                        className="text-white/40 hover:text-white disabled:opacity-20 transition-colors"
                        aria-label="Next feature"
                    >
                        <ChevronRight size={20} />
                    </button>
                </div>
            </header>

            {/* Hero band */}
            <section className="relative flex items-end" style={{ height: '50vh', minHeight: '360px' }}>
                <VideoBackground src={feature.heroVideoUrl} overlay="bg-black/65" />
                {/* Watermark letter */}
                <div className="absolute left-8 inset-y-0 flex items-center pointer-events-none">
                    <span className="opacity-[0.10] select-none"
                        style={{
                            fontFamily: '"Anton SC", sans-serif',
                            fontSize: 'clamp(160px, 30vw, 480px)',
                            lineHeight: 1,
                        }}>
                        {feature.watermark}
                    </span>
                </div>
                <div className="relative z-10 px-4 sm:px-6 md:px-8 pb-12 pt-20">
                    <div className="flex flex-col gap-6 md:flex-row md:items-end md:justify-between">
                        <div>
                            <h1 className="font-light leading-[0.95] tracking-[-0.03em]" style={{ fontSize: 'clamp(36px, 7vw, 72px)' }}>
                                <ScrambleIn text={feature.title} delay={200} />
                            </h1>
                            <motion.p
                                className="text-white/50 text-[15px] sm:text-[17px] max-w-xl mt-4"
                                initial={{ opacity: 0, y: 20 }}
                                animate={{ opacity: 1, y: 0 }}
                                transition={{ delay: 0.3, duration: 0.7 }}
                            >
                                {feature.heroSub}
                            </motion.p>
                        </div>
                        {/* Features with something to build or try. Each opens its own playground. */}
                        {feature.id === 'feature-a' && (
                            <Link
                                to="/features/hysteresis/playground"
                                className="shrink-0 h-12 px-6 bg-white text-black rounded-full inline-flex items-center gap-2 text-[14px] font-medium self-start md:self-end hover:bg-white/90 transition-colors"
                            >
                                Build &amp; Test Network
                                <span aria-hidden="true">→</span>
                            </Link>
                        )}
                        {feature.id === 'feature-c' && (
                            <Link
                                to="/features/multipath/playground"
                                className="shrink-0 h-12 px-6 bg-white text-black rounded-full inline-flex items-center gap-2 text-[14px] font-medium self-start md:self-end hover:bg-white/90 transition-colors"
                            >
                                Try Interactive Demo
                                <span aria-hidden="true">→</span>
                            </Link>
                        )}
                        {feature.lab && (
                            <Link
                                to={feature.lab.to}
                                className="shrink-0 h-12 px-6 bg-white text-black rounded-full inline-flex items-center gap-2 text-[14px] font-medium self-start md:self-end hover:bg-white/90 transition-colors"
                            >
                                {feature.lab.label}
                                <span aria-hidden="true">→</span>
                            </Link>
                        )}
                        {feature.id === 'feature-d' && (
                            <Link
                                to="/features/diagnostics/playground"
                                className="shrink-0 h-12 px-6 bg-white text-black rounded-full inline-flex items-center gap-2 text-[14px] font-medium self-start md:self-end hover:bg-white/90 transition-colors"
                            >
                                Try Interactive Demo
                                <span aria-hidden="true">→</span>
                            </Link>
                        )}
                    </div>
                </div>
            </section>

            {/* How to run this experiment comes before any explanation. */}
            {FEATURE_WORKFLOW[feature.id] && (
                <section className="max-w-6xl mx-auto px-4 sm:px-6 md:px-8 pt-10 space-y-4" data-testid="detail-run-guide">
                    <ExperimentWorkflow id={FEATURE_WORKFLOW[feature.id]} />
                    {FEATURE_MODE[feature.id] && <FeatureModePanel feature={FEATURE_MODE[feature.id]} />}
                </section>
            )}

            {/* Content grid: sidebar + content */}
            <section className="max-w-6xl mx-auto px-4 sm:px-6 md:px-8 py-16">
                <div className="grid grid-cols-1 md:grid-cols-[200px_1fr] gap-8 md:gap-12">
                    {/* Sidebar — desktop only */}
                    <nav className="hidden md:block w-48 sticky top-24 self-start border-l border-white/10 pl-6">
                        <ul className="space-y-4">
                            {feature.sections.map(s => (
                                <li key={s.id}>
                                    <a
                                        href={`#${s.id}`}
                                        className={`block text-[13px] transition-colors ${activeSection === s.id
                                                ? 'text-white border-l-2 border-white -ml-[calc(1.5rem+1px)] pl-[calc(1.5rem-1px)]'
                                                : 'text-white/40 hover:text-white/70'
                                            }`}
                                    >
                                        {s.heading}
                                    </a>
                                </li>
                            ))}
                        </ul>
                    </nav>

                    {/* Mobile pill bar */}
                    <div className="md:hidden flex gap-2 overflow-x-auto pb-4 -mx-4 px-4">
                        {feature.sections.map(s => (
                            <a key={s.id} href={`#${s.id}`}
                                className={`whitespace-nowrap text-[12px] px-3 py-1.5 rounded-full border transition-colors ${activeSection === s.id ? 'border-white/30 text-white' : 'border-white/10 text-white/40'
                                    }`}>
                                {s.heading}
                            </a>
                        ))}
                    </div>

                    {/* Content */}
                    <div className="max-w-3xl">
                        {feature.sections.map(s => (
                            <div
                                key={s.id}
                                id={s.id}
                                ref={el => { sectionRefs.current[s.id] = el; }}
                                className="mb-16 scroll-mt-24"
                            >
                                <h2 className="text-white text-[22px] sm:text-[28px] font-light tracking-[-0.02em] mb-6">
                                    {s.heading}
                                </h2>
                                {s.body && (
                                    <p className="text-white/60 text-[15px] sm:text-[16px] leading-relaxed mb-6">{s.body}</p>
                                )}
                                {s.table && (
                                    <div className="w-full border border-white/10 rounded-lg overflow-hidden mb-6">
                                        <table className="w-full">
                                            <thead>
                                                <tr className="bg-white/[0.03]">
                                                    {s.table.columns.map(col => (
                                                        <th key={col} className="text-left text-white/70 px-4 py-3 text-[14px] font-normal">
                                                            {col}
                                                        </th>
                                                    ))}
                                                </tr>
                                            </thead>
                                            <tbody>
                                                {s.table.rows.map((row, ri) => (
                                                    <tr key={ri} className="border-t border-white/10">
                                                        {row.map((cell, ci) => (
                                                            <td key={ci} className="text-white/50 px-4 py-3 text-[14px]">{cell}</td>
                                                        ))}
                                                    </tr>
                                                ))}
                                            </tbody>
                                        </table>
                                    </div>
                                )}
                                {s.bullets && (
                                    <ul className="list-disc pl-6 text-white/50 space-y-2 text-[14px] leading-relaxed">
                                        {s.bullets.map((b, bi) => <li key={bi}>{b}</li>)}
                                    </ul>
                                )}
                                {/* Supporting video. Loaded from youtube-nocookie so
                                    nothing is requested until the user scrolls to it and
                                    no tracking cookie is set. */}
                                {s.videoId && (
                                    <figure className="mt-8">
                                        <YouTubeEmbed videoId={s.videoId} title={s.videoTitle ?? 'Embedded video'} />
                                        <figcaption className="mt-3 text-[13px] text-white/40 leading-relaxed">
                                            {s.videoTitle}
                                            {s.videoAuthor && <> &mdash; {s.videoAuthor}</>}
                                            {' \u00b7 '}
                                            <a
                                                href={`https://www.youtube.com/watch?v=${s.videoId}`}
                                                target="_blank"
                                                rel="noreferrer"
                                                className="text-white/60 hover:text-white underline underline-offset-2 transition-colors"
                                            >
                                                Watch on YouTube
                                            </a>
                                        </figcaption>
                                    </figure>
                                )}
                            </div>
                        ))}

                        {/* Research Anchor */}
                        {feature.researchAnchor.length > 0 && (
                            <div className="mb-16">
                                <h3 className="text-white/60 text-[14px] tracking-[0.1em] uppercase mb-4">Sources</h3>
                                <ul className="space-y-2">
                                    {feature.researchAnchor.map((r, i) => (
                                        <li key={i} className="text-white/40 text-[13px]">• {r}</li>
                                    ))}
                                </ul>
                            </div>
                        )}

                        {/* Prev / Next */}
                        <div className="mt-20 pt-8 border-t border-white/10 flex flex-col sm:flex-row justify-between gap-4">
                            {prev ? (
                                <Link to={`/features/${prev.id}`} className="text-white/50 hover:text-white/70 transition-colors text-[14px]">
                                    ← Previous: {prev.title}
                                </Link>
                            ) : <div />}
                            {next ? (
                                <Link to={`/features/${next.id}`} className="text-white/50 hover:text-white/70 transition-colors text-[14px] sm:text-right">
                                    Next: {next.title} →
                                </Link>
                            ) : <div />}
                        </div>
                    </div>
                </div>
            </section>
        </motion.main>
    );
}
