# 🏁 PitWall — F1 Tracker

A full Formula 1 tracking website: live timing & telemetry, championship standings,
the race calendar with a live countdown, and browsable race + qualifying results.

Built with **React + Vite**. All data is fetched live in the browser from two free,
no-auth APIs — there is **no backend**.

## Data sources

| Source | Used for |
|--------|----------|
| [Jolpica-F1](https://github.com/jolpica/jolpica-f1) (the maintained Ergast successor) | Standings, calendar, race & qualifying results |
| [OpenF1](https://openf1.org) | Live timing tower, tyres, stints, weather, race control, team radio |

Both send `Access-Control-Allow-Origin: *`, so the app talks to them directly.

## Pages

- **Home** — season dashboard: next-race countdown, championship leaders, latest race.
- **Standings** — drivers' & constructors' championships, any season.
- **Calendar** — full schedule, next-race hero, per-session times in your timezone.
- **Results** — race classification (with podium, grid delta, fastest lap) and qualifying, for any round/season.
- **Live** — OpenF1 timing tower: positions, current tyre, stint strip, last lap, pit stops, gaps, plus weather, race-control feed and team-radio players. Auto-refreshes while a session is live; otherwise shows the most recent session's final classification.

## Run it

```bash
npm install     # already done if you cloned with node_modules
npm run dev      # http://localhost:5173
```

Build for production:

```bash
npm run build && npm run preview
```

## Deploy

The production build in `dist/` is a static SPA — host it anywhere:

- **Vercel** — `vercel deploy` (SPA rewrites already configured in `vercel.json`).
- **Netlify** — drag-and-drop `dist/` or connect the repo (`public/_redirects` handles routing).
- **Any static host** — serve `dist/` and rewrite all paths to `/index.html`.

No environment variables, no server, no keys.

## Engineering notes

- **Code-split routes** — each page ships as its own chunk over an ~84 KB gzip core.
- **Per-page titles** and OG/meta tags for sharing.
- **Error boundary** per route — one bad payload can't blank the app.
- **Rate-limit hardening** — both API clients have a concurrency limiter, 429 retry with backoff, request timeouts, and TTL caches.
- Skeleton loading states; `prefers-reduced-motion` respected; WCAG-AA text colors.

## Notes

- Jolpica is rate-limited, so responses are cached in memory per session (past seasons are cached for a day).
- The "Live" page only polls the network while a session is actually running; a finished session is fetched once.
- Unofficial project — not associated with Formula 1, the FIA, or any team.
