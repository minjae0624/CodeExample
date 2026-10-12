import { useNow } from '../lib/hooks'
import { countdownParts } from '../lib/format'
import Icon from './Icon'

// Live countdown to a target Date. Renders D/H/M/S tiles.
export default function Countdown({ target, compact = false }) {
  const now = useNow(1000)
  if (!target) return null
  const { days, hours, minutes, seconds, done } = countdownParts(target.getTime() - now.getTime())

  if (done) return <span className="chip" style={{ color: 'var(--good)' }}><Icon name="flag" size={14} /> Lights out</span>

  const units = [
    ['Days', days], ['Hrs', hours], ['Min', minutes], ['Sec', seconds],
  ]
  const shown = compact ? units.filter(([, v], i) => i < 3 || days === 0) : units

  return (
    <div className="countdown" role="timer" aria-label="Time until race start">
      {shown.map(([u, v]) => (
        <div className="cd-unit" key={u}>
          <div className="n">{String(v).padStart(2, '0')}</div>
          <div className="u">{u}</div>
        </div>
      ))}
    </div>
  )
}
