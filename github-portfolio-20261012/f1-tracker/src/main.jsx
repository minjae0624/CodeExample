import React, { Suspense, lazy } from 'react'
import ReactDOM from 'react-dom/client'
import { createBrowserRouter, RouterProvider, useRouteError } from 'react-router-dom'
import Layout from './components/Layout'
import { ErrorState } from './components/ui'
import './index.css'

// Static shell for router-level errors — never re-renders app code that may
// itself be the thing that crashed.
function RootError() {
  const err = useRouteError()
  return (
    <div className="app">
      <header className="nav">
        <div className="container nav-inner">
          <a href="/" className="brand" aria-label="PitWall home">
            <span className="mark" aria-hidden />
            <span><b>PIT</b><span style={{ color: 'var(--accent-2)' }}>WALL</span></span>
          </a>
        </div>
      </header>
      <main className="grow"><div className="page"><div className="container">
        <ErrorState
          label="Something went wrong"
          error={err?.message || String(err || 'Unknown error')}
          onRetry={() => window.location.assign('/')}
        />
      </div></div></main>
    </div>
  )
}

// Code-split per page — each route ships only what it needs.
// If a hashed chunk 404s (a redeploy replaced it mid-session), reload once to
// pick up the new build instead of dead-ending in the error boundary.
const lazyPage = (loader) => lazy(async () => {
  try {
    const mod = await loader()
    sessionStorage.removeItem('pw-chunk-reloaded')
    return mod
  } catch (err) {
    if (!sessionStorage.getItem('pw-chunk-reloaded')) {
      sessionStorage.setItem('pw-chunk-reloaded', '1')
      window.location.reload()
      await new Promise(() => {}) // suppress the error flash while reloading
    }
    throw err
  }
})
const Home = lazyPage(() => import('./pages/Home'))
const Standings = lazyPage(() => import('./pages/Standings'))
const Calendar = lazyPage(() => import('./pages/Calendar'))
const Results = lazyPage(() => import('./pages/Results'))
const Live = lazyPage(() => import('./pages/Live'))

function PageFallback() {
  return (
    <div className="page"><div className="container">
      <div className="state"><div className="spinner" /></div>
    </div></div>
  )
}

function RouteError() {
  return (
    <div className="page"><div className="container">
      <ErrorState label="Page not found" error="This page doesn't exist. Head back to the pit wall." />
    </div></div>
  )
}

const withSuspense = (el) => <Suspense fallback={<PageFallback />}>{el}</Suspense>

const router = createBrowserRouter([
  {
    path: '/',
    element: <Layout />,
    errorElement: <RootError />,
    children: [
      { index: true, element: withSuspense(<Home />) },
      { path: 'standings', element: withSuspense(<Standings />) },
      { path: 'schedule', element: withSuspense(<Calendar />) },
      { path: 'results', element: withSuspense(<Results />) },
      { path: 'live', element: withSuspense(<Live />) },
      { path: '*', element: <RouteError /> },
    ],
  },
])

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <RouterProvider router={router} />
  </React.StrictMode>,
)
