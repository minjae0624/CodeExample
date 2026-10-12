// Formatting helpers for dates, timing, and numbers.

// Ergast gives date "2026-03-08" and time "04:00:00Z" separately.
export function raceDateTime(date, time) {
  if (!date) return null
  const iso = time ? `${date}T${time}` : `${date}T00:00:00Z`
  const d = new Date(iso)
  return isNaN(d) ? null : d
}

const DAY = { weekday: 'short', month: 'short', day: 'numeric' }
export function fmtDate(d, opts = DAY) {
  if (!d) return '—'
  return new Intl.DateTimeFormat(undefined, opts).format(d)
}
export function fmtDateFull(d) {
  return fmtDate(d, { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' })
}
export function fmtTime(d) {
  if (!d) return '—'
  return new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit' }).format(d)
}
export function fmtDateTime(d) {
  if (!d) return '—'
  return new Intl.DateTimeFormat(undefined, {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  }).format(d)
}
export function localTZ() {
  try { return Intl.DateTimeFormat().resolvedOptions().timeZone } catch { return 'local time' }
}

// Countdown parts from a millisecond delta.
export function countdownParts(ms) {
  const s = Math.max(0, Math.floor(ms / 1000))
  return {
    days: Math.floor(s / 86400),
    hours: Math.floor((s % 86400) / 3600),
    minutes: Math.floor((s % 3600) / 60),
    seconds: s % 60,
    done: ms <= 0,
  }
}

// Relative "in 3 days" / "2 hours ago"
export function relative(target, now = new Date()) {
  if (!target) return ''
  const diff = target.getTime() - now.getTime()
  const abs = Math.abs(diff)
  const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' })
  const units = [
    ['day', 86400000], ['hour', 3600000], ['minute', 60000],
  ]
  for (const [unit, size] of units) {
    if (abs >= size || unit === 'minute') {
      return rtf.format(Math.round(diff / size), unit)
    }
  }
  return 'now'
}

// Seconds (e.g. lap_duration 105.132) -> "1:45.132"
export function fmtLapTime(seconds) {
  if (seconds == null || isNaN(seconds)) return '—'
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  return m > 0
    ? `${m}:${s.toFixed(3).padStart(6, '0')}`
    : s.toFixed(3)
}

// Race duration seconds -> "1:27:11.335"
export function fmtRaceDuration(seconds) {
  if (seconds == null || isNaN(seconds)) return '—'
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = seconds % 60
  const ss = s.toFixed(3).padStart(6, '0')
  return h > 0 ? `${h}:${String(m).padStart(2, '0')}:${ss}` : `${m}:${ss}`
}

// Gap in seconds -> "+2.317" (leader shows the marker passed in)
export function fmtGap(seconds, leaderLabel = 'LEADER') {
  if (seconds == null || isNaN(seconds)) return '—'
  if (seconds === 0) return leaderLabel
  return `+${Number(seconds).toFixed(3)}`
}

export function pts(n) {
  const v = Number(n)
  return Number.isInteger(v) ? String(v) : v.toFixed(1).replace(/\.0$/, '')
}

export function compound(c) {
  const up = (c || '').toUpperCase()
  return ['SOFT', 'MEDIUM', 'HARD', 'INTERMEDIATE', 'WET'].includes(up) ? up : 'UNKNOWN'
}
export function compoundLetter(c) {
  return ({ SOFT: 'S', MEDIUM: 'M', HARD: 'H', INTERMEDIATE: 'I', WET: 'W' })[compound(c)] || '?'
}
