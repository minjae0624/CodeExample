import { useEffect, useRef, useState, useCallback } from 'react'

// useFetch(asyncFn, deps) — runs asyncFn on mount and when deps change.
// Ignores results from stale runs so fast filter changes can't flip-flop the UI.
export function useFetch(fn, deps = []) {
  const [state, setState] = useState({ data: null, loading: true, error: null })
  const fnRef = useRef(fn)
  fnRef.current = fn
  const [nonce, setNonce] = useState(0)
  const refetch = useCallback(() => setNonce((n) => n + 1), [])

  useEffect(() => {
    let alive = true
    setState((s) => ({ data: s.data, loading: true, error: null }))
    fnRef.current()
      .then((data) => { if (alive) setState({ data, loading: false, error: null }) })
      .catch((error) => { if (alive) setState({ data: null, loading: false, error }) })
    return () => { alive = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce])

  return { ...state, refetch }
}

// usePoll(asyncFn, deps, { interval, enabled }) — like useFetch but re-runs on a
// timer while `enabled`. Keeps previous data visible across refreshes (no flicker).
export function usePoll(fn, deps = [], { interval = 8000, enabled = true } = {}) {
  const [state, setState] = useState({ data: null, loading: true, error: null })
  const [lastUpdated, setLastUpdated] = useState(null)
  const fnRef = useRef(fn)
  fnRef.current = fn

  useEffect(() => {
    let alive = true
    let timer = null

    const tick = async () => {
      try {
        const data = await fnRef.current()
        if (!alive) return
        setState({ data, loading: false, error: null })
        setLastUpdated(new Date())
      } catch (error) {
        if (alive) setState((s) => ({ data: s.data, loading: false, error }))
      } finally {
        if (alive && enabled) timer = setTimeout(tick, interval)
      }
    }

    setState((s) => ({ data: s.data, loading: true, error: null }))
    tick()
    return () => { alive = false; if (timer) clearTimeout(timer) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, interval, enabled])

  return { ...state, lastUpdated }
}

// useTitle(title) — per-page document titles (SEO / tab clarity).
export function useTitle(title) {
  useEffect(() => {
    const prev = document.title
    document.title = title ? `${title} · PitWall` : 'PitWall — F1 Live Standings, Schedule & Timing'
    return () => { document.title = prev }
  }, [title])
}

// useNow(interval) — a ticking clock for countdowns / relative timestamps.
export function useNow(interval = 1000) {
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), interval)
    return () => clearInterval(id)
  }, [interval])
  return now
}
