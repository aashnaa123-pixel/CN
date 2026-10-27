import React, { useState, useEffect, useRef, useCallback } from 'react';

interface ScrambleTextProps {
    text: string;
    className?: string;
    /** When given, the parent drives the scramble; otherwise the text reacts to its own hover. */
    isHovered?: boolean;
}

const CHARS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789!@#$%^&*()_+~|}{[]:;?><';

export function ScrambleText({ text, className = '', isHovered }: ScrambleTextProps) {
    const [display, setDisplay] = useState(text);
    const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

    const scramble = useCallback(() => {
        let iteration = 0;
        if (intervalRef.current) clearInterval(intervalRef.current);
        intervalRef.current = setInterval(() => {
            setDisplay(
                text
                    .split('')
                    .map((char, i) => {
                        if (char === ' ') return ' ';
                        if (i < iteration) return text[i];
                        return CHARS[Math.floor(Math.random() * CHARS.length)];
                    })
                    .join('')
            );
            iteration += 1 / 4;
            if (iteration >= text.length) {
                if (intervalRef.current) clearInterval(intervalRef.current);
                setDisplay(text);
            }
        }, 25);
    }, [text]);

    const reset = useCallback(() => {
        if (intervalRef.current) clearInterval(intervalRef.current);
        setDisplay(text);
    }, [text]);

    useEffect(() => {
        if (isHovered === undefined) return;
        if (isHovered) scramble(); else reset();
    }, [isHovered, scramble, reset]);

    useEffect(() => () => { if (intervalRef.current) clearInterval(intervalRef.current); }, []);

    const controlled = isHovered !== undefined;
    return (
        <span
            className={className}
            onMouseEnter={controlled ? undefined : scramble}
            onMouseLeave={controlled ? undefined : reset}
            style={{ cursor: 'default' }}
        >
            {display}
        </span>
    );
}

interface ScrambleInProps {
    text: string;
    className?: string;
    /** Milliseconds to wait after `triggered` before the reveal starts. */
    delay?: number;
    triggered?: boolean;
}

export function ScrambleIn({ text, className = '', delay = 0, triggered = true }: ScrambleInProps) {
    const [display, setDisplay] = useState<string>('');
    const [started, setStarted] = useState(false);

    useEffect(() => {
        if (!triggered) { setStarted(false); return; }
        const timer = setTimeout(() => setStarted(true), delay);
        return () => clearTimeout(timer);
    }, [delay, triggered]);

    useEffect(() => {
        if (!started) return;
        let iteration = 0;
        const interval = setInterval(() => {
            setDisplay(
                text
                    .split('')
                    .map((char, i) => {
                        if (char === ' ') return ' ';
                        if (i < iteration) return text[i];
                        if (i < iteration + 3) return CHARS[Math.floor(Math.random() * CHARS.length)];
                        return ' ';
                    })
                    .join('')
            );
            iteration += 0.5;
            if (iteration >= text.length) {
                clearInterval(interval);
                setDisplay(text);
            }
        }, 25);
        return () => clearInterval(interval);
    }, [started, text]);

    return (
        <span className={className}>
            {started ? display : ' '}
        </span>
    );
}
