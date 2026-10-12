import { teamColor, openf1Color, natISO, countryISO, codeISO } from '../lib/teams'
import { compound, compoundLetter } from '../lib/format'
import { FLAG_SRC } from '../lib/flags'
import Icon from './Icon'

// Country flag as an SVG image. Accepts one of: iso (alpha-2), a demonym (`nat`),
// a country name (`country`), or a 3-letter code (`code`).
export function Flag({ iso, nat, country, code, title, alt = '' }) {
  const cc = (iso || natISO(nat) || countryISO(country) || codeISO(code) || '').toLowerCase()
  const src = FLAG_SRC[cc]
  if (!src) return null
  // alt defaults to "" (decorative — a name sits beside it); pass alt when standalone.
  return <img className="flag" src={src} alt={alt} title={title} width={20} height={15} loading="lazy" />
}

// --- Loading / error / empty states ---------------------------------------
export function Loader({ label = 'Loading…' }) {
  return (
    <div className="state">
      <div className="spinner" />
      <div>{label}</div>
    </div>
  )
}

export function ErrorState({ error, onRetry, label }) {
  return (
    <div className="state">
      <Icon name="alert" size={40} className="big" style={{ color: 'var(--bad)' }} />
      <div style={{ color: 'var(--text)', fontWeight: 700 }}>{label || 'Could not load data'}</div>
      <div style={{ maxWidth: 460, fontSize: '.9rem' }}>{String(error?.message || error || 'Unknown error')}</div>
      {onRetry && <button className="btn primary" onClick={onRetry}>Try again</button>}
    </div>
  )
}

export function EmptyState({ icon = 'flag', title = 'Nothing here yet', children }) {
  return (
    <div className="state">
      <Icon name={icon} size={40} className="big" />
      <div style={{ color: 'var(--text)', fontWeight: 700 }}>{title}</div>
      {children && <div style={{ maxWidth: 460, fontSize: '.9rem' }}>{children}</div>}
    </div>
  )
}

// --- Skeleton shimmer (Stripe/Linear-style loading, not a spinner) ---------
export function Skeleton({ w = '100%', h = 14, r = 8, style }) {
  return <span className="skeleton" style={{ display: 'block', width: w, height: h, borderRadius: r, ...style }} />
}

// A table-shaped placeholder so the layout doesn't jump when data arrives.
export function TableSkeleton({ rows = 8 }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14, padding: '4px 0' }}>
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="row" style={{ gap: 12, opacity: 1 - i * 0.06 }}>
          <Skeleton w={26} h={26} r={8} />
          <Skeleton w={4} h={26} r={2} />
          <div className="grow" style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            <Skeleton w={`${45 + (i % 4) * 8}%`} h={12} />
            <Skeleton w="30%" h={9} />
          </div>
          <Skeleton w={60} h={12} />
        </div>
      ))}
    </div>
  )
}

// --- Cards / sections ------------------------------------------------------
export function Card({ title, right, children, icon, className = '', bodyClass = '' }) {
  return (
    <section className={`card ${className}`}>
      {(title || right) && (
        <div className="card-head">
          <h3>{icon && <span className="ico-chip"><Icon name={icon} size={17} /></span>}{title}</h3>
          {right}
        </div>
      )}
      <div className={`card-body ${bodyClass}`}>{children}</div>
    </section>
  )
}

// --- Driver identity cell --------------------------------------------------
export function TeamStripe({ color }) {
  return <span className="team-stripe" style={{ '--team': color }} />
}

// Driver cell for Ergast/Jolpica-shaped data.
export function DriverCell({ driver, constructor, showNumber = true, subtitle }) {
  const color = teamColor(constructor?.id)
  return (
    <div className="driver-cell">
      <TeamStripe color={color} />
      {showNumber && driver?.number != null && <span className="dnum">{driver.number}</span>}
      <span className="driver-name">
        <b>{driver?.name || '—'}</b>
        <span className="team">{subtitle ?? constructor?.name}</span>
      </span>
    </div>
  )
}

// Driver cell for OpenF1-shaped data (already carries team_colour + names).
export function OpenF1DriverCell({ d, subtitle }) {
  const color = openf1Color(d?.team_colour)
  return (
    <div className="driver-cell">
      <TeamStripe color={color} />
      <span className="dnum">{d?.driver_number}</span>
      <span className="driver-name">
        <b>{d?.full_name || d?.broadcast_name || `#${d?.driver_number}`}</b>
        <span className="team">{subtitle ?? d?.team_name}</span>
      </span>
    </div>
  )
}

// --- Position cell (podium-tinted) -----------------------------------------
export function Pos({ n }) {
  const cls = n === 1 ? 'p1' : n === 2 ? 'p2' : n === 3 ? 'p3' : ''
  return <span className={`pos ${cls}`}>{n == null ? '—' : n}</span>
}

// --- Sparkbar (points magnitude) -------------------------------------------
export function Sparkbar({ value, max, color }) {
  const pct = max > 0 ? Math.max(2, (value / max) * 100) : 0
  return (
    <div className="sparkbar" style={{ '--team': color }} role="img" aria-label={`${value} points`}>
      <span style={{ width: `${pct}%` }} />
    </div>
  )
}

// --- Tyre compound badge ---------------------------------------------------
export function Tyre({ compound: c, title }) {
  const comp = compound(c)
  return <span className={`tyre ${comp}`} title={title || comp}>{compoundLetter(comp)}</span>
}

export function StintStrip({ stints, totalLaps }) {
  if (!stints?.length) return <span className="faint">—</span>
  const total = totalLaps || stints.reduce((a, s) => a + Math.max(1, (s.lap_end - s.lap_start + 1)), 0)
  return (
    <div className="stint-strip" title={stints.map((s) => `${compound(s.compound)} (L${s.lap_start}–${s.lap_end})`).join('  ')}>
      {stints.map((s, i) => {
        const laps = Math.max(1, (s.lap_end - s.lap_start + 1))
        return <span key={i} className={`stint-seg ${compound(s.compound)}`} style={{ width: `${(laps / total) * 100}%` }} />
      })}
    </div>
  )
}

// --- Badges ----------------------------------------------------------------
export function Badge({ children, kind = '', style, title }) {
  return <span className={`badge ${kind}`} style={style} title={title}>{children}</span>
}

export function LiveBadge({ live }) {
  return live
    ? <span className="live-badge on"><span className="live-dot on" /> Live</span>
    : <span className="live-badge off"><span className="live-dot" /> Finished</span>
}
