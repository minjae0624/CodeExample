// Team identity: colors keyed by Ergast/Jolpica constructorId, plus flag helpers.
// Team NAME is always shown next to the color, so color is a secondary cue.

const TEAM_COLORS = {
  mercedes:     '#27F4D2',
  ferrari:      '#E8002D',
  mclaren:      '#FF8000',
  red_bull:     '#3671C6',
  williams:     '#1868DB',
  alpine:       '#00A1E8',
  rb:           '#6C98FF', // Racing Bulls
  alphatauri:   '#6C98FF', // legacy id, same lineage
  aston_martin: '#229971',
  haas:         '#9AA0A6',
  audi:         '#B41E3C',
  sauber:       '#52E252', // pre-2026 identity of the Audi entry
  cadillac:     '#C9A24A',
  // historical ids that may appear when browsing past seasons
  alfa:         '#900000',
  alpha_romeo:  '#900000',
  renault:      '#FFF500',
  racing_point: '#F596C8',
  force_india:  '#F596C8',
  toro_rosso:   '#469BFF',
  lotus_f1:     '#FFB800',
}

const DEFAULT_TEAM = '#8a8a99'

export function teamColor(constructorId) {
  if (!constructorId) return DEFAULT_TEAM
  return TEAM_COLORS[constructorId] || DEFAULT_TEAM
}

// Richer livery palettes for the detailed car render: body / accent / dark / rim.
const LIVERY = {
  mercedes:     { body: '#00D7B6', accent: '#E8E8EE', dark: '#0C0C10', rim: '#D6D6DC' },
  ferrari:      { body: '#E8002D', accent: '#FFE000', dark: '#14100F', rim: '#D2D2D8' },
  mclaren:      { body: '#FF8000', accent: '#2FD2FF', dark: '#141414', rim: '#1a1a1a' },
  red_bull:     { body: '#0E1A47', accent: '#E4002B', dark: '#0A0A0A', rim: '#C8C8CE' },
  williams:     { body: '#1868DB', accent: '#48B8FF', dark: '#0B1E3F', rim: '#C8C8CE' },
  alpine:       { body: '#0093CC', accent: '#FF57A6', dark: '#0C1B2A', rim: '#C8C8CE' },
  rb:           { body: '#1E3A6E', accent: '#E4002B', dark: '#111318', rim: '#C8C8CE' },
  aston_martin: { body: '#00594F', accent: '#CEDC00', dark: '#0A1512', rim: '#C8C8CE' },
  haas:         { body: '#C7CBCE', accent: '#E4002B', dark: '#161616', rim: '#1a1a1a' },
  audi:         { body: '#15181C', accent: '#E4002B', dark: '#0A0A0A', rim: '#C8C8CE' },
  cadillac:     { body: '#0C1E3E', accent: '#C7A24A', dark: '#0A0A0A', rim: '#C8C8CE' },
  sauber:       { body: '#00E701', accent: '#111', dark: '#0A2A0A', rim: '#C8C8CE' },
}

export function teamLivery(constructorId) {
  return LIVERY[constructorId] || { body: teamColor(constructorId), accent: '#E8E8EE', dark: '#101014', rim: '#C8C8CE' }
}

// OpenF1 gives a hex string without a leading '#'. Normalize + guard against black.
export function openf1Color(hex) {
  if (!hex) return DEFAULT_TEAM
  const c = hex.startsWith('#') ? hex : `#${hex}`
  return /^#0{3,6}$/i.test(c) ? DEFAULT_TEAM : c
}

// --- Flags -----------------------------------------------------------------
// Ergast uses demonyms ("British") for people and country names ("UK") for places.
// OpenF1 uses 3-letter country codes. We normalize all three to a lowercase
// ISO-3166-1 alpha-2 code, which the <Flag> component renders as an SVG via
// flag-icons (regional-indicator emoji don't render on Windows Chrome/Edge).

