import React, { useState, useEffect, useRef } from 'react';
import { Link } from 'react-router-dom';
import { motion, useScroll, useSpring, useTransform, useMotionTemplate } from 'framer-motion';
import VideoBackground from '../components/VideoBackground';
import { ScrambleIn } from '../components/ScrambleText';
import { features, VIDEO_URLS } from '../data/features';

const fadeUp = (delay = 0) => ({
    initial: { opacity: 0, y: 25 },
    whileInView: { opacity: 1, y: 0 },
    transition: { duration: 0.9, delay, ease: [0.215, 0.610, 0.355, 1.0] as [number, number, number, number] },
    viewport: { once: true, amount: 0.3 },
});

const METRICS = [
    { value: '< 3s', label: 'Failure Detection (target)' },
    { value: '≥ 50%', label: 'Route Flap Reduction (target)' },
    { value: 'SHA-256', label: 'Transfer Integrity' },
    { value: 'Real', label: 'Host Diagnostics' },
    { value: '5×', label: 'Repeatability Runs' },
    { value: 'Local', label: 'Runs on Your Host' },
];

function HeroSection() {
    const [entranceComplete, setEntranceComplete] = useState(false);
    useEffect(() => { const t = setTimeout(() => setEntranceComplete(true), 800); return () => clearTimeout(t); }, []);

    return (
        <section className="relative h-screen h-[100dvh] min-h-[600px] flex flex-col">
            <VideoBackground src={VIDEO_URLS.hero} scrub overlay="bg-black/20" />
            {/* Dot grid */}
            <div className="absolute inset-0 pointer-events-none opacity-[0.05]"
                style={{ backgroundImage: 'radial-gradient(#ffffff 1px, transparent 1px)', backgroundSize: '24px 24px' }} />
            {/* Watermark */}
            <div className="absolute inset-0 flex items-center justify-center pointer-events-none" style={{ paddingTop: '50px' }}>
                <span className="uppercase tracking-[-4px] opacity-[0.10] select-none"
                    style={{
                        fontFamily: '"Anton SC", sans-serif',
                        fontSize: 'clamp(120px, 30vw, 521px)',
                        background: 'radial-gradient(circle, rgba(142,127,148,0) 0%, #8E7F94 70%)',
                        WebkitBackgroundClip: 'text',
                        backgroundClip: 'text',
                        WebkitTextFillColor: 'transparent',
                    }}>
                    RESILIENCE
                </span>
            </div>

            {/* Content */}
            <motion.div
                className="relative z-10 flex-1 flex flex-col px-4 sm:px-6 md:px-8 pt-20 sm:pt-24 pb-8 sm:pb-12"
                initial={{ opacity: 0 }}
                animate={{ opacity: entranceComplete ? 1 : 0 }}
                transition={{ duration: 1 }}
            >
                <div className="flex-1" />
                <div className="flex flex-col gap-6 md:flex-row md:items-end md:justify-between">
                    <div className="flex flex-col gap-4">
                        <h1 className="text-white font-light leading-[0.95] tracking-[-0.03em] text-[clamp(40px,10vw,100px)]">
                            <ScrambleIn text="Self-Healing" delay={200} triggered={entranceComplete} /><br />
                            <ScrambleIn text="Networks" delay={500} triggered={entranceComplete} />
                        </h1>
                        <motion.p {...fadeUp(0.2)} className="max-w-sm text-[13px] sm:text-[15px] text-white/60 leading-relaxed">
                            A software-defined overlay that recovers from node failure and schedules multipath transfers with algorithms drawn from published networking research.
                        </motion.p>
                    </div>
                    <div aria-hidden="true" className="text-white font-light leading-[0.95] tracking-[-0.03em] text-[clamp(40px,10vw,100px)] text-left md:text-right">
                        <ScrambleIn text="One" delay={700} triggered={entranceComplete} /><br />
                        <ScrambleIn text="System" delay={1000} triggered={entranceComplete} />
                    </div>
                </div>
            </motion.div>
        </section>
    );
}

