import { useState } from 'react'

// Detailed side-profile F1 car (nose to the right) rendered from a team livery.
// Wheels spin; pauses under reduced-motion.

function Wheel({ cx, cy, r, rim, dark, accent, animate }) {
  const holes = []
  for (let i = 0; i < 6; i++) {
    const a = (i * Math.PI) / 3
    holes.push([cx + Math.cos(a) * r * 0.46, cy + Math.sin(a) * r * 0.46])
  }
  return (
    <g>
      <circle cx={cx} cy={cy} r={r} fill="#0f0f13" />
      <circle cx={cx} cy={cy} r={r} fill="none" stroke="#050506" strokeWidth="2.5" />
      <circle cx={cx} cy={cy} r={r - 8} fill="none" stroke={accent} strokeWidth="3" opacity="0.5" />
      <circle cx={cx} cy={cy} r={r - 13} fill="#191920" />
      <g>
        <circle cx={cx} cy={cy} r={r * 0.55} fill={rim} />
        {holes.map(([x, y], i) => <circle key={i} cx={x} cy={y} r={r * 0.1} fill="#0c0c10" />)}
        <circle cx={cx} cy={cy} r={r * 0.17} fill={dark} />
        <circle cx={cx} cy={cy} r={r * 0.06} fill={accent} />
        {animate && (
          <animateTransform attributeName="transform" attributeType="XML" type="rotate"
            from={`0 ${cx} ${cy}`} to={`360 ${cx} ${cy}`} dur="0.55s" repeatCount="indefinite" />
        )}
      </g>
    </g>
  )
}

export default function F1Car({ livery, number, className }) {
  const { body, accent = '#E8E8EE', dark = '#101014', rim = '#C8C8CE' } = livery || {}
  const [reduce] = useState(() => {
    try { return window.matchMedia('(prefers-reduced-motion: reduce)').matches } catch { return false }
  })
  const wy = 202, wr = 60, rwx = 225, fwx = 665

  return (
    <svg className={className} viewBox="0 0 900 300" preserveAspectRatio="xMidYMid meet" role="img" aria-label="Formula 1 car">
      <ellipse cx="450" cy="266" rx="360" ry="12" fill="#000" opacity="0.45" />

      {/* rear wing (left, high) */}
      <rect x="62" y="86" width="17" height="96" rx="3" fill={dark} />
      <rect x="62" y="94" width="104" height="15" rx="3" fill={dark} />
      <rect x="62" y="91" width="104" height="4.5" rx="2" fill={accent} />
      <rect x="78" y="116" width="80" height="10" rx="3" fill={dark} />
      <rect x="96" y="170" width="62" height="8" rx="3" fill={dark} />
      <path d="M 150 120 C 152 146 152 164 160 180" fill="none" stroke={dark} strokeWidth="9" strokeLinecap="round" />

      {/* floor shadow */}
      <path d="M 160 218 L 800 218 C 808 218 810 224 800 226 L 160 226 C 150 226 150 218 160 218 Z" fill={dark} opacity="0.85" />

      {/* main body */}
      <path d="M 158 210 C 150 210 147 195 150 181 C 154 163 174 161 208 162
               C 254 163 288 163 314 155 C 332 149 338 141 352 140
               C 374 138 388 145 412 149 C 456 156 500 156 546 165
               C 604 175 654 187 704 199 C 744 209 772 213 792 214
               C 802 215 804 220 795 221 C 690 223 360 223 250 220
               C 206 218 176 217 158 210 Z" fill={body} />

      {/* livery flash */}
      <path d="M 322 202 C 420 187 540 185 660 197 C 720 203 760 209 792 213"
            fill="none" stroke={accent} strokeWidth="6" strokeLinecap="round" opacity="0.92" />

      {/* sidepod + intake */}
      <path d="M 316 178 C 344 164 392 162 444 168 C 480 172 486 200 446 208
               L 334 210 C 312 210 304 190 316 178 Z" fill={body} />
      <path d="M 298 185 C 305 175 319 173 331 179 C 342 185 342 199 332 206
               C 316 210 296 203 296 193 Z" fill={dark} />

      {/* airbox / roll-hoop */}
      <path d="M 332 152 C 334 122 346 104 360 104 C 373 104 383 124 387 151
               C 369 144 349 146 332 152 Z" fill={body} />
      <path d="M 340 132 C 346 118 356 112 364 116 C 360 130 350 134 340 132 Z" fill={dark} />

      {/* cockpit + halo + helmet */}
      <path d="M 398 150 C 412 141 452 140 484 148 L 478 160 C 446 154 416 155 400 158 Z" fill={dark} />
      <ellipse cx="448" cy="144" rx="16" ry="14" fill={accent} />
      <ellipse cx="454" cy="144" rx="8" ry="12" fill={dark} opacity="0.5" />
      <path d="M 492 150 C 466 126 416 126 397 151" fill="none" stroke="#0b0b0e" strokeWidth="9" strokeLinecap="round" />
      <rect x="403" y="131" width="7" height="21" rx="2" fill="#0b0b0e" />

      {/* nose tip */}
      <path d="M 792 214 C 812 214 828 218 836 222 C 828 226 812 228 792 226 Z" fill={body} />

      {/* front wing (right, low) */}
      <rect x="740" y="220" width="120" height="10" rx="3" fill={dark} />
      <rect x="740" y="217" width="120" height="4" rx="2" fill={accent} />
      <rect x="754" y="209" width="94" height="8" rx="3" fill={dark} />
      <rect x="846" y="202" width="14" height="40" rx="3" fill={dark} />

      {/* driver number roundel */}
      {number != null && (
        <g>
          <circle cx="300" cy="176" r="17" fill={dark} />
          <text x="300" y="183" textAnchor="middle" fontFamily="'Roboto Mono', monospace" fontWeight="700" fontSize="18" fill={accent}>{number}</text>
        </g>
      )}

      {/* wheels (open-wheel, drawn on top) */}
      <Wheel cx={rwx} cy={wy} r={wr} rim={rim} dark={dark} accent={accent} animate={!reduce} />
      <Wheel cx={fwx} cy={wy} r={wr} rim={rim} dark={dark} accent={accent} animate={!reduce} />
    </svg>
  )
}
