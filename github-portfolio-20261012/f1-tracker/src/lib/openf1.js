// Client for the OpenF1 API — live & historical F1 timing/telemetry.
// Docs: https://openf1.org  ·  Base: https://api.openf1.org/v1
//
// Static session metadata is cached; live-timing endpoints are fetched fresh so
// polling always sees new data.

import { createLimiter } from './limiter'

const BASE = 'https://api.openf1.org/v1'
const cache = new Map()

// OpenF1 429s on bursts → keep at most 2 in flight, ~180ms apart.
const limit = createLimiter({ concurrency: 2, minGapMs: 180 })

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

// Retry on 429 (rate limit) with exponential backoff, honoring Retry-After.
// A 12s AbortController timeout guarantees a hung request rejects (and frees its
// limiter slot) rather than wedging the client forever.
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
    await sleep(Number.isFinite(ra) && ra > 0 ? Math.min(ra * 1000, 10000) : Math.min(700 * 2 ** attempt, 5000))
  }
  return res
}

async function get(endpoint, params = {}, { ttl = 0 } = {}) {
  const qs = new URLSearchParams(params).toString()
  const url = `${BASE}/${endpoint}${qs ? `?${qs}` : ''}`
  const now = Date.now()
  if (ttl > 0) {
    const hit = cache.get(url)
    if (hit && now - hit.at < ttl) return hit.data
  }
  const res = await limit(() => fetchWithRetry(url))
  if (!res.ok) throw new Error(`OpenF1 ${res.status} for ${endpoint}`)
  const data = await res.json()
  if (!Array.isArray(data)) {
    // OpenF1 returns an error object rather than an array on bad queries.
    throw new Error(data?.detail || data?.message || `OpenF1 unexpected response for ${endpoint}`)
  }
  if (ttl > 0) cache.set(url, { at: now, data })
  return data
}

const HOUR = 60 * 60 * 1000

// --- Sessions --------------------------------------------------------------
export async function getLatestSession() {
  const rows = await get('sessions', { session_key: 'latest' }, { ttl: 60 * 1000 })
  return rows[0] || null
}

// --- Static-ish per session ------------------------------------------------
export async function getDrivers(sessionKey) {
  const rows = await get('drivers', { session_key: sessionKey }, { ttl: HOUR })
  // Deduplicate: OpenF1 can list a driver more than once per session.
  const byNum = new Map()
  for (const d of rows) if (!byNum.has(d.driver_number)) byNum.set(d.driver_number, d)
  return [...byNum.values()]
}

// --- Timing / classification ----------------------------------------------
// A finished session's data is immutable, so pass a long ttl to cache it and
// avoid re-fetching (and rate-limiting) on every visit. Live sessions pass ttl=0.
export function getSessionResult(sessionKey, ttl = 0) {
  return get('session_result', { session_key: sessionKey }, { ttl })
}
export function getStints(sessionKey, ttl = 0) {
  return get('stints', { session_key: sessionKey }, { ttl })
}
export function getWeather(sessionKey, ttl = 0) {
  return get('weather', { session_key: sessionKey }, { ttl })
}
export function getRaceControl(sessionKey, ttl = 0) {
  return get('race_control', { session_key: sessionKey }, { ttl })
}
export function getTeamRadio(sessionKey, ttl = 0) {
  return get('team_radio', { session_key: sessionKey }, { ttl })
}
export function getPits(sessionKey, ttl = 0) {
  return get('pit', { session_key: sessionKey }, { ttl })
}
export function getPositions(sessionKey, ttl = 0) {
  return get('position', { session_key: sessionKey }, { ttl })
}
export function getLaps(sessionKey, ttl = 0) {
  return get('laps', { session_key: sessionKey }, { ttl })
}

// --- Reducers --------------------------------------------------------------
// Keep only the most recent record per driver_number (by ISO date field).
export function latestPerDriver(records, dateField = 'date') {
  const out = new Map()
  for (const r of records) {
    const prev = out.get(r.driver_number)
    if (!prev || (r[dateField] && r[dateField] > prev[dateField])) out.set(r.driver_number, r)
  }
  return out
}

// Latest stint per driver = highest stint_number.
export function latestStintPerDriver(stints) {
  const out = new Map()
  for (const s of stints) {
    const prev = out.get(s.driver_number)
    if (!prev || s.stint_number > prev.stint_number) out.set(s.driver_number, s)
  }
  return out
}

// All stints per driver, ordered — for the stint strip.
export function stintsByDriver(stints) {
  const out = new Map()
  for (const s of stints) {
    if (!out.has(s.driver_number)) out.set(s.driver_number, [])
    out.get(s.driver_number).push(s)
  }
  for (const arr of out.values()) arr.sort((a, b) => a.stint_number - b.stint_number)
  return out
}

// Latest completed lap per driver.
export function latestLapPerDriver(laps) {
  const out = new Map()
  for (const l of laps) {
    if (l.lap_duration == null) continue
    const prev = out.get(l.driver_number)
    if (!prev || l.lap_number > prev.lap_number) out.set(l.driver_number, l)
  }
  return out
}

// Fastest (min duration) racing lap per driver — excludes pit-out laps.
// More meaningful than "last lap", which is often a slow in/cool-down lap.
export function fastestLapPerDriver(laps) {
  const out = new Map()
  for (const l of laps) {
    if (l.lap_duration == null || l.is_pit_out_lap) continue
    const prev = out.get(l.driver_number)
    if (!prev || l.lap_duration < prev.lap_duration) out.set(l.driver_number, l)
  }
  return out
}

// Count of pit stops per driver.
export function pitCountByDriver(pits) {
  const out = new Map()
  for (const p of pits) out.set(p.driver_number, (out.get(p.driver_number) || 0) + 1)
  return out
}

// Is the session happening right now? (with a small margin either side)
export function isSessionLive(session, now = new Date()) {
  if (!session || session.is_cancelled) return false
  const start = new Date(session.date_start)
  const end = new Date(session.date_end)
  const t = now.getTime()
  return t >= start.getTime() - 5 * 60 * 1000 && t <= end.getTime() + 10 * 60 * 1000
}
