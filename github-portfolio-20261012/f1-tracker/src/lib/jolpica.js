// Client for the Jolpica-F1 API (the maintained successor to Ergast).
// Docs: https://github.com/jolpica/jolpica-f1  ·  Base: https://api.jolpi.ca/ergast/f1
//
// Jolpica is rate-limited (~4 req/s, ~500/hr), so responses are cached in memory
// for the session. Past-season data is effectively immutable → long TTL.

import { createLimiter } from './limiter'

const BASE = 'https://api.jolpi.ca/ergast/f1'

const cache = new Map() // url -> { at, ttl, data }

// Jolpica allows ~4 req/s → cap at 3 concurrent, lightly spaced.
const limit = createLimiter({ concurrency: 3, minGapMs: 120 })

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

// Retry on 429 (rate limit) with exponential backoff, honoring Retry-After.
// A 12s AbortController timeout frees the limiter slot if a request hangs.
async function fetchWithRetry(url, retries = 3) {
  let res
  for (let attempt = 0; attempt <= retries; attempt++) {
    const ctrl = new AbortController()
    const t = setTimeout(() => ctrl.abort(), 12000)
    try {
      res = await fetch(url, { signal: ctrl.signal })
    } finally {
      clearTimeout(t)
    }
    if (res.status !== 429) return res
    if (attempt === retries) break
    const ra = Number(res.headers.get('retry-after'))
    // Cap the wait so an oversized Retry-After can't hold a limiter slot too long.
    await sleep(Number.isFinite(ra) && ra > 0 ? Math.min(ra * 1000, 10000) : Math.min(700 * 2 ** attempt, 5000))
  }
  return res
}

const pending = new Map() // url -> in-flight promise (dedup concurrent callers)

async function get(path, { ttl = 5 * 60 * 1000 } = {}) {
  const url = `${BASE}/${path}`
  const hit = cache.get(url)
  const now = Date.now()
  if (hit && now - hit.at < hit.ttl) return hit.data
  if (pending.has(url)) return pending.get(url)

  const p = (async () => {
    const res = await limit(() => fetchWithRetry(url))
    if (!res.ok) {
      throw new Error(`Jolpica ${res.status} for ${path}${res.status === 429 ? ' (rate limited — try again in a moment)' : ''}`)
    }
    const json = await res.json()
    const data = json.MRData
    cache.set(url, { at: Date.now(), ttl, data })
    return data
  })().finally(() => pending.delete(url))
  pending.set(url, p)
  return p
}

const LONG = 24 * 60 * 60 * 1000 // 1 day for immutable historical data

// --- Seasons ---------------------------------------------------------------
export async function getSeasons() {
  const d = await get('seasons.json?limit=100', { ttl: LONG })
  return d.SeasonTable.Seasons.map((s) => s.season).reverse() // newest first
}

// --- Schedule --------------------------------------------------------------
export async function getSchedule(season = 'current') {
  const d = await get(`${season}.json`)
  return {
    season: d.RaceTable.season,
    races: d.RaceTable.Races.map(normalizeRace),
  }
}

function normalizeRace(r) {
  return {
    round: Number(r.round),
    name: r.raceName,
    date: r.date,
    time: r.time,
    url: r.url,
    circuit: {
      id: r.Circuit.circuitId,
      name: r.Circuit.circuitName,
      locality: r.Circuit.Location.locality,
      country: r.Circuit.Location.country,
      lat: r.Circuit.Location.lat,
      long: r.Circuit.Location.long,
    },
    sessions: {
      fp1: r.FirstPractice,
      fp2: r.SecondPractice,
      fp3: r.ThirdPractice,
      qualifying: r.Qualifying,
      sprint: r.Sprint,
      sprintQualifying: r.SprintQualifying || r.SprintShootout,
      race: { date: r.date, time: r.time },
    },
  }
}

// --- Standings -------------------------------------------------------------
export async function getDriverStandings(season = 'current') {
  const d = await get(`${season}/driverStandings.json`)
  const list = d.StandingsTable.StandingsLists[0]
  return {
    season: d.StandingsTable.season,
    round: list ? Number(list.round) : null,
    standings: (list ? list.DriverStandings : []).map((s) => ({
      position: Number(s.position),
      points: Number(s.points),
      wins: Number(s.wins),
      driver: normDriver(s.Driver),
      constructor: s.Constructors[s.Constructors.length - 1]
        ? normConstructor(s.Constructors[s.Constructors.length - 1]) : null,
    })),
  }
}

export async function getConstructorStandings(season = 'current') {
  const d = await get(`${season}/constructorStandings.json`)
  const list = d.StandingsTable.StandingsLists[0]
  return {
    season: d.StandingsTable.season,
    round: list ? Number(list.round) : null,
    standings: (list ? list.ConstructorStandings : []).map((s) => ({
      position: Number(s.position),
      points: Number(s.points),
      wins: Number(s.wins),
      constructor: normConstructor(s.Constructor),
    })),
  }
}

// --- Results & qualifying --------------------------------------------------
export async function getResults(season, round) {
  const d = await get(`${season}/${round}/results.json`, { ttl: LONG })
  const race = d.RaceTable.Races[0]
  if (!race) return null
  return {
    season: race.season,
    round: Number(race.round),
    name: race.raceName,
    date: race.date,
    time: race.time,
    circuit: race.Circuit,
    results: race.Results.map((r) => ({
      position: Number(r.position),
      positionText: r.positionText,
      points: Number(r.points),
      grid: Number(r.grid),
      laps: Number(r.laps),
      status: r.status,
      driver: normDriver(r.Driver),
      constructor: normConstructor(r.Constructor),
      time: r.Time ? r.Time.time : null,
      fastestLap: r.FastestLap
        ? { rank: Number(r.FastestLap.rank), lap: Number(r.FastestLap.lap), time: r.FastestLap.Time?.time }
        : null,
    })),
  }
}

export async function getQualifying(season, round) {
  const d = await get(`${season}/${round}/qualifying.json`, { ttl: LONG })
  const race = d.RaceTable.Races[0]
  if (!race || !race.QualifyingResults) return null
  return {
    season: race.season,
    round: Number(race.round),
    name: race.raceName,
    results: race.QualifyingResults.map((r) => ({
      position: Number(r.position),
      driver: normDriver(r.Driver),
      constructor: normConstructor(r.Constructor),
      q1: r.Q1 || null,
      q2: r.Q2 || null,
      q3: r.Q3 || null,
    })),
  }
}

// --- Normalizers -----------------------------------------------------------
function normDriver(d) {
  return {
    id: d.driverId,
    code: d.code || d.familyName.slice(0, 3).toUpperCase(),
    number: d.permanentNumber,
    given: d.givenName,
    family: d.familyName,
    name: `${d.givenName} ${d.familyName}`,
    nationality: d.nationality,
    url: d.url,
  }
}
function normConstructor(c) {
  return { id: c.constructorId, name: c.name, nationality: c.nationality, url: c.url }
}
