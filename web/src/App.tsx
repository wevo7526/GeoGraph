import { Suspense, lazy, useEffect, useState } from 'react'
import ApiHealthBanner from './components/ApiHealthBanner'
import Landing from './components/Landing'
import TopBar from './components/TopBar'

// Lazy on purpose: the explorer carries three.js (~1.5 MB minified), and the
// front door must not pay for it. Working pages load when entered.
const CaseStudyView = lazy(() => import('./components/CaseStudyView'))
const Explorer = lazy(() => import('./components/Explorer'))
const RelationshipPage = lazy(() => import('./components/RelationshipPage'))
const WatchlistPage = lazy(() => import('./components/WatchlistPage'))
const CasesPage = lazy(() => import('./components/CasesPage'))

// Hash routing rather than a router dependency: a handful of views, and the
// hash keeps URLs shareable — a reader can send someone the case study, which
// is the same reason node_ids are typed and stable rather than rowids.
function currentRoute(): string {
  const hash = window.location.hash.replace(/^#/, '')
  return hash.startsWith('/') ? hash : '/'
}

function Loading() {
  return (
    <div className="h-full grid place-items-center">
      <p className="mono text-xs uppercase tracking-widest" style={{ color: 'var(--muted)' }}>
        Loading the archive…
      </p>
    </div>
  )
}

export default function App() {
  const [route, setRoute] = useState(currentRoute)
  // The region is the LENS every page looks through. It survives reloads —
  // a China analyst should not wake up in MENA every morning.
  const [region, setRegion] = useState(
    () => window.localStorage.getItem('geograph.region') ?? 'mena',
  )

  useEffect(() => {
    const onHashChange = () => setRoute(currentRoute())
    window.addEventListener('hashchange', onHashChange)
    return () => window.removeEventListener('hashchange', onHashChange)
  }, [])

  const navigate = (next: string) => {
    window.location.hash = next
    // Landing on a new view scrolled halfway down the previous one reads as a
    // rendering bug rather than a navigation.
    window.scrollTo(0, 0)
  }

  const chooseRegion = (next: string) => {
    window.localStorage.setItem('geograph.region', next)
    setRegion(next)
  }

  if (route === '/' || route === '') {
    return <Landing onEnter={navigate} />
  }

  let page: React.ReactNode
  let scrollPage = false
  if (route.startsWith('/case/')) {
    page = <CaseStudyView slug={route.slice('/case/'.length)} onNavigate={navigate} />
    scrollPage = true
  } else if (route.startsWith('/cases')) {
    page = <CasesPage region={region} onNavigate={navigate} />
    scrollPage = true
  } else if (
    // Reasoning, the game and trading folded into the Relationship page; old
    // deep links (e.g. #/games?dyad=…) land there, dyad carried over.
    route.startsWith('/relationship') ||
    route.startsWith('/games') ||
    route.startsWith('/reasoning') ||
    route.startsWith('/trading')
  ) {
    page = <RelationshipPage region={region} onNavigate={navigate} />
    scrollPage = true
  } else if (route.startsWith('/watchlist')) {
    page = <WatchlistPage region={region} onNavigate={navigate} />
    scrollPage = true
  } else {
    page = <Explorer region={region} />
  }

  return (
    <div className="app-frame">
      <TopBar route={route} region={region} onRegion={chooseRegion} onNavigate={navigate} />
      <ApiHealthBanner />
      <div className={scrollPage ? 'app-page app-page--scroll' : 'app-page'}>
        <Suspense fallback={<Loading />}>{page}</Suspense>
      </div>
    </div>
  )
}
