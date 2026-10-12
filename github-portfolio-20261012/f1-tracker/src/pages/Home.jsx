import { useState } from 'react'
import { Link } from 'react-router-dom'
import { getSchedule, getDriverStandings, getConstructorStandings, getResults } from '../lib/jolpica'
import { useFetch, useNow, useTitle } from '../lib/hooks'
import { teamColor, teamLivery } from '../lib/teams'
import { raceDateTime, fmtDateFull, fmtTime, pts } from '../lib/format'
import { Card, DriverCell, Pos, ErrorState, TeamStripe, Flag, Sparkbar, Skeleton, TableSkeleton } from '../components/ui'
import Icon from '../components/Icon'
import Countdown from '../components/Countdown'
import F1Car from '../components/F1Car'

export default function Home() {
  useTitle('Race Control')
  const schedule = useFetch(() => getSchedule('current'), [])
  const drivers = useFetch(() => getDriverStandings('current'), [])
  const constructors = useFetch(() => getConstructorStandings('current'), [])
  const now = useNow(30000)

  const races = schedule.data?.races || []
  const next = races.map((r) => ({ ...r, dt: raceDateTime(r.date, r.time) })).find((r) => r.dt && r.dt.getTime() > now.getTime())
  const lastRound = drivers.data?.round ?? null
  const lastResult = useFetch(() => (lastRound ? getResults('current', lastRound) : Promise.resolve(null)), [lastRound])

  if (schedule.error) return <div className="page"><div className="container"><ErrorState error={schedule.error} onRetry={schedule.refetch} /></div></div>

  const dTop = drivers.data?.standings || []
  const leader = dTop[0]
  const gap = leader && dTop[1] ? leader.points - dTop[1].points : 0
  const cTop = constructors.data?.standings?.[0]
  const winner = lastResult.data?.results?.[0]

  return (
    <div className="page"><div className="container">
      <header className="page-head">
        <div className="eyebrow">Season {schedule.data?.season || '2026'} · FIA Formula One World Championship</div>
        <div className="page-title"><h1>Race Control</h1></div>
      </header>

      <div className="bento fade-in">
        <div className="col-12">
          <HomeHero next={next} loading={schedule.loading} drivers={drivers} constructors={constructors} />
        </div>

        <div className="col-3">
          <StatTile icon="calendar" label="Season progress"
            value={next ? `R${next.round}` : '—'} suffix={` / ${races.length || '—'}`}
            sub={next ? `${next.name.replace(' Grand Prix', ' GP')} next` : 'Season complete'} loading={schedule.loading && !races.length} />
        </div>
        <div className="col-3">
          <StatTile icon="crown" label="Points leader" value={leader ? pts(leader.points) : '—'}
            sub={leader ? `${leader.driver.family} · +${pts(gap)} ahead` : ''} loading={drivers.loading && !leader} />
        </div>
        <div className="col-3">
          <StatTile icon="wrench" label="Top constructor" value={cTop ? pts(cTop.points) : '—'}
            sub={cTop ? cTop.constructor.name : ''} loading={constructors.loading && !cTop} />
        </div>
        <div className="col-3">
          <StatTile icon="trophy" label="Last winner" value={winner ? winner.driver.family : '—'} valueSmall
            sub={lastResult.data ? lastResult.data.name.replace(' Grand Prix', ' GP') : ''} loading={lastResult.loading && !winner} />
        </div>

        <div className="col-7"><Championship drivers={drivers} constructors={constructors} /></div>
        <div className="col-5"><LatestRace result={lastResult} season={schedule.data?.season} /></div>
      </div>
    </div></div>
  )
}

