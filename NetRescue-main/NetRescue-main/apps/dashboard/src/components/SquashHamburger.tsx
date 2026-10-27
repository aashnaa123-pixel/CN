import React from 'react';
import { motion } from 'framer-motion';

const spring = { type: 'spring' as const, stiffness: 300, damping: 20 };

interface SquashHamburgerProps {
    open: boolean;
    mobile?: boolean;
}

/** Three bars that squash into an X when `open`. */
export default function SquashHamburger({ open, mobile = false }: SquashHamburgerProps) {
    const width = mobile ? 15 : 18;
    const height = mobile ? 10 : 12;
    const bar = mobile ? 1.2 : 1.5;
    const travel = height / 2 - bar / 2;
    const base = 'absolute left-0 block w-full bg-white rounded-full';

    return (
        <div className="relative" style={{ width, height }} aria-hidden="true">
            <motion.span
                className={base}
                style={{ height: bar, top: 0 }}
                animate={open ? { y: travel, rotate: 45 } : { y: 0, rotate: 0 }}
                transition={spring}
            />
            <motion.span
                className={base}
                style={{ height: bar, top: `calc(50% - ${bar / 2}px)` }}
                animate={open ? { opacity: 0, scaleX: 0 } : { opacity: 1, scaleX: 1 }}
                transition={spring}
            />
            <motion.span
                className={base}
                style={{ height: bar, bottom: 0 }}
                animate={open ? { y: -travel, rotate: -45 } : { y: 0, rotate: 0 }}
                transition={spring}
            />
        </div>
    );
}
