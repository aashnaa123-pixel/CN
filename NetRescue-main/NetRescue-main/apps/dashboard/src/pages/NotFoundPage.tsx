import React, { useEffect } from 'react';
import { Link } from 'react-router-dom';
import { motion } from 'framer-motion';

export default function NotFoundPage() {
    useEffect(() => { document.title = '404 — NetRescue'; }, []);
    return (
        <motion.main
            className="min-h-screen flex flex-col items-center justify-center px-6"
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            transition={{ type: 'spring', stiffness: 260, damping: 26 }}
        >
            <h1 className="font-light tracking-[-0.04em] mb-4" style={{ fontSize: 'clamp(60px, 12vw, 120px)' }}>
                404
            </h1>
            <p className="text-white/50 text-[15px] mb-8">
                Node unreachable. No alternate route available.
            </p>
            <Link
                to="/"
                className="px-6 py-3 border border-white/20 rounded-lg text-[14px] hover:bg-white/5 transition-colors"
            >
                Return to origin
            </Link>
        </motion.main>
    );
}