function CinematicSection() {
    const ref = useRef(null);
    const { scrollYProgress } = useScroll({ target: ref, offset: ['start end', 'end start'] });
    const smooth = useSpring(scrollYProgress, { stiffness: 15, damping: 32, mass: 1.8 });
    const yScaleValue = useTransform(smooth, [0, 1], [60, -120]);
    const opacity = useTransform(smooth, [0.3, 0.5], [0, 1]);
    const transform = useMotionTemplate`rotateX(24deg) translateY(${yScaleValue}px) translateZ(15px)`;

    return (
        <section ref={ref} className="relative h-screen h-[100dvh] flex items-center justify-center">
            <VideoBackground src={VIDEO_URLS.cinematic} overlay="bg-black/50" />
            <div className="absolute top-0 left-0 right-0 h-[180px] z-10"
                style={{ background: 'linear-gradient(to bottom, #010103, transparent)' }} />
            <div className="relative z-20 max-w-5xl" style={{ perspective: 400 }}>
                <motion.p
                    className="font-sans font-normal text-[22px] sm:text-[30px] md:text-[36px] lg:text-[42px] text-white leading-[1.35] tracking-[-0.02em] select-none px-6 sm:px-12 text-center"
                    style={{ transform, opacity }}
                >
                    A network resilience lab that implements ideas from published networking research. NetRescue translates routing instability into measurable recovery. Every heartbeat, every chunk, every failure becomes observable. The system continuously rebuilds forwarding state as topology changes.
                </motion.p>
            </div>
        </section>
    );
}

function MetricsSection() {
    return (
        <section id="metrics" className="relative min-h-screen min-h-[100dvh] flex flex-col items-center justify-center pt-32 pb-32 px-6">
            <VideoBackground src={VIDEO_URLS.metrics} overlay="bg-black/60" />
            <motion.p className="relative z-10 text-white/40 text-[13px] sm:text-[14px] tracking-[0.2em] uppercase mb-20 text-center"
                initial={{ opacity: 0 }} whileInView={{ opacity: 1 }} transition={{ duration: 1.2 }} viewport={{ once: true, amount: 0.3 }}>
                Design Targets (verify in the labs)
            </motion.p>
            <div className="relative z-10 grid grid-cols-1 md:grid-cols-3 gap-16 md:gap-8 max-w-6xl w-full">
                {METRICS.map((m, i) => (
                    <motion.div key={m.label}
                        initial={{ opacity: 0, y: 30 }}
                        whileInView={{ opacity: 1, y: 0 }}
                        transition={{ duration: 0.8, delay: i * 0.15 }}
                        viewport={{ once: true, amount: 0.3 }}
                        className="text-center"
                    >
                        <div className="text-white font-light tracking-[-0.04em] leading-none text-[clamp(48px,10vw,96px)]">
                            {m.value}
                        </div>
                        <div className="text-white/40 text-[13px] sm:text-[15px] mt-4 tracking-wide">{m.label}</div>
                    </motion.div>
                ))}
            </div>
        </section>
    );
}

