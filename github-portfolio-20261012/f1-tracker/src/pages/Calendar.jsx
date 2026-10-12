import { useState } from 'react'
import { Link } from 'react-router-dom'
import { getSchedule } from '../lib/jolpica'
import { useFetch, useNow } from '../lib/hooks'
import { raceDateTime, fmtDate, fmtDateFull, fmtTime, fmtDateTime, localTZ, relative } from '../lib/format'
import { useTitle } from '../lib/hooks'
import { Card, ErrorState, EmptyState, Badge, Flag, Skeleton } from '../components/ui'
import Icon from '../components/Icon'
import Countdown from '../components/Countdown'
import { SeasonSelect } from './Standings'

const SESSION_LABELS = {
  fp1: 'Practice 1', fp2: 'Practice 2', fp3: 'Practice 3',
  sprintQualifying: 'Sprint Quali', sprint: 'Sprint',
  qualifying: 'Qualifying', race: 'Race',
}

function sessionEntries(sessions) {
  return Object.entries(SESSION_LABELS)
    .map(([key, label]) => {
      const s = sessions[key]
      const dt = s && raceDateTime(s.date, s.time)
      return dt ? { key, label, dt } : null
    })
    .filter(Boolean)
    .sort((a, b) => a.dt - b.dt)
}

export default function Calendar() {
  useTitle('Calendar')
  const [season, setSeason] = useState('current')
  const { data, loading, error, refetch } = useFetch(() => getSchedule(season), [season])
  const now = useNow(30000)

  return (
    <div className="page"><div className="container">
      <header className="page-head">
        <div className="eyebrow">Race weekends · times in your timezone ({localTZ()})</div>
        <div className="row between wrap" style={{ gap: 14 }}>
          <div className="page-title"><h1>Calendar</h1></div>
          <SeasonSelect value={season} onChange={setSeason} />
        </div>
      </header>

      {loading && !data && (
        <div className="grid" style={{ gap: 12 }}>
          <div className="hero"><Skeleton w="40%" h={22} /><Skeleton w="65%" h={16} style={{ marginTop: 12 }} /></div>
          {Array.from({ length: 8 }).map((_, i) => (
            <div className="card" key={i}><div className="card-body"><Skeleton w={`${40 + (i % 3) * 12}%`} h={16} /><Skeleton w="55%" h={10} style={{ marginTop: 8 }} /></div></div>
          ))}
        </div>
      )}
      {error && <ErrorState error={error} onRetry={refetch} />}
      {data && <CalendarBody data={data} now={now} season={season} />}
    </div></div>
  )
}

function CalendarBody({ data, now, season }) {
  const races = data.races
  if (!races.length) return <EmptyState title="No calendar published" />

  const withDt = races.map((r) => ({ ...r, dt: raceDateTime(r.date, r.time) }))
  const nextIdx = withDt.findIndex((r) => r.dt && r.dt.getTime() > now.getTime())
  const next = nextIdx >= 0 ? withDt[nextIdx] : null
  const completed = withDt.filter((r) => r.dt && r.dt.getTime() <= now.getTime()).length

  return (
    <>
      {next && <div className="fade-in"><NextRaceHero race={next} /></div>}

      <div className="row between wrap mt-lg mb">
        <h2 style={{ fontSize: '1.15rem' }}>Full Schedule</h2>
        <span className="chip">{completed} / {races.length} races completed</span>
      </div>

      <div className="grid fade-in" style={{ gap: 12 }}>
        {withDt.map((r, i) => (
          <RaceRow key={r.round} race={r} now={now} isNext={i === nextIdx} season={data.season} />
        ))}
      </div>
    </>
  )
}

