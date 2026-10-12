import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { getSchedule, getResults, getQualifying } from '../lib/jolpica'
import { useFetch, useTitle } from '../lib/hooks'
import { teamColor } from '../lib/teams'
import { raceDateTime, fmtDateFull } from '../lib/format'
import { Card, DriverCell, Pos, ErrorState, EmptyState, Badge, Flag, TableSkeleton } from '../components/ui'
import Icon from '../components/Icon'
import { SeasonSelect } from './Standings'

export default function Results() {
  useTitle('Results')
  const [params, setParams] = useSearchParams()
  const [season, setSeason] = useState(params.get('season') || 'current')
  const [round, setRound] = useState(params.get('round') ? Number(params.get('round')) : null)
  const [view, setView] = useState('race')

  const { data: schedule } = useFetch(() => getSchedule(season), [season])

  // Choose a sensible default round once the schedule for this season arrives.
  useEffect(() => {
    if (!schedule?.races.length) return
    const valid = schedule.races.some((r) => r.round === round)
    if (round == null || !valid) {
      // Results post a few hours after lights-out, so treat a race as "done" only
      // ~4h past its start — otherwise we'd default to an in-progress round with no results.
      const cutoff = Date.now() - 4 * 60 * 60 * 1000
      const past = schedule.races.filter((r) => {
        const dt = raceDateTime(r.date, r.time)
        return dt && dt.getTime() <= cutoff
      })
      const def = past.length ? past[past.length - 1].round : schedule.races[0].round
      setRound(def)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [schedule])

  // Keep the URL in sync so links are shareable.
  useEffect(() => {
    if (round != null) setParams({ season, round: String(round) }, { replace: true })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [season, round])

  const onSeason = (s) => { setSeason(s); setRound(null) }

  return (
    <div className="page"><div className="container">
      <header className="page-head">
        <div className="eyebrow">Classification · race &amp; qualifying</div>
        <div className="row between wrap" style={{ gap: 14 }}>
          <div className="page-title"><h1>Results</h1></div>
          <div className="row wrap" style={{ gap: 10 }}>
            <div className="seg">
              <button className={view === 'race' ? 'on' : ''} onClick={() => setView('race')}>Race</button>
              <button className={view === 'quali' ? 'on' : ''} onClick={() => setView('quali')}>Qualifying</button>
            </div>
            <SeasonSelect value={season} onChange={onSeason} />
            <label className="field">
              Round
              <select className="input" value={round ?? ''} onChange={(e) => setRound(Number(e.target.value))} disabled={!schedule}>
                {(schedule?.races || []).map((r) => (
                  <option key={r.round} value={r.round}>R{r.round} · {r.name.replace(' Grand Prix', ' GP')}</option>
                ))}
              </select>
            </label>
          </div>
        </div>
      </header>

      {round == null
        ? <Card title="Results" icon="flag"><TableSkeleton rows={12} /></Card>
        : view === 'race'
          ? <RaceResult season={season} round={round} />
          : <QualiResult season={season} round={round} />}
    </div></div>
  )
}

function RaceHeader({ name, circuit, date, time, right }) {
  const dt = raceDateTime(date, time)
  return (
    <div className="row between wrap mb" style={{ gap: 10 }}>
      <div>
        <h2><Flag country={circuit?.Location?.country || circuit?.country} /> {name}</h2>
        <div className="muted">{circuit?.circuitName || circuit?.name} · {fmtDateFull(dt)}</div>
      </div>
      {right}
    </div>
  )
}

function RaceResult({ season, round }) {
  const { data, loading, error, refetch } = useFetch(() => getResults(season, round), [season, round])
  if (loading && !data) return <Card title="Full Classification" icon="flag"><TableSkeleton rows={12} /></Card>
  if (error) return <ErrorState error={error} onRetry={refetch} />
  if (!data?.results.length) return <EmptyState title="No race result">This round hasn’t been run yet, or results aren’t available.</EmptyState>

  const fastest = data.results.find((r) => r.fastestLap?.rank === 1)
  const winner = data.results[0]

  return (
    <div className="fade-in">
      <RaceHeader name={data.name} circuit={data.circuit} date={data.date} time={data.time}
        right={<span className="chip">Round {data.round} · {data.season}</span>} />
      <Podium results={data.results.slice(0, 3)} />
      <Card title="Full Classification" icon="flag" className="mt">
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th className="c">Pos</th>
                <th>Driver</th>
                <th className="c hide-sm">Grid</th>
                <th className="r">Time / Status</th>
                <th className="c">Pts</th>
                <th className="c hide-sm">Fastest</th>
              </tr>
            </thead>
            <tbody>
              {data.results.map((r) => {
                const gained = r.grid > 0 ? r.grid - r.position : 0
                return (
                  <tr key={r.driver.id}>
                    <td className="c"><Pos n={r.positionText === 'R' || isNaN(r.position) ? '—' : r.position} /></td>
                    <td><DriverCell driver={r.driver} constructor={r.constructor}
                      subtitle={<><Flag nat={r.driver.nationality} /> {r.constructor.name}</>} /></td>
                    <td className="c hide-sm">
                      <span className="num">{r.grid || '—'}</span>{' '}
                      {gained !== 0 && r.status === 'Finished' && (
                        <small style={{ color: gained > 0 ? 'var(--good)' : 'var(--bad)' }}>
                          {gained > 0 ? `▲${gained}` : `▼${-gained}`}
                        </small>
                      )}
                    </td>
                    <td className="r mono">{r.time || r.status}</td>
                    <td className="c num">{r.points ? r.points : '—'}</td>
                    <td className="c hide-sm">
                      {r.fastestLap?.rank === 1
                        ? <Badge kind="purple" title={`Fastest lap: ${r.fastestLap.time}`}><Icon name="zap" size={12} /> {r.fastestLap.time}</Badge>
                        : r.fastestLap?.time ? <span className="mono faint">{r.fastestLap.time}</span> : '—'}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </Card>

      <div className="muted mt row wrap gap-sm" style={{ fontSize: '.86rem' }}>
        <span className="row gap-sm"><Icon name="trophy" size={14} style={{ color: 'var(--gold)' }} /> Winner: <b style={{ color: 'var(--text)' }}>{winner.driver.name}</b></span>
        {fastest && <span className="row gap-sm">· <Icon name="zap" size={14} style={{ color: 'var(--purple)' }} /> Fastest lap: <b style={{ color: 'var(--purple)' }}>{fastest.driver.name}</b> ({fastest.fastestLap.time})</span>}
      </div>
    </div>
  )
}

function Podium({ results }) {
  const order = [1, 0, 2] // 2nd, 1st, 3rd for a visual podium
  const heights = { 0: 120, 1: 92, 2: 74 }
  const rankColor = { 0: 'var(--gold)', 1: 'var(--silver)', 2: 'var(--bronze)' }
  return (
    <div className="grid cols-3 podium" style={{ alignItems: 'end', gap: 14 }}>
      {order.map((idx) => {
        const r = results[idx]
        if (!r) return <div key={idx} />
        const color = teamColor(r.constructor.id)
        const rc = rankColor[idx]
        return (
          <div key={idx} className={`card podium-card order-${idx}`} style={{ overflow: 'hidden' }}>
            <div className="card-body" style={{ textAlign: 'center' }}>
              <div style={{
                width: 40, height: 40, margin: '0 auto', borderRadius: '50%', display: 'grid', placeItems: 'center',
                fontFamily: 'var(--mono)', fontWeight: 700, fontSize: '1.15rem', color: rc,
                border: `2px solid ${rc}`, boxShadow: `0 0 22px -4px ${rc}`,
              }}>{idx + 1}</div>
              <div style={{ fontWeight: 800, marginTop: 10 }}>{r.driver.given} <span className="uppercase">{r.driver.family}</span></div>
              <div className="muted row gap-sm" style={{ fontSize: '.85rem', justifyContent: 'center' }}>
                <span style={{ width: 8, height: 8, borderRadius: '50%', background: color, boxShadow: `0 0 8px ${color}` }} /> {r.constructor.name}
              </div>
              <div className="mono muted mt" style={{ fontSize: '.85rem' }}>{idx === 0 ? r.time : r.time || r.status}</div>
            </div>
            <div style={{ height: heights[idx], background: `linear-gradient(180deg, ${color}2b, transparent)`, borderTop: `1px solid ${color}55` }} />
          </div>
        )
      })}
    </div>
  )
}

function QualiResult({ season, round }) {
  const { data, loading, error, refetch } = useFetch(() => getQualifying(season, round), [season, round])
  if (loading && !data) return <Card title="Qualifying Classification" icon="target"><TableSkeleton rows={12} /></Card>
  if (error) return <ErrorState error={error} onRetry={refetch} />
  if (!data?.results.length) return <EmptyState title="No qualifying data" icon="clock">Qualifying isn’t available for this round.</EmptyState>

  // Pole time is the best Q3 (fallback Q2/Q1) of P1.
  const pole = data.results[0]
  const poleTime = pole?.q3 || pole?.q2 || pole?.q1

  return (
    <div className="fade-in">
      <div className="row between wrap mb" style={{ gap: 10 }}>
        <h2 className="row gap-sm"><Icon name="clock" size={22} /> {data.name} · Qualifying</h2>
        <span className="chip">Round {data.round} · {data.season}</span>
      </div>
      <Card title="Qualifying Classification" icon="target">
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th className="c">Pos</th><th>Driver</th>
                <th className="r">Q1</th><th className="r">Q2</th><th className="r">Q3</th>
              </tr>
            </thead>
            <tbody>
              {data.results.map((r) => (
                <tr key={r.driver.id}>
                  <td className="c"><Pos n={r.position} /></td>
                  <td><DriverCell driver={r.driver} constructor={r.constructor}
                    subtitle={<><Flag nat={r.driver.nationality} /> {r.constructor.name}</>} /></td>
                  <td className="r mono faint">{r.q1 || '—'}</td>
                  <td className="r mono faint">{r.q2 || '—'}</td>
                  <td className="r mono" style={{ color: r.q3 ? 'var(--purple)' : 'var(--faint)', fontWeight: r.position === 1 ? 700 : 400 }}>{r.q3 || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
      {poleTime && <div className="muted mt row gap-sm" style={{ fontSize: '.86rem' }}><Icon name="target" size={14} style={{ color: 'var(--purple)' }} /> Pole: <b style={{ color: 'var(--text)' }}>{pole.driver.name}</b> — <span className="mono">{poleTime}</span></div>}
    </div>
  )
}
