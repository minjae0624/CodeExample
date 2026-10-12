import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { getSchedule } from '../lib/jolpica'
import { useFetch, useNow } from '../lib/hooks'
import { raceDateTime, relative } from '../lib/format'
import ErrorBoundary from './ErrorBoundary'

const LINKS = [
  { to: '/', label: 'Home', end: true },
  { to: '/standings', label: 'Standings' },
  { to: '/schedule', label: 'Calendar' },
  { to: '/results', label: 'Results' },
  { to: '/live', label: 'Live', live: true },
]

// Compact "next race" status in the nav — the whole site shares one cached fetch.
function NextRacePill() {
  const { data } = useFetch(() => getSchedule('current'), [])
  const now = useNow(60000)
  const next = (data?.races || [])
    .map((r) => ({ ...r, dt: raceDateTime(r.date, r.time) }))
    .find((r) => r.dt && r.dt.getTime() > now.getTime())
  if (!next) return null
  return (
    <NavLink to="/schedule" className="next-pill hide-sm" title={`${next.name} — round ${next.round}`}>
      <span className="tick" aria-hidden />
      R{next.round} <b>{next.name.replace(' Grand Prix', ' GP')}</b> {relative(next.dt, now)}
    </NavLink>
  )
}

export default function Layout() {
  const location = useLocation()
  return (
    <div className="app">
      <header className="nav">
        <div className="container nav-inner">
          <NavLink to="/" className="brand" aria-label="PitWall home">
            <span className="mark" aria-hidden />
            <span><b>PIT</b><span style={{ color: 'var(--accent-2)' }}>WALL</span></span>
          </NavLink>
          <nav className="nav-links" aria-label="Primary">
            {LINKS.map((l) => (
              <NavLink
                key={l.to}
                to={l.to}
                end={l.end}
                className={({ isActive }) => `nav-link${l.live ? ' live' : ''}${isActive ? ' active' : ''}`}
              >
                {l.live && <span className="live-dot on" aria-hidden />}
                {l.label}
              </NavLink>
            ))}
          </nav>
          <NextRacePill />
        </div>
      </header>

      <main className="grow">
        <ErrorBoundary resetKey={location.key}>
          <Outlet />
        </ErrorBoundary>
      </main>

      <footer className="footer">
        <div className="container foot-grid">
          <div>
            <div className="foot-brand"><span className="mark" aria-hidden />PITWALL</div>
            <p className="foot-note" style={{ margin: '10px 0 0' }}>
              An unofficial Formula 1 tracker. Not associated with Formula 1, the FIA, or any team.
              F1® and related marks are trademarks of Formula One Licensing BV.
            </p>
          </div>
          <div className="foot-note">
            Timing &amp; results from{' '}
            <a href="https://github.com/jolpica/jolpica-f1" target="_blank" rel="noreferrer">Jolpica-F1</a>{' '}
            and{' '}
            <a href="https://openf1.org" target="_blank" rel="noreferrer">OpenF1</a>.
            <br />
            <span className="faint">Data can lag the broadcast. No cookies, no accounts, no tracking.</span>
          </div>
        </div>
      </footer>
    </div>
  )
}
