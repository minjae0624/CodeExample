// Line-icon set (Lucide/Feather style) — stroke = currentColor so CSS colors them.
// Replaces emoji for a consistent, professional visual language.

const P = {
  home: <><path d="M3 10.2 12 3l9 7.2" /><path d="M5 9v11h5v-6h4v6h5V9" /></>,
  trophy: <><path d="M7 4h10v5a5 5 0 0 1-10 0V4Z" /><path d="M7 6H4v1a3 3 0 0 0 3 3" /><path d="M17 6h3v1a3 3 0 0 1-3 3" /><path d="M9.5 20h5" /><path d="M12 14v6" /></>,
  calendar: <><rect x="3" y="4.5" width="18" height="16.5" rx="2.5" /><path d="M3 9.5h18" /><path d="M8 2.5v4M16 2.5v4" /></>,
  flag: <><path d="M5 21V4" /><path d="M5 4.2c3-1.6 5 1.4 8 0s5-1.2 5-1.2v9s-2 .2-5 1.6-5-1.4-8 0" /></>,
  signal: <><path d="M8.5 8.5a5 5 0 0 0 0 7M15.5 8.5a5 5 0 0 1 0 7" /><path d="M5.5 5.5a9 9 0 0 0 0 13M18.5 5.5a9 9 0 0 1 0 13" /><circle cx="12" cy="12" r="1.4" fill="currentColor" stroke="none" /></>,
  wrench: <path d="M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18v3h3l6.3-6.3a4 4 0 0 0 5.4-5.4l-2.9 2.9-2.4-.6-.6-2.4 2.9-2.9Z" />,
  crown: <><path d="M3 8l3.7 3.2L12 5l5.3 6.2L21 8l-1.4 10.5H4.4L3 8Z" /><path d="M4.4 18.5h15.2" /></>,
  chart: <><path d="M4 21h16" /><rect x="5.5" y="11" width="3.6" height="8" rx="1.2" /><rect x="10.2" y="6" width="3.6" height="13" rx="1.2" /><rect x="14.9" y="14" width="3.6" height="5" rx="1.2" /></>,
  clipboard: <><rect x="5" y="4" width="14" height="17" rx="2.5" /><path d="M9 4V3a1.5 1.5 0 0 1 1.5-1.5h3A1.5 1.5 0 0 1 15 3v1H9Z" /><path d="M9 11h6M9 15h4" /></>,
  radio: <><path d="M4.5 9.5 18 4" /><rect x="3" y="9.5" width="18" height="11" rx="2.5" /><circle cx="8" cy="15" r="2.6" /><path d="M15 13.5h3" /></>,
  sun: <><circle cx="12" cy="12" r="4.3" /><path d="M12 2.5v2M12 19.5v2M4.4 4.4l1.5 1.5M18.1 18.1l1.5 1.5M2.5 12h2M19.5 12h2M4.4 19.6 5.9 18.1M18.1 5.9l1.5-1.5" /></>,
  rain: <><path d="M7.5 15.5a4 4 0 0 1-.5-8A5 5 0 0 1 17 8.5a3.5 3.5 0 0 1 .3 7" /><path d="M9 18.5l-.5 2M13 18.5l-.7 2.7M16 18.5l-.5 2" /></>,
  target: <><circle cx="12" cy="12" r="8" /><circle cx="12" cy="12" r="4" /><circle cx="12" cy="12" r="1" fill="currentColor" stroke="none" /></>,
  gauge: <><path d="M4.6 17.5a9 9 0 1 1 14.8 0" /><path d="M12 15l3.5-3.5" /><circle cx="12" cy="15" r="1.3" fill="currentColor" stroke="none" /></>,
  clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5.2l3.2 2" /></>,
  thermometer: <><path d="M12 3.5a2.5 2.5 0 0 1 2.5 2.5v7.6a4 4 0 1 1-5 0V6A2.5 2.5 0 0 1 12 3.5Z" /><circle cx="12" cy="17" r="1.5" fill="currentColor" stroke="none" /></>,
  wind: <><path d="M3 8h10a3 3 0 1 0-3-3" /><path d="M3 12h15a3 3 0 1 1-3 3" /><path d="M3 16h7a2.5 2.5 0 1 1-2.5 2.5" /></>,
  droplet: <path d="M12 3s6 6.4 6 10.4A6 6 0 1 1 6 13.4C6 9.4 12 3 12 3Z" />,
  zap: <path d="M13 2 4.5 13.5H11l-1 8.5 8.5-11.5H12l1-8.5Z" fill="currentColor" stroke="none" />,
  external: <><path d="M7 17 17 7" /><path d="M8.5 7H17v8.5" /></>,
  chevron: <path d="M6 9.5l6 6 6-6" />,
  info: <><circle cx="12" cy="12" r="9" /><path d="M12 11v5" /><path d="M12 7.6h.01" /></>,
  alert: <><path d="M12 3 2.6 20h18.8L12 3Z" /><path d="M12 10v4.5" /><path d="M12 17.6h.01" /></>,
  search: <><circle cx="11" cy="11" r="7" /><path d="M20 20l-3.6-3.6" /></>,
  pin: <><path d="M12 21s-6.5-5.5-6.5-11a6.5 6.5 0 0 1 13 0c0 5.5-6.5 11-6.5 11Z" /><circle cx="12" cy="10" r="2.3" /></>,
}

export default function Icon({ name, size = 20, className = '', style }) {
  const paths = P[name]
  if (!paths) return null
  return (
    <svg
      className={`ico ${className}`.trim()}
      width={size} height={size} viewBox="0 0 24 24"
      fill="none" stroke="currentColor" strokeWidth="1.75"
      strokeLinecap="round" strokeLinejoin="round"
      style={style} aria-hidden="true" focusable="false"
    >
      {paths}
    </svg>
  )
}
