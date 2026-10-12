import { useMemo } from 'react'
import {
  getLatestSession, getDrivers, getSessionResult, getStints, getLaps, getPits,
  getWeather, getRaceControl, getTeamRadio, getPositions,
  latestPerDriver, latestStintPerDriver, stintsByDriver, latestLapPerDriver, fastestLapPerDriver,
  pitCountByDriver, isSessionLive,
} from '../lib/openf1'
import { useFetch, usePoll, useNow, useTitle } from '../lib/hooks'
import { fmtLapTime, fmtRaceDuration, fmtGap, fmtTime, relative, pts } from '../lib/format'
import { Card, OpenF1DriverCell, Pos, Tyre, StintStrip, Loader, ErrorState, EmptyState, LiveBadge, Badge, Flag, TableSkeleton } from '../components/ui'
import Icon from '../components/Icon'

const POLL_MS = 6000

const driverMapFrom = (drivers) => {
  const m = new Map()
  for (const d of drivers || []) m.set(d.driver_number, d)
  return m
}

// Non-race sessions return arrays (Q1/Q2/Q3) for duration/gap; take the best (last non-null) value.
const bestSet = (v) => (Array.isArray(v) ? ([...v].reverse().find((x) => x != null) ?? null) : v)

async function loadTiming(sessionKey, live) {
  // Immutable once a session ends → cache for 30 min; live → always fresh.
  const ttl = live ? 0 : 30 * 60 * 1000
  const [result, stints, laps, pits, weather, rc, radio] = await Promise.all([
    // The classification is the critical feed — let its failure surface as an error
    // (secondary feeds degrade to [] so one flaky endpoint can't blank the tower).
    getSessionResult(sessionKey, ttl),
    getStints(sessionKey, ttl).catch(() => []),
    getLaps(sessionKey, ttl).catch(() => []),
    getPits(sessionKey, ttl).catch(() => []),
    getWeather(sessionKey, ttl).catch(() => []),
    getRaceControl(sessionKey, ttl).catch(() => []),
    getTeamRadio(sessionKey, ttl).catch(() => []),
  ])
  // Positions are only needed as a live fallback when the classification is empty.
  let positions = []
  if (live && (!result || !result.length)) positions = await getPositions(sessionKey, 0).catch(() => [])
  return { result, stints, laps, pits, weather, rc, radio, positions }
}

export default function Live() {
  useTitle('Live Timing')
  const sessionQ = useFetch(getLatestSession, [])
  const session = sessionQ.data
  const sessionKey = session?.session_key
  const now = useNow(10000)
  const live = isSessionLive(session, now)

  const driversQ = useFetch(
    () => (sessionKey ? getDrivers(sessionKey) : Promise.resolve([])),
    [sessionKey],
  )

  const timing = usePoll(
    () => (sessionKey ? loadTiming(sessionKey, live) : Promise.resolve(null)),
    [sessionKey],
    { interval: POLL_MS, enabled: !!sessionKey && live },
  )

  if (sessionQ.loading) return <div className="page"><div className="container"><Loader label="Finding the latest session…" /></div></div>
  if (sessionQ.error) return <div className="page"><div className="container"><ErrorState error={sessionQ.error} onRetry={sessionQ.refetch} /></div></div>
  if (!session) return <div className="page"><div className="container"><EmptyState title="No session found" /></div></div>

  return (
    <div className="page"><div className="container">
      <header className="page-head">
        <div className="eyebrow">Timing tower · {live ? 'session in progress' : 'latest session'}</div>
        <div className="page-title"><h1>Live Timing</h1></div>
      </header>
      <SessionHeader session={session} live={live} lastUpdated={timing.lastUpdated} />
      {!live && (
        <div className="card pad mt row gap-sm" style={{ fontSize: '.9rem' }}>
          <Icon name="info" size={18} style={{ color: 'var(--info)', flex: 'none' }} />
          <span className="muted">No session is running right now. Showing the most recent session — the leaderboard is its final classification.</span>
        </div>
      )}

      <div className="grid dash mt-lg fade-in">
        <Leaderboard session={session} timing={timing} drivers={driversQ.data} live={live} />
        <div className="grid" style={{ gap: 16 }}>
          <Weather timing={timing} />
          <RaceControl timing={timing} />
          <TeamRadio timing={timing} drivers={driversQ.data} />
        </div>
      </div>
    </div></div>
  )
}

