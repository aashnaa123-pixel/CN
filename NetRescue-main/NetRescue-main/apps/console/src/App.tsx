/**
 * App.tsx — shell and navigation.
 *
 * The live-event socket is opened once here and passed down, so every screen
 * shares a single connection and a single ordered event history.
 */
import { NavLink, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { useEvents } from './hooks'
import { SocketBadge } from './components'
import { Overview } from './pages/Overview'
import { RecoveryLab } from './pages/RecoveryLab'
import { TrafficLab } from './pages/TrafficLab'
import { TransferLab } from './pages/TransferLab'
import { BenchmarkLab } from './pages/BenchmarkLab'
import { RunHistory } from './pages/RunHistory'
import { RunDetails } from './pages/RunDetails'
import { SecurityAudit } from './pages/SecurityAudit'

const SCREENS = [
  { to: '/overview', label: 'Overview', icon: '◈' },
  { to: '/recovery', label: 'Recovery Lab', icon: '⚡' },
  { to: '/traffic', label: 'Traffic Lab', icon: '⇅' },
  { to: '/transfer', label: 'Transfer Lab', icon: '⇄' },
  { to: '/benchmark', label: 'Benchmark Lab', icon: '📈' },
  { to: '/history', label: 'Run History', icon: '☰' },
  { to: '/security', label: 'Security & Audit', icon: '⛨' },
]

const TITLES: Record<string, { title: string; sub: string }> = {
  '/overview': { title: 'Overview', sub: 'Live topology, node health and system state' },
  '/recovery': { title: 'Recovery Lab', sub: 'Break a real topology and watch it route around the failure' },
  '/traffic': { title: 'Traffic Lab', sub: 'FIFO versus priority scheduling under congestion' },
  '/transfer': { title: 'Transfer Lab', sub: 'Multipath transfer with SHA-256 integrity verification' },
  '/benchmark': { title: 'Benchmark Lab', sub: 'Measured network conditions, repeated runs and CSV export' },
  '/history': { title: 'Run History', sub: 'Saved experiments, results and reproduction' },
  '/security': { title: 'Security & Audit', sub: 'Authorised actions, denials and host capability' },
  '/runs': { title: 'Run Details', sub: 'Configuration, metrics and reproduction' },
}

export function App() {
  const events = useEvents()
  const location = useLocation()
  const meta = TITLES[location.pathname] ??
    (location.pathname.startsWith('/runs/')
      ? TITLES['/runs']
      : { title: 'NetRescue', sub: '' })

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <h1>NetRescue</h1>
          <p>Resilience Lab</p>
        </div>
        <nav className="nav">
          {SCREENS.map((s) => (
            <NavLink
              key={s.to}
              to={s.to}
              className={({ isActive }) => (isActive ? 'active' : '')}
            >
              <span className="icon" aria-hidden="true">{s.icon}</span>
              {s.label}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-foot">
          Self-healing recovery · Traffic priority · Multipath transfer · Benchmarking
        </div>
      </aside>

      <div className="main">
        <header className="topbar">
          <h2>{meta.title}</h2>
          <span className="sub">{meta.sub}</span>
          <span className="spacer" />
          <SocketBadge state={events.state} />
        </header>
        <main className="content">
          <Routes>
            <Route path="/" element={<Navigate to="/overview" replace />} />
            <Route path="/overview" element={<Overview events={events} />} />
            <Route path="/recovery" element={<RecoveryLab events={events} />} />
            <Route path="/traffic" element={<TrafficLab />} />
            <Route path="/transfer" element={<TransferLab />} />
            <Route path="/benchmark" element={<BenchmarkLab />} />
            <Route path="/history" element={<RunHistory />} />
            <Route path="/runs/:runId" element={<RunDetails />} />
            <Route path="/security" element={<SecurityAudit />} />
            <Route path="*" element={<Navigate to="/overview" replace />} />
          </Routes>
        </main>
      </div>
    </div>
  )
}