function FeaturesGrid() {
    return (
        <section className="relative min-h-screen min-h-[100dvh] flex flex-col px-8 sm:px-12 md:px-16 py-12 sm:py-16">
            <VideoBackground src={VIDEO_URLS.features} overlay="bg-black/60" />
            <div className="relative z-10 flex flex-col md:flex-row md:justify-between md:items-start gap-6">
                <motion.h2
                    initial={{ opacity: 0, y: 40 }}
                    whileInView={{ opacity: 1, y: 0 }}
                    transition={{ duration: 1.0 }}
                    viewport={{ once: true, amount: 0.3 }}
                    className="text-white font-light leading-[0.95] tracking-[-0.03em] text-[clamp(36px,8vw,72px)]"
                >
                    Four<br />Features
                </motion.h2>
                <motion.p
                    initial={{ opacity: 0, y: 20 }}
                    whileInView={{ opacity: 1, y: 0 }}
                    transition={{ duration: 1.0, delay: 0.2 }}
                    viewport={{ once: true, amount: 0.3 }}
                    className="text-white/50 text-[13px] sm:text-[15px] leading-relaxed max-w-xs md:text-right md:pt-2"
                >
                    Each feature is independently demonstrable, based on published algorithms, and checked with real host measurements plus a deterministic simulator. Click any feature to open its full specification.
                </motion.p>
            </div>

            <div className="flex-1" />

            <motion.div
                className="relative z-10 grid grid-cols-2 md:grid-cols-4 gap-8 md:gap-6 mt-16"
                initial={{ opacity: 0 }}
                whileInView={{ opacity: 1 }}
                transition={{ duration: 1.0, delay: 0.3 }}
                viewport={{ once: true, amount: 0.3 }}
            >
                {features.map((f, i) => (
                    <motion.div key={f.id}
                        initial={{ opacity: 0, y: 20 }}
                        whileInView={{ opacity: 1, y: 0 }}
                        transition={{ duration: 0.7, delay: i * 0.1 }}
                        viewport={{ once: true, amount: 0.3 }}
                    >
                        <Link
                            to={`/features/${f.id}`}
                            className="group block h-full transition-opacity hover:opacity-80"
                        >
                            <h3 className="text-white text-[14px] sm:text-[16px] font-normal mb-2">
                                {f.title} <span className="text-white/60 group-hover:text-white transition-colors">→</span>
                            </h3>
                            <p className="text-white/40 text-[12px] sm:text-[14px] leading-relaxed">{f.tagline}</p>
                        </Link>
                    </motion.div>
                ))}
            </motion.div>
        </section>
    );
}

function ArchitectureSection() {
    const layers = [
        { label: 'Layer 1', desc: 'Control Plane' },
        { label: 'Layer 2', desc: 'Forwarding Plane' },
        { label: 'Layer 3', desc: 'Diagnostics & Simulation' },
    ];

    return (
        <section id="architecture" className="min-h-screen min-h-[100dvh] flex items-center justify-center bg-black px-6 py-32">
            <div className="max-w-3xl text-center">
                <motion.p {...fadeUp()} className="text-white/40 tracking-[0.2em] uppercase mb-8 text-[13px] sm:text-[14px]">
                    Architecture
                </motion.p>
                <motion.h2 {...fadeUp(0.1)} className="text-white font-light leading-[1.15] tracking-[-0.02em] mb-10 text-[clamp(28px,6vw,56px)]">
                    Three layers. Runs locally.
                </motion.h2>
                <motion.p {...fadeUp(0.2)} className="text-white/45 text-[15px] sm:text-[17px] leading-relaxed max-w-xl mx-auto">
                    Control plane decides. Forwarding plane moves. Diagnostics and a seeded simulator run on the host, with no kernel facility and no admin rights. PostgreSQL persists everything the run produces.
                </motion.p>
                <motion.div className="mt-20 flex flex-col gap-4 items-center"
                    initial={{ opacity: 0 }} whileInView={{ opacity: 1 }} transition={{ duration: 1.2, delay: 0.4 }} viewport={{ once: true, amount: 0.4 }}>
                    {layers.map(l => (
                        <div key={l.label} className="max-w-md w-full h-[72px] border border-white/10 rounded-lg flex items-center justify-between px-6">
                            <span className="text-white/30 text-[12px] tracking-[0.15em] uppercase">{l.label}</span>
                            <span className="text-white text-[16px] sm:text-[18px] font-light">{l.desc}</span>
                        </div>
                    ))}
                </motion.div>
            </div>
        </section>
    );
}

export default function LandingPage() {
    useEffect(() => { document.title = 'NetRescue — Self-Healing Networks'; }, []);
    return (
        <motion.main initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}
            transition={{ type: 'spring', stiffness: 260, damping: 26, duration: 0.35 }}>
            <HeroSection />
            <CinematicSection />
            <MetricsSection />
            <FeaturesGrid />
            <ArchitectureSection />
        </motion.main>
    );
}
