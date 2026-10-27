import React from 'react';
import { Routes, Route } from 'react-router-dom';
import Layout from './components/Layout';
import LandingPage from './pages/LandingPage';
import FeaturesIndexPage from './pages/FeaturesIndexPage';
import FeatureDetailPage from './pages/FeatureDetailPage';
import HysteresisPlaygroundPage from './pages/HysteresisPlaygroundPage';
import MultipathPlaygroundPage from './pages/MultipathPlaygroundPage';
import DiagnosticsPlaygroundPage from './pages/DiagnosticsPlaygroundPage';
import RoutingLabPage from './pages/RoutingLabPage';
import TcpCongestionLabPage from './pages/TcpCongestionLabPage';
import TrafficShapingLabPage from './pages/TrafficShapingLabPage';
import TcpUdpLabPage from './pages/TcpUdpLabPage';
import IntegrationLabPage from './pages/IntegrationLabPage';
import { PrivacyPage, TermsPage, CookiesPage } from './pages/LegalPages';
import NotFoundPage from './pages/NotFoundPage';

export default function App() {
    return (
        <Routes>
            <Route element={<Layout />}>
                <Route path="/" element={<LandingPage />} />
                <Route path="/features" element={<FeaturesIndexPage />} />
                {/* Feature A's workbench. Declared before the `:id` route so the
                    static segment wins — `/features/hysteresis/playground` is
                    not a feature called "hysteresis" with a stray segment. */}
                <Route path="/features/hysteresis/playground" element={<HysteresisPlaygroundPage />} />
                <Route path="/features/multipath/playground" element={<MultipathPlaygroundPage />} />
                <Route path="/features/diagnostics/playground" element={<DiagnosticsPlaygroundPage />} />
                <Route path="/features/routing/playground" element={<RoutingLabPage />} />
                <Route path="/features/tcp-congestion/playground" element={<TcpCongestionLabPage />} />
                <Route path="/features/traffic-shaping/playground" element={<TrafficShapingLabPage />} />
                <Route path="/features/tcp-udp/playground" element={<TcpUdpLabPage />} />
                <Route path="/features/integration/playground" element={<IntegrationLabPage />} />
                <Route path="/features/:id" element={<FeatureDetailPage />} />
                <Route path="/privacy" element={<PrivacyPage />} />
                <Route path="/terms" element={<TermsPage />} />
                <Route path="/cookies" element={<CookiesPage />} />
                <Route path="*" element={<NotFoundPage />} />
            </Route>
        </Routes>
    );
}
