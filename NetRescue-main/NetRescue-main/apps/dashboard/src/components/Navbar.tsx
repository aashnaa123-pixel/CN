import React, { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { motion, AnimatePresence } from 'framer-motion';
import { LayoutDashboard } from 'lucide-react';
import NetRescueLogo from './NetRescueLogo';
import SquashHamburger from './SquashHamburger';
import { ScrambleText } from './ScrambleText';

const spring = { type: 'spring' as const, stiffness: 350, damping: 28 };

/** Expanded pill covers 40% of the viewport, capped so it stays balanced on ultrawide screens. */
const OPEN_WIDTH_RATIO = 0.4;
const OPEN_WIDTH_MAX = 520;

function useOpenMenuWidth() {
    const [width, setWidth] = useState(OPEN_WIDTH_MAX);

    useEffect(() => {
        const measure = () =>
            setWidth(Math.min(Math.round(window.innerWidth * OPEN_WIDTH_RATIO), OPEN_WIDTH_MAX));
        measure();
        window.addEventListener('resize', measure);
        return () => window.removeEventListener('resize', measure);
    }, []);

    return width;
}

const LINKS = [
    { label: 'Features', to: '/features', hash: false },
    { label: 'Metrics', to: '/#metrics', hash: true },
    { label: 'Architecture', to: '/#architecture', hash: true },
];

function NavLink({ label, to, hash, onClick, className }: {
    label: string; to: string; hash: boolean; onClick?: () => void; className: string;
}) {
    const [hovered, setHovered] = useState(false);
    const hover = { onMouseEnter: () => setHovered(true), onMouseLeave: () => setHovered(false) };
    const text = <ScrambleText text={label} isHovered={hovered} />;
    return hash
        ? <a href={to} onClick={onClick} className={className} {...hover}>{text}</a>
        : <Link to={to} onClick={onClick} className={className} {...hover}>{text}</Link>;
}

const MotionLink = motion.create(Link);

function DashboardButton({ mobile = false }: { mobile?: boolean }) {
    const [hovered, setHovered] = useState(false);
    return (
        <MotionLink
            to="/features"
            className={`${mobile ? 'h-9 px-3.5 text-[13px]' : 'h-12 px-6 text-[14px]'} bg-white text-black rounded-full flex items-center gap-2 font-medium`}
            whileHover={{ scale: 1.03, backgroundColor: '#e2e2e6' }}
            whileTap={{ scale: 0.97 }}
            onMouseEnter={() => setHovered(true)}
            onMouseLeave={() => setHovered(false)}
        >
            <LayoutDashboard size={mobile ? 14 : 16} aria-hidden="true" />
            <ScrambleText text="Dashboard" isHovered={hovered} />
        </MotionLink>
    );
}

export default function Navbar() {
    const [menuOpen, setMenuOpen] = useState(false);
    const [entranceComplete, setEntranceComplete] = useState(false);
    const openWidth = useOpenMenuWidth();

    useEffect(() => {
        const t = setTimeout(() => setEntranceComplete(true), 800);
        return () => clearTimeout(t);
    }, []);

    return (
        <motion.nav
            className="fixed top-0 left-0 right-0 z-50 h-20 flex items-center justify-between gap-2 px-4 sm:px-6 md:px-8"
            initial={{ opacity: 0 }}
            animate={{ opacity: entranceComplete ? 1 : 0 }}
            transition={{ duration: 0.8 }}
        >
            {/* Desktop */}
            <div className="hidden sm:flex items-center gap-2">
                <motion.div
                    whileHover={{ scale: 1.02, backgroundColor: 'rgba(255,255,255,0.22)' }}
                    whileTap={{ scale: 0.98 }}
                    className={`${menuOpen ? 'hidden md:flex' : 'flex'} h-12 rounded-[14px] bg-white/15 backdrop-blur-md`}
                >
                    <Link to="/" className="h-12 px-5 flex items-center gap-2">
                        <NetRescueLogo size={18} />
                        <span className="text-[16px] font-medium tracking-tight">NetRescue</span>
                    </Link>
                </motion.div>

                <motion.div
                    className="flex items-center overflow-hidden rounded-[14px] h-12 bg-white/15 backdrop-blur-md"
                    initial={{ width: 48 }}
                    animate={{ width: menuOpen ? openWidth : 48 }}
                    transition={spring}
                    onMouseEnter={() => setMenuOpen(true)}
                    onMouseLeave={() => setMenuOpen(false)}
                    onFocus={() => setMenuOpen(true)}
                    onBlur={e => { if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setMenuOpen(false); }}
                    onKeyDown={e => { if (e.key === 'Escape') setMenuOpen(false); }}
                >
                    <button
                        className={`flex items-center justify-center flex-shrink-0 transition-colors ${
                            menuOpen ? 'w-9 h-9 rounded-[11px] bg-white/10 hover:bg-white/20 ml-1.5' : 'w-12 h-12 rounded-[14px]'
                        }`}
                        aria-label="Menu"
                        aria-expanded={menuOpen}
                    >
                        <SquashHamburger open={menuOpen} />
                    </button>
                    <AnimatePresence>
                        {menuOpen && (
                            <motion.div
                                initial={{ opacity: 0, x: 15 }}
                                animate={{ opacity: 1, x: 0 }}
                                exit={{ opacity: 0, x: 15 }}
                                className="flex grow shrink-0 items-center justify-center gap-6 px-4 whitespace-nowrap"
                            >
                                {LINKS.map(l => (
                                    <NavLink key={l.label} {...l} className="text-[16px] font-normal text-white/85 hover:text-white transition-colors" />
                                ))}
                            </motion.div>
                        )}
                    </AnimatePresence>
                </motion.div>
            </div>

            <div className="hidden sm:block"><DashboardButton /></div>

            {/* Mobile */}
            <div className="sm:hidden flex items-center gap-2 flex-1 min-w-0">
                <motion.div
                    className="overflow-hidden flex-shrink-0 rounded-[10px] bg-white/15 backdrop-blur-md"
                    animate={{ width: menuOpen ? 0 : 'auto', opacity: menuOpen ? 0 : 1 }}
                    transition={spring}
                >
                    <Link to="/" className="h-9 px-3.5 flex items-center gap-2 whitespace-nowrap">
                        <NetRescueLogo size={15} />
                        <span className="text-[13px] font-medium tracking-tight">NetRescue</span>
                    </Link>
                </motion.div>

                <motion.div
                    className="flex items-center overflow-hidden rounded-[10px] h-9 bg-white/15 backdrop-blur-md"
                    initial={{ width: 36 }}
                    animate={{ width: menuOpen ? '100%' : 36 }}
                    transition={spring}
                >
                    <button
                        className={`flex items-center justify-center flex-shrink-0 transition-colors ${
                            menuOpen ? 'w-7 h-7 rounded-[8px] bg-white/10 ml-1' : 'w-9 h-9'
                        }`}
                        onClick={() => setMenuOpen(o => !o)}
                        aria-label="Menu"
                        aria-expanded={menuOpen}
                    >
                        <SquashHamburger open={menuOpen} mobile />
                    </button>
                    <AnimatePresence>
                        {menuOpen && (
                            <motion.div
                                initial={{ opacity: 0, x: 15 }}
                                animate={{ opacity: 1, x: 0 }}
                                exit={{ opacity: 0, x: 15 }}
                                className="flex grow items-center justify-around gap-3 px-3 whitespace-nowrap"
                            >
                                {LINKS.map(l => (
                                    <NavLink key={l.label} {...l} onClick={() => setMenuOpen(false)}
                                        className="text-[13px] font-normal text-white/85 hover:text-white transition-colors" />
                                ))}
                            </motion.div>
                        )}
                    </AnimatePresence>
                </motion.div>
            </div>
            {!menuOpen && <div className="sm:hidden flex-shrink-0"><DashboardButton mobile /></div>}
        </motion.nav>
    );
}
