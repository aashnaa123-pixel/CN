import React, { useEffect } from 'react';
import { Link } from 'react-router-dom';
import { motion } from 'framer-motion';
import VideoBackground from '../components/VideoBackground';
import { ScrambleIn } from '../components/ScrambleText';
import { features, VIDEO_URLS } from '../data/features';
import { ArrowUpRight } from 'lucide-react';

const COUNT_WORDS: Record<number, string> = { 1: 'One', 2: 'Two', 3: 'Three', 4: 'Four', 5: 'Five', 6: 'Six', 7: 'Seven' };

export default function FeaturesIndexPage() {
    useEffect(() => { document.title = 'Features — NetRescue'; }, []);

    return (
        <motion.main
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            transition={{ type: 'spring', stiffness: 260, damping: 26, duration: 0.35 }}
        >
            {/* Hero band */}
            <section className="relative flex items-center justify-center" style={{ height: '60vh', minHeight: '420px' }}>
                <VideoBackground src={VIDEO_URLS.features} overlay="bg-black/60" />
                {/* Watermark */}
                <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
                    <span className="uppercase opacity-[0.08] select-none"
                        style={{
                            fontFamily: '"Anton SC", sans-serif',
                            fontSize: 'clamp(100px, 22vw, 400px)',
                            letterSpacing: '-2px',
                        }}>
                        FEATURES
                    </span>
                </div>
                <div className="relative z-10 text-center px-6">
                    {/* Breadcrumb */}
                    <p className="text-white/40 text-[13px] mb-6">
                        <Link to="/" className="hover:text-white/60 transition-colors">Home</Link>
                        <span className="mx-2">/</span>
                        <span>Features</span>
                    </p>
                    <h1 className="font-light leading-[0.95] tracking-[-0.03em]" style={{ fontSize: 'clamp(36px, 8vw, 72px)' }}>
                        {COUNT_WORDS[features.length] ?? features.length}<br />Features
                    </h1>
                    <p className="text-white/50 text-[15px] sm:text-[17px] max-w-xl mx-auto mt-6">
                        Each independently demonstrable. Each labelled real or simulated.
                    </p>
                </div>
            </section>

            {/* Feature rows */}
            <section className="max-w-6xl mx-auto px-4 sm:px-6 md:px-8 py-8">
                {features.map((f, i) => (
                    <Link key={f.id} to={`/features/${f.id}`} className="block group">
                        <motion.div
                            className="grid grid-cols-1 md:grid-cols-[120px_1fr_auto] gap-6 md:gap-12 py-16 border-t border-white/10"
                            initial={{ opacity: 0, y: 20 }}
                            whileInView={{ opacity: 1, y: 0 }}
                            transition={{ duration: 0.7, delay: i * 0.1 }}
                            viewport={{ once: true, amount: 0.3 }}
                        >
                            {/* Index */}
                            <div className="text-white/60 text-[14px] tracking-[0.2em]">
                                <ScrambleIn text={`0${i + 1}`} delay={200 + i * 200} />
                            </div>

                            {/* Content */}
                            <div>
                                <h2 className="text-[20px] sm:text-[24px] font-light tracking-[-0.02em] mb-2 group-hover:text-white/80 transition-colors">
                                    {f.title}
                                </h2>
                                <p className="text-white/50 text-[14px] mb-4">{f.tagline}</p>
                                <ul className="space-y-1.5">
                                    {f.highlights.map((h, j) => (
                                        <li key={j} className="text-white/40 text-[13px] leading-relaxed">
                                            • {h}
                                        </li>
                                    ))}
                                </ul>
                            </div>

                            {/* Arrow */}
                            <div className="flex items-start pt-1">
                                <span className="text-white text-[13px] tracking-[0.15em] uppercase flex items-center gap-1 group-hover:text-white/70 transition-colors">
                                    Open <ArrowUpRight size={14} />
                                </span>
                            </div>
                        </motion.div>
                    </Link>
                ))}
            </section>

        </motion.main>
    );
}