const NATIONALITY_ISO = {
  british: 'GB', english: 'GB', 'northern irish': 'GB', scottish: 'GB', welsh: 'GB',
  german: 'DE', italian: 'IT', spanish: 'ES', french: 'FR', dutch: 'NL',
  finnish: 'FI', australian: 'AU', mexican: 'MX', canadian: 'CA', monegasque: 'MC',
  japanese: 'JP', thai: 'TH', american: 'US', 'american-italian': 'US', danish: 'DK',
  chinese: 'CN', brazilian: 'BR', argentine: 'AR', argentinian: 'AR', austrian: 'AT',
  swiss: 'CH', belgian: 'BE', 'new zealander': 'NZ', 'new zealand': 'NZ', swedish: 'SE',
  polish: 'PL', russian: 'RU', indian: 'IN', indonesian: 'ID', portuguese: 'PT',
  colombian: 'CO', venezuelan: 'VE', hungarian: 'HU', czech: 'CZ', irish: 'IE',
  'south african': 'ZA', malaysian: 'MY', liechtensteiner: 'LI', chilean: 'CL', uruguayan: 'UY',
}

const COUNTRY_ISO = {
  uk: 'GB', 'united kingdom': 'GB', 'great britain': 'GB', england: 'GB',
  usa: 'US', 'united states': 'US', 'united states of america': 'US', america: 'US',
  uae: 'AE', 'united arab emirates': 'AE', australia: 'AU', austria: 'AT', azerbaijan: 'AZ',
  bahrain: 'BH', belgium: 'BE', brazil: 'BR', canada: 'CA', china: 'CN', france: 'FR',
  germany: 'DE', hungary: 'HU', italy: 'IT', japan: 'JP', mexico: 'MX', monaco: 'MC',
  netherlands: 'NL', portugal: 'PT', qatar: 'QA', russia: 'RU', 'saudi arabia': 'SA',
  singapore: 'SG', spain: 'ES', 'south korea': 'KR', korea: 'KR', switzerland: 'CH',
  turkey: 'TR', vietnam: 'VN', india: 'IN', malaysia: 'MY', argentina: 'AR', thailand: 'TH',
  finland: 'FI', denmark: 'DK', sweden: 'SE', 'south africa': 'ZA',
}

const CODE3_ISO = {
  GBR: 'GB', GER: 'DE', DEU: 'DE', ITA: 'IT', ESP: 'ES', FRA: 'FR', NED: 'NL', NLD: 'NL',
  FIN: 'FI', AUS: 'AU', MEX: 'MX', CAN: 'CA', MON: 'MC', MCO: 'MC', JPN: 'JP', THA: 'TH',
  USA: 'US', DEN: 'DK', DNK: 'DK', CHN: 'CN', BRA: 'BR', BRZ: 'BR', ARG: 'AR', AUT: 'AT',
  SUI: 'CH', CHE: 'CH', BEL: 'BE', NZL: 'NZ', SWE: 'SE', POL: 'PL', RUS: 'RU', IND: 'IN',
  POR: 'PT', PRT: 'PT', BRN: 'BH', BHR: 'BH', KSA: 'SA', SAU: 'SA', ARE: 'AE', UAE: 'AE',
  QAT: 'QA', SGP: 'SG', AZE: 'AZ', HUN: 'HU', TUR: 'TR', KOR: 'KR', VNM: 'VN', VIE: 'VN',
  ZAF: 'ZA', RSA: 'ZA',
}

// Each resolver returns a lowercase alpha-2 code (e.g. 'gb') or '' if unknown.
export function natISO(nat) {
  if (!nat) return ''
  return (NATIONALITY_ISO[nat.toLowerCase().trim()] || '').toLowerCase()
}
export function countryISO(country) {
  if (!country) return ''
  return (COUNTRY_ISO[country.toLowerCase().trim()] || '').toLowerCase()
}
export function codeISO(code3) {
  if (!code3) return ''
  return (CODE3_ISO[code3.toUpperCase().trim()] || '').toLowerCase()
}