/* One cinematic band: the next race on the left, the leader's car on the right. */
function HomeHero({ next, loading, drivers, constructors }) {
  const teams = constructors.data?.standings?.map((s) => s.constructor) || []
  const leaderTeamId = drivers.data?.standings?.[0]?.constructor?.id
  const [sel, setSel] = useState(null)
  const teamId = sel || leaderTeamId
  const team = teams.find((t) => t.id === teamId)
  const topDriver = drivers.data?.standings?.find((s) => s.constructor?.id === teamId)
  const livery = teamLivery(teamId || 'ferrari')

  return (
    <section className="home-hero" aria-label="Next race and featured car">
      <div className="hh-left">
        {next ? (
          <>
            <div className="round-tag">Next Grand Prix · Round {next.round}</div>
            <h2><Flag country={next.circuit.country} /> {next.name}</h2>
            <div className="muted mb" style={{ fontSize: '.9rem' }}>{next.circuit.name} · {next.circuit.locality}, {next.circuit.country}</div>
            <Countdown target={next.dt} />
            <div className="grow" style={{ minHeight: 14 }} />
            <div className="muted" style={{ fontSize: '.85rem' }}>Lights out {fmtDateFull(next.dt)} · {fmtTime(next.dt)}</div>
            <div className="btn-row mt">
              <Link className="btn primary" to="/schedule">Full calendar</Link>
              <Link className="btn ghost" to="/live">Live timing</Link>
            </div>
          </>
        ) : loading ? (
          <>
            <Skeleton w="40%" h={12} />
            <Skeleton w="72%" h={28} style={{ marginTop: 14 }} />
            <Skeleton w="55%" h={13} style={{ marginTop: 10 }} />
            <Skeleton w="80%" h={54} style={{ marginTop: 18 }} />
          </>
        ) : (
          <>
            <div className="round-tag">Season complete</div>
            <h2>That's a wrap.</h2>
            <p className="muted">No more races scheduled this season. See how it ended in the <Link to="/standings" style={{ color: 'var(--accent-2)' }}>final standings</Link>.</p>
          </>
        )}
      </div>

      <div className="hh-stage">
        <div className="speedlines" aria-hidden>
          {[[16, 34, 2.1], [30, 26, 2.6], [44, 40, 1.8], [58, 30, 2.9], [72, 22, 2.3], [86, 36, 2.0]].map(([top, w, d], i) => (
            <i key={i} style={{ top: `${top}%`, width: `${w}%`, animationDuration: `${d}s`, animationDelay: `${i * 0.35}s` }} />
          ))}
        </div>
        <F1Car livery={livery} number={topDriver?.driver.number} className="featured-car" />
        <div className="hh-stage-foot">
          <span className="hh-team">
            {team ? <><b>{team.name}</b>{topDriver && <> · #{topDriver.driver.number} {topDriver.driver.family} · {pts(topDriver.points)} pts</>}</> : 'Formula 1'}
          </span>
          {teams.length > 0 && (
            <div className="team-chips" role="group" aria-label="Choose a team livery">
              {teams.map((t) => (
                <button key={t.id} className={`team-chip${t.id === teamId ? ' on' : ''}`}
                  style={{ '--c': teamColor(t.id) }} title={t.name} aria-label={t.name}
                  aria-pressed={t.id === teamId}
                  onClick={() => setSel(t.id)}><span /></button>
              ))}
            </div>
          )}
        </div>
      </div>
    </section>
  )
}

function StatTile({ icon, label, value, suffix, sub, valueSmall, loading }) {
  return (
    <div className="stat-tile">
      <div className="st-top">
        <span className="st-label">{label}</span>
        <span className="st-ico"><Icon name={icon} size={15} /></span>
      </div>
      {loading
        ? <Skeleton w="60%" h={24} style={{ margin: '4px 0' }} />
        : <div className="st-value" style={valueSmall ? { fontSize: '1.3rem' } : undefined}>
            {value}{suffix && <span style={{ color: 'var(--faint)', fontWeight: 700 }}>{suffix}</span>}
          </div>}
      {loading ? <Skeleton w="80%" h={11} /> : sub && <div className="st-sub">{sub}</div>}
    </div>
  )
}

/* Championship snapshot: leader spotlight + drivers' top five + top constructor. */
function Championship({ drivers, constructors }) {
  const top5 = drivers.data?.standings?.slice(0, 5) || []
  const max = top5[0]?.points || 1
  const leader = top5[0]
  const cTop = constructors.data?.standings?.[0]
  const round = drivers.data?.round

  return (
    <Card title="Championship" icon="trophy"
      right={round && <span className="chip">after round {round}</span>}>
      {drivers.loading && !drivers.data ? <TableSkeleton rows={6} /> : leader ? (
        <>
          <div className="row" style={{ gap: 14 }}>
            <div style={{ fontSize: '1.9rem', lineHeight: 1 }}><Flag nat={leader.driver.nationality} alt={`${leader.driver.nationality} flag`} /></div>
            <div className="grow" style={{ minWidth: 0 }}>
              <div className="st-label">Drivers' leader</div>
              <div style={{ fontSize: '1.2rem', fontWeight: 800, letterSpacing: '-0.01em' }}>{leader.driver.name}</div>
              <div className="muted" style={{ fontSize: '.82rem' }}>{leader.constructor?.name} · {pts(leader.points)} pts · {leader.wins} wins</div>
            </div>
          </div>
          <div className="divider" />
          <div className="table-wrap">
            <table className="data">
              <tbody>
                {top5.map((s) => (
                  <tr key={s.driver.id}>
                    <td className="c" style={{ width: 30 }}><Pos n={s.position} /></td>
                    <td><DriverCell driver={s.driver} constructor={s.constructor} showNumber={false} /></td>
                    <td style={{ width: '38%' }}>
                      <div className="row" style={{ gap: 8 }}>
                        <b className="num" style={{ minWidth: 34 }}>{pts(s.points)}</b>
                        <Sparkbar value={s.points} max={max} color={teamColor(s.constructor?.id)} />
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {cTop && (
            <>
              <div className="divider" />
              <div className="row between">
                <div className="row" style={{ gap: 10, minWidth: 0 }}>
                  <span className="st-label">Constructors</span>
                  <TeamStripe color={teamColor(cTop.constructor.id)} />
                  <b style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{cTop.constructor.name}</b>
                </div>
                <span className="num muted">{pts(cTop.points)}</span>
              </div>
            </>
          )}
          <Link className="btn ghost mt" to="/standings" style={{ width: '100%' }}>Full standings →</Link>
        </>
      ) : <p className="muted">Standings not available yet.</p>}
    </Card>
  )
}

function LatestRace({ result, season }) {
  const data = result.data
  return (
    <Card title="Latest Race" icon="flag"
      right={data && <Link className="chip" to={`/results?season=${season}&round=${data.round}`} style={{ color: 'var(--muted)' }}>Full result →</Link>}>
      {result.loading && !data ? <TableSkeleton rows={5} />
        : !data ? <p className="muted">No completed races yet this season.</p>
          : (
            <>
              <div className="mb"><b><Flag country={data.circuit?.Location?.country} /> {data.name}</b> <span className="faint">· Round {data.round}</span></div>
              <div className="table-wrap">
                <table className="data">
                  <tbody>
                    {data.results.slice(0, 6).map((r) => (
                      <tr key={r.driver.id}>
                        <td className="c" style={{ width: 30 }}><Pos n={r.position} /></td>
                        <td><DriverCell driver={r.driver} constructor={r.constructor} showNumber={false} /></td>
                        <td className="r mono muted" style={{ fontSize: '.82rem' }}>{r.time || r.status}</td>
                        <td className="r num" style={{ width: 44 }}>{r.points ? `+${pts(r.points)}` : ''}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
    </Card>
  )
}
