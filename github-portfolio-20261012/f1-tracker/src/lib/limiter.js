// A tiny concurrency + rate limiter. F1's free APIs reject bursts of concurrent
// requests (HTTP 429), so we cap how many run at once and space out their starts.
// Each API gets its own limiter instance (independent rate budgets).
export function createLimiter({ concurrency = 2, minGapMs = 0 } = {}) {
  let active = 0
  let nextSlot = 0
  const waiters = []

  function drain() {
    if (active >= concurrency || waiters.length === 0) return
    const resolve = waiters.shift()
    active++
    const now = Date.now()
    const wait = Math.max(0, nextSlot - now)
    nextSlot = Math.max(now, nextSlot) + minGapMs
    setTimeout(resolve, wait)
  }

  return async function limit(fn) {
    await new Promise((resolve) => { waiters.push(resolve); drain() })
    try {
      return await fn()
    } finally {
      active--
      drain()
    }
  }
}