function SessionHeader({ session, live, lastUpdated }) {
  const now = useNow(1000)
  return (
    <div className="hero">
      <div className="row between wrap" style={{ gap: 12 }}>
        <div>
          <div className="round-tag">{session.session_type} · {session.location}</div>
          <h2><Flag code={session.country_code} /> {session.circuit_short_name} — {session.session_name}</h2>
          <div className="muted">{session.country_name} · {session.year}</div>
        </div>
        <div style={{ textAlign: 'right' }}>
          <LiveBadge live={live} />
          <div className="faint mt" style={{ fontSize: '.8rem' }}>
            {live
              ? (lastUpdated ? `Updated ${relative(lastUpdated, now)} · refreshes every ${POLL_MS / 1000}s` : 'Connecting…')
              : `Session ended ${relative(new Date(session.date_end), now)}`}
          </div>
        </div>
      </div>
    </div>
  )
}

// Render the gap/time cell defensively — OpenF1's shape varies by session type.
function GapCell({ row, isRace }) {
  if (row.dsq) return <Badge kind="bad">DSQ</Badge>
  if (row.dnf) return <Badge kind="bad">DNF</Badge>
  if (row.dns) return <Badge kind="warn">DNS</Badge>
  // Practice / Qualifying: the "result" is a lap time, not a race gap.
  if (!isRace) {
    return typeof row.time === 'number'
      ? <span className="mono">{fmtLapTime(row.time)}</span>
      : <span className="faint">—</span>
  }
  if (row.pos === 1) {
    return <span className="mono" style={{ color: 'var(--good)' }}>{typeof row.time === 'number' ? fmtRaceDuration(row.time) : 'LEADER'}</span>
  }
  const g = row.gap
  if (g == null) return <span className="faint">—</span>
  if (typeof g === 'string') return <span className="mono">{g}</span>
  return <span className="mono">{fmtGap(g)}</span>
}

