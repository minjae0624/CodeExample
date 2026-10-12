import { useState } from 'react'
import { getDriverStandings, getConstructorStandings, getSeasons } from '../lib/jolpica'
import { useFetch, useTitle } from '../lib/hooks'
import { teamColor } from '../lib/teams'
import { pts } from '../lib/format'
import { Card, DriverCell, Pos, Sparkbar, TeamStripe, ErrorState, EmptyState, Flag, TableSkeleton } from '../components/ui'

export default function Standings() {
  useTitle('Standings')
  const [tab, setTab] = useState('drivers')
  const [season, setSeason] = useState('current')

  return (
    <div className="page"><div className="container">
      <header className="page-head">
        <div className="eyebrow">World Championship · points table</div>
        <div className="row between wrap" style={{ gap: 14 }}>
          <div className="page-title"><h1>Standings</h1></div>
          <div className="row wrap" style={{ gap: 10 }}>
            <div className="seg">
              <button className={tab === 'drivers' ? 'on' : ''} onClick={() => setTab('drivers')}>Drivers</button>
              <button className={tab === 'constructors' ? 'on' : ''} onClick={() => setTab('constructors')}>Constructors</button>
            </div>
            <SeasonSelect value={season} onChange={setSeason} />
          </div>
        </div>
      </header>

      {tab === 'drivers'
        ? <DriverTable season={season} />
        : <ConstructorTable season={season} />}
    </div></div>
  )
}

export function SeasonSelect({ value, onChange }) {
  const { data: seasons } = useFetch(getSeasons, [])
  return (
    <label className="field">
      Season
      <select className="input" value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="current">Current</option>
        {(seasons || []).map((s) => <option key={s} value={s}>{s}</option>)}
      </select>
    </label>
  )
}

function DriverTable({ season }) {
  const { data, loading, error, refetch } = useFetch(() => getDriverStandings(season), [season])
  if (error) return <ErrorState error={error} onRetry={refetch} />
  const leaderPts = data?.standings[0]?.points ?? 0
  const max = leaderPts || 1 // sparkbar denominator only

  return (
    <div className="fade-in">
    <Card
      title={`Drivers’ Championship`}
      icon="trophy"
      right={data && <span className="chip">{data.season}{data.round ? ` · after Round ${data.round}` : ''}</span>}
    >
      {loading && !data ? <TableSkeleton rows={12} />
        : !data?.standings.length ? <EmptyState title="No standings yet">The season hasn’t started.</EmptyState>
        : (
      <div className="table-wrap">
        <table className="data">
          <thead>
            <tr>
              <th className="c">Pos</th>
              <th>Driver</th>
              <th className="hide-sm">Team</th>
              <th style={{ width: '28%' }}>Points</th>
              <th className="r hide-sm">Gap</th>
              <th className="c">Wins</th>
            </tr>
          </thead>
          <tbody>
            {data.standings.map((s) => (
              <tr key={s.driver.id}>
                <td className="c"><Pos n={s.position} /></td>
                <td>
                  <DriverCell driver={s.driver} constructor={s.constructor}
                    subtitle={<><Flag nat={s.driver.nationality} /> {s.constructor?.name || ''}</>} />
                </td>
                <td className="hide-sm muted">{s.constructor?.name}</td>
                <td>
                  <div className="row" style={{ gap: 10 }}>
                    <b className="num" style={{ minWidth: 42 }}>{pts(s.points)}</b>
                    <Sparkbar value={s.points} max={max} color={teamColor(s.constructor?.id)} />
                  </div>
                </td>
                <td className="r mono muted hide-sm">{s.position === 1 ? '—' : leaderPts - s.points === 0 ? '0' : `−${pts(leaderPts - s.points)}`}</td>
                <td className="c num">{s.wins || '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
        )}
    </Card>
    </div>
  )
}

function ConstructorTable({ season }) {
  const { data, loading, error, refetch } = useFetch(() => getConstructorStandings(season), [season])
  if (error) return <ErrorState error={error} onRetry={refetch} />
  const leaderPts = data?.standings[0]?.points ?? 0
  const max = leaderPts || 1 // sparkbar denominator only

  return (
    <div className="fade-in">
    <Card
      title="Constructors’ Championship"
      icon="wrench"
      right={data && <span className="chip">{data.season}{data.round ? ` · after Round ${data.round}` : ''}</span>}
    >
      {loading && !data ? <TableSkeleton rows={11} />
        : !data?.standings.length ? <EmptyState title="No standings yet">The season hasn’t started.</EmptyState>
        : (
      <div className="table-wrap">
        <table className="data">
          <thead>
            <tr>
              <th className="c">Pos</th>
              <th>Team</th>
              <th style={{ width: '34%' }}>Points</th>
              <th className="r hide-sm">Gap</th>
              <th className="c">Wins</th>
            </tr>
          </thead>
          <tbody>
            {data.standings.map((s) => {
              const color = teamColor(s.constructor.id)
              return (
                <tr key={s.constructor.id}>
                  <td className="c"><Pos n={s.position} /></td>
                  <td>
                    <div className="driver-cell">
                      <TeamStripe color={color} />
                      <span className="driver-name">
                        <b>{s.constructor.name}</b>
                        <span className="team"><Flag nat={s.constructor.nationality} /> {s.constructor.nationality}</span>
                      </span>
                    </div>
                  </td>
                  <td>
                    <div className="row" style={{ gap: 10 }}>
                      <b className="num" style={{ minWidth: 42 }}>{pts(s.points)}</b>
                      <Sparkbar value={s.points} max={max} color={color} />
                    </div>
                  </td>
                  <td className="r mono muted hide-sm">{s.position === 1 ? '—' : leaderPts - s.points === 0 ? '0' : `−${pts(leaderPts - s.points)}`}</td>
                  <td className="c num">{s.wins || '—'}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
        )}
    </Card>
    </div>
  )
}