function NextRaceHero({ race }) {
  const sessions = sessionEntries(race.sessions)
  return (
    <div className="hero mt">
      <div className="row between wrap" style={{ gap: 16, alignItems: 'flex-start' }}>
        <div style={{ minWidth: 260 }}>
          <div className="round-tag">Next up · Round {race.round}</div>
          <h2><Flag country={race.circuit.country} /> {race.name}</h2>
          <div className="muted">{race.circuit.name} · {race.circuit.locality}, {race.circuit.country}</div>
          <div className="mt"><Countdown target={race.dt} /></div>
          <div className="muted mt" style={{ fontSize: '.9rem' }}>
            Lights out {fmtDateFull(race.dt)} · {fmtTime(race.dt)}
          </div>
        </div>
        {sessions.length > 0 && (
          <div className="card pad" style={{ minWidth: 240, background: 'rgba(0,0,0,.25)' }}>
            <div className="uppercase faint" style={{ fontSize: '.72rem', marginBottom: 8 }}>Weekend schedule</div>
            {sessions.map((s) => (
              <div className="row between" key={s.key} style={{ padding: '4px 0', gap: 16 }}>
                <span className={s.key === 'race' ? '' : 'muted'} style={{ fontWeight: s.key === 'race' ? 700 : 600 }}>{s.label}</span>
                <span className="mono" style={{ fontSize: '.85rem' }}>{fmtDate(s.dt, { weekday: 'short' })} {fmtTime(s.dt)}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

function RaceRow({ race, now, isNext, season }) {
  const [open, setOpen] = useState(false)
  const isPast = race.dt && race.dt.getTime() <= now.getTime()
  const sessions = sessionEntries(race.sessions)
  const hasSprint = !!race.sessions.sprint

  return (
    <div className="card hover" style={isNext ? { borderColor: 'rgba(255,43,23,.45)' } : undefined}>
      <div
        className="card-body row between wrap"
        style={{ gap: 14, cursor: 'pointer' }}
        role="button"
        tabIndex={0}
        aria-expanded={open}
        aria-label={`${race.name} — show session times`}
        onClick={() => setOpen((o) => !o)}
        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setOpen((o) => !o) } }}
      >
        <div className="row" style={{ gap: 14, minWidth: 0 }}>
          <div style={{ textAlign: 'center', minWidth: 40 }}>
            <div className="mono faint" style={{ fontSize: '.7rem' }}>RND</div>
            <div className="mono" style={{ fontSize: '1.3rem', fontWeight: 700 }}>{String(race.round).padStart(2, '0')}</div>
          </div>
          <div style={{ minWidth: 0 }}>
            <div className="row gap-sm wrap">
              <b style={{ fontSize: '1.05rem' }}><Flag country={race.circuit.country} /> {race.name}</b>
              {hasSprint && <Badge kind="warn">Sprint</Badge>}
              {isNext && <Badge kind="bad">Next</Badge>}
              {isPast && !isNext && <Badge kind="good">Completed</Badge>}
            </div>
            <div className="muted" style={{ fontSize: '.88rem' }}>{race.circuit.name} · {race.circuit.locality}, {race.circuit.country}</div>
          </div>
        </div>
        <div className="row" style={{ gap: 18 }}>
          <div style={{ textAlign: 'right' }}>
            <div style={{ fontWeight: 700 }}>{fmtDate(race.dt, { month: 'short', day: 'numeric' })}</div>
            <div className="faint" style={{ fontSize: '.82rem' }}>{race.dt ? relative(race.dt, now) : ''}</div>
          </div>
          {isPast && (
            <Link className="btn ghost hide-sm" to={`/results?season=${season}&round=${race.round}`} onClick={(e) => e.stopPropagation()}>
              Results
            </Link>
          )}
          <Icon name="chevron" size={18} style={{ color: 'var(--faint)', transform: open ? 'rotate(180deg)' : 'none', transition: 'transform .25s' }} />
        </div>
      </div>

      {open && (
        <div className="card-body" style={{ borderTop: '1px solid rgba(255,255,255,0.08)', background: 'rgba(255,255,255,0.02)' }}>
          <div className="wx-grid">
            {sessions.map((s) => (
              <div className="wx" key={s.key}>
                <div className="k">{s.label}</div>
                <div className="v" style={{ fontSize: '1rem' }}>{fmtDateTime(s.dt)}</div>
              </div>
            ))}
          </div>
          {isPast && (
            <div className="btn-row mt">
              <Link className="btn primary" to={`/results?season=${season}&round=${race.round}`}>View results</Link>
              <a className="btn ghost" href={race.url} target="_blank" rel="noreferrer">Wikipedia <Icon name="external" size={15} /></a>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