function Leaderboard({ session, timing, drivers, live }) {
  const driverMap = useMemo(() => driverMapFrom(drivers), [drivers])

  const model = useMemo(() => {
    if (!timing.data) return null
    const { result, stints, laps, pits, positions } = timing.data
    const latestStint = latestStintPerDriver(stints)
    const stintMap = stintsByDriver(stints)
    const lastLap = latestLapPerDriver(laps)
    const bestLap = fastestLapPerDriver(laps)
    const pitCount = pitCountByDriver(pits)
    const totalLaps = Math.max(0, ...stints.map((s) => s.lap_end || 0))

    // Driver holding the session's fastest lap — gets the purple highlight.
    let fastestNum = null, fastestDur = Infinity
    for (const [num, l] of bestLap) if (l.lap_duration < fastestDur) { fastestDur = l.lap_duration; fastestNum = num }

    let rows
    if (result && result.length) {
      // Keep DNS/DSQ/DNF rows (null position) so their badges show; sort them last.
      rows = result
        .filter((r) => r.position != null || r.dnf || r.dns || r.dsq)
        .sort((a, b) => (a.position ?? Infinity) - (b.position ?? Infinity))
        .map((r) => ({
          pos: r.position ?? null, num: r.driver_number, gap: bestSet(r.gap_to_leader), time: bestSet(r.duration),
          laps: r.number_of_laps, points: r.points, dnf: r.dnf, dns: r.dns, dsq: r.dsq,
        }))
    } else {
      // Live fallback: order by latest position record.
      const latestPos = latestPerDriver(positions)
      rows = [...latestPos.values()]
        .filter((p) => p.position != null)
        .sort((a, b) => a.position - b.position)
        .map((p) => ({
          pos: p.position, num: p.driver_number, gap: null, time: null,
          laps: lastLap.get(p.driver_number)?.lap_number ?? null, points: null,
        }))
    }
    return { rows, latestStint, stintMap, bestLap, pitCount, totalLaps, fastestNum }
  }, [timing.data])

  const isRace = session.session_type === 'Race'

  return (
    <Card
      title="Timing Tower"
      icon="signal"
      right={timing.error && <Badge kind="warn" title={String(timing.error.message)}>data hiccup</Badge>}
    >
      {timing.loading && !model ? <TableSkeleton rows={12} />
        : !model || !model.rows.length ? <EmptyState title="No timing data" icon="signal">Timing data isn’t available for this session.</EmptyState>
          : (
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th className="c">Pos</th>
                    <th>Driver</th>
                    <th className="c">Tyre</th>
                    <th className="hide-sm">Stints</th>
                    <th className="c hide-sm">Laps</th>
                    <th className="r hide-sm">Best Lap</th>
                    <th className="c hide-sm">Pits</th>
                    <th className="r">{isRace ? 'Gap / Time' : 'Result'}</th>
                    {isRace && <th className="c hide-sm">Pts</th>}
                  </tr>
                </thead>
                <tbody>
                  {model.rows.map((row) => {
                    const d = driverMap.get(row.num)
                    const stint = model.latestStint.get(row.num)
                    const best = model.bestLap.get(row.num)
                    const isFastest = row.num === model.fastestNum
                    return (
                      <tr key={row.num}>
                        <td className="c"><Pos n={row.pos} /></td>
                        <td>{d ? <OpenF1DriverCell d={d} /> : <span className="mono">#{row.num}</span>}</td>
                        <td className="c">{stint ? <Tyre compound={stint.compound} title={`${stint.compound} · age ${stint.tyre_age_at_start}+`} /> : '—'}</td>
                        <td className="hide-sm"><StintStrip stints={model.stintMap.get(row.num)} totalLaps={model.totalLaps} /></td>
                        <td className="c hide-sm num">{row.laps ?? '—'}</td>
                        <td className="r hide-sm mono" style={isFastest ? { color: 'var(--purple)', fontWeight: 700 } : undefined} title={isFastest ? 'Fastest lap of the session' : undefined}>{best ? fmtLapTime(best.lap_duration) : '—'}</td>
                        <td className="c hide-sm num">{model.pitCount.get(row.num) || 0}</td>
                        <td className="r"><GapCell row={row} isRace={isRace} /></td>
                        {isRace && <td className="c hide-sm num">{row.points ? pts(row.points) : '—'}</td>}
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
    </Card>
  )
}

function Weather({ timing }) {
  const wx = useMemo(() => {
    const arr = timing.data?.weather
    if (!arr || !arr.length) return null
    return arr.reduce((a, b) => (a && a.date > b.date ? a : b))
  }, [timing.data])

  if (!wx) return null
  const tiles = [
    ['Air', `${wx.air_temperature}°`, 'C', 'thermometer'],
    ['Track', `${wx.track_temperature}°`, 'C', 'thermometer'],
    ['Humidity', `${wx.humidity}`, '%', 'droplet'],
    ['Wind', `${wx.wind_speed}`, 'm/s', 'wind'],
  ]
  return (
    <Card title="Weather" icon={wx.rainfall ? 'rain' : 'sun'}
      right={<Badge kind={wx.rainfall ? 'info' : ''}>{wx.rainfall ? 'Wet' : 'Dry'}</Badge>}>
      <div className="wx-grid">
        {tiles.map(([k, v, u, ic]) => (
          <div className="wx" key={k}><div className="k"><Icon name={ic} size={13} /> {k}</div><div className="v">{v}<small> {u}</small></div></div>
        ))}
      </div>
    </Card>
  )
}

const FLAG_COLORS = {
  GREEN: '#2bd576', YELLOW: '#ffd42a', 'DOUBLE YELLOW': '#ffb020', RED: '#e10600',
  BLUE: '#3aa0ff', CLEAR: '#2bd576', CHEQUERED: '#f4f4f7',
}

function RaceControl({ timing }) {
  const feed = useMemo(() => {
    const arr = timing.data?.rc
    if (!arr) return []
    return [...arr].sort((a, b) => (b.date || '').localeCompare(a.date || '')).slice(0, 25)
  }, [timing.data])

  return (
    <Card title="Race Control" icon="clipboard">
      {!feed.length ? <p className="muted" style={{ fontSize: '.9rem' }}>No messages.</p> : (
        <div className="feed" style={{ maxHeight: 340, overflowY: 'auto' }}>
          {feed.map((m) => (
            <div className="feed-item" key={`${m.date}|${m.message}`}>
              {m.flag && FLAG_COLORS[m.flag] && <span className="flag-chip" style={{ background: FLAG_COLORS[m.flag] }} />}
              <span className="t">{fmtTime(new Date(m.date))}</span>
              <span className="msg">{m.message}</span>
            </div>
          ))}
        </div>
      )}
    </Card>
  )
}

function TeamRadio({ timing, drivers }) {
  const clips = useMemo(() => {
    const arr = timing.data?.radio
    if (!arr) return []
    return [...arr].sort((a, b) => (b.date || '').localeCompare(a.date || '')).slice(0, 10)
  }, [timing.data])

  const driverMap = useMemo(() => driverMapFrom(drivers), [drivers])

  if (!clips.length) return null
  return (
    <Card title="Team Radio" icon="radio">
      {clips.map((c) => {
        const d = driverMap.get(c.driver_number)
        return (
          <div className="radio-item" key={c.recording_url}>
            <span className="dnum" style={{ borderColor: d?.team_colour ? `#${d.team_colour}` : undefined }}>
              {d?.name_acronym || c.driver_number}
            </span>
            <span className="muted grow" style={{ fontSize: '.82rem' }}>{fmtTime(new Date(c.date))}</span>
            <audio controls preload="none" src={c.recording_url}
              aria-label={`Team radio — ${d?.full_name || d?.name_acronym || `car ${c.driver_number}`}`} />
          </div>
        )
      })}
    </Card>
  )
}
