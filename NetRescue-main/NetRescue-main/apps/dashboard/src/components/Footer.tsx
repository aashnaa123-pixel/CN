import React from 'react';
import { Link } from 'react-router-dom';
import { useConsent } from '../consent/ConsentContext';
import NetRescueLogo from './NetRescueLogo';
import VideoBackground from './VideoBackground';
import { VIDEO_URLS } from '../data/features';

export default function Footer() {
    const { openPanel } = useConsent();
    return (
        <footer className="bg-black overflow-hidden flex flex-col md:flex-row min-h-[400px]">
            {/* Left: Video */}
            <div className="relative w-full md:w-1/2 h-[300px] md:h-auto">
                <VideoBackground
                    src={VIDEO_URLS.footer}
                    overlay="bg-black/30"
                />
            </div>

            {/* Right: Content */}
            <div className="flex flex-col justify-between p-10 sm:p-16 w-full md:w-1/2">
                <div>
                    <div className="flex items-center gap-2 mb-6">
                        <NetRescueLogo size={18} className="text-white/70" />
                        <span className="text-[15px] font-medium text-white/70">NetRescue</span>
                    </div>
                    <p className="text-white/40 max-w-sm text-[14px] leading-relaxed">
                        A networking lab for experimenting with failure detection, multipath transfer and diagnostics.
                    </p>
                </div>
                <div className="mt-12">
                    <nav aria-label="Legal" className="flex flex-wrap gap-x-5 gap-y-2 text-[13px] text-white/70">
                        <Link to="/privacy" className="underline underline-offset-2 hover:text-white">Privacy</Link>
                        <Link to="/terms" className="underline underline-offset-2 hover:text-white">Terms</Link>
                        <Link to="/cookies" className="underline underline-offset-2 hover:text-white">Cookies &amp; third parties</Link>
                        <button onClick={openPanel} className="underline underline-offset-2 hover:text-white">Third-party preferences</button>
                    </nav>
                    <p className="text-white/60 text-[12px] mt-4">© 2026 NetRescue. Licence not yet declared; see Terms.</p>
                </div>
            </div>
        </footer>
    );
}
