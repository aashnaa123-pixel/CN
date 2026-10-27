import React, { useEffect } from 'react';
import { Outlet, useLocation } from 'react-router-dom';
import { AnimatePresence } from 'framer-motion';
import Navbar from './Navbar';
import Footer from './Footer';
import { ConsentProvider } from '../consent/ConsentContext';
import ConsentPanel from '../consent/ConsentPanel';

function ScrollToTop() {
    const { pathname } = useLocation();
    useEffect(() => {
        window.scrollTo(0, 0);
    }, [pathname]);
    return null;
}

export default function Layout() {
    const location = useLocation();
    return (
        <ConsentProvider>
            <a href="#main" onClick={e => {
                e.preventDefault();
                const m = document.querySelector('main');
                if (m) { m.setAttribute('tabindex', '-1'); (m as HTMLElement).focus(); }
            }} className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-[200] focus:bg-white focus:text-black focus:px-3 focus:py-2 focus:rounded">Skip to main content</a>
            <ScrollToTop />
            <Navbar />
            <AnimatePresence mode="wait">
                <Outlet key={location.pathname} />
            </AnimatePresence>
            <Footer />
            <ConsentPanel />
        </ConsentProvider>
    );
}
