import React, { useRef, useEffect } from 'react';

/** Fraction of the clip one full viewport width of mouse travel scrubs through. */
const SCRUB_SENSITIVITY = 0.8;

interface VideoBackgroundProps {
    src: string;
    className?: string;
    overlay?: string;
    scrub?: boolean;
}

export default function VideoBackground({
    src,
    className = '',
    overlay = 'bg-black/40',
    scrub = false,
}: VideoBackgroundProps) {
    const videoRef = useRef<HTMLVideoElement>(null);
    // Visitors who ask for reduced motion get the overlay only; no video is requested.
    const reduceMotion = typeof window !== 'undefined' && !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    const enabled = !reduceMotion;

    useEffect(() => {
        if (!scrub || !enabled) return;
        const video = videoRef.current;
        if (!video) return;

        video.pause();

        // Horizontal mouse travel (delta, not absolute position) moves the playhead.
        // Seeks are chained on `seeked` so a fast drag never queues dropped frames.
        let target = video.currentTime;
        let lastX: number | null = null;
        let seeking = false;

        const seek = () => {
            if (seeking || !video.duration) return;
            if (Math.abs(video.currentTime - target) < 0.001) return;
            seeking = true;
            video.currentTime = target;
        };
        const handleSeeked = () => { seeking = false; seek(); };
        const handleMouse = (e: MouseEvent) => {
            if (!video.duration) return;
            if (lastX !== null) {
                const delta = (e.clientX - lastX) / window.innerWidth;
                target = Math.min(video.duration, Math.max(0, target + delta * video.duration * SCRUB_SENSITIVITY));
                seek();
            }
            lastX = e.clientX;
        };

        video.addEventListener('seeked', handleSeeked);
        window.addEventListener('mousemove', handleMouse);
        return () => {
            window.removeEventListener('mousemove', handleMouse);
            video.removeEventListener('seeked', handleSeeked);
        };
    }, [scrub, enabled]);

    return (
        <div className={`absolute inset-0 overflow-hidden ${className}`}>
            {enabled && <video
                ref={videoRef}
                src={src}
                className="w-full h-full object-cover"
                autoPlay={!scrub}
                loop={!scrub}
                muted
                playsInline
                preload="auto"
                aria-hidden="true"
            />}
            <div className={`absolute inset-0 ${overlay}`} />
        </div>
    );
}
