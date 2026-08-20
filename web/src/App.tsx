import { Suspense, lazy, useEffect, useState } from 'react'
import ApiHealthBanner from './components/ApiHealthBanner'
import AgentModal from './components/AgentModal'
import { AgentProvider } from './components/AgentSession'
import Landing from './components/Landing'
import Sidebar from './components/Sidebar'

// Lazy on purpose: the explorer carries three.js (~1.5 MB minified), and the
// front door must not pay for it. Working pages load when entered.
const CaseStudyView = lazy(() => import('./components/CaseStudyView'))
const Explorer = lazy(() => import('./components/Explorer'))
const RelationshipPage = lazy(() => import('./components/RelationshipPage'))
const CasesPage = lazy(() => import('./components/CasesPage'))
const GamesPage = lazy(() => import('./components/GamesPage'))
const MarketsPage = lazy(() => import('./components/MarketsPage'))
const IntelPage = lazy(() => import('./components/IntelPage'))
const NetworkPage = lazy(() => import('./components/NetworkPage'))

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

  // A relationship can be linked WITH its region (from the Wire, or from a
  // shared URL). The lens follows what you opened: without this, opening a saved
  // China relationship while the lens is on MENA silently shows a MENA pair,
  // because its dyad id is not in the MENA list.
  //
  // On ROUTE ONLY — the link's region applies when the hash changes, once.
  // With `region` in the deps this re-fired on every manual region change and
  // snapped the lens back to the stale hash's region, making the Sidebar
  // select inert on any page reached through a region-carrying link.
  useEffect(() => {
    const q = route.split('?')[1]
    if (!q) return
    const linkedRegion = new URLSearchParams(q).get('region')
    if (linkedRegion) chooseRegion(linkedRegion)
  }, [route])

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
  } else if (route.startsWith('/games')) {
    page = <GamesPage region={region} onNavigate={navigate} />
    scrollPage = true
  } else if (route.startsWith('/markets') || route.startsWith('/trading')) {
    page = <MarketsPage region={region} onNavigate={navigate} />
    scrollPage = true
  } else if (
    // /relationships (plural, 2026-08-15) with the old singular and the
    // retired /reasoning deep links landing there, dyad carried over.
    route.startsWith('/relationship') ||
    route.startsWith('/reasoning')
  ) {
    page = <RelationshipPage region={region} onNavigate={navigate} />
    scrollPage = true
  } else if (
    route.startsWith('/intel') ||
    route.startsWith('/situation') ||
    route.startsWith('/wire')
  ) {
    page = <IntelPage region={region} onNavigate={navigate} />
    scrollPage = true
  } else if (route.startsWith('/network')) {
    page = <NetworkPage region={region} onNavigate={navigate} />
    scrollPage = true
  } else {
    page = <Explorer region={region} onNavigate={navigate} />
  }

  return (
    <AgentProvider region={region} route={route}>
      <div className="app-frame">
        <Sidebar route={route} region={region} onRegion={chooseRegion} onNavigate={navigate} />
        <div className="app-body">
          <ApiHealthBanner />
          <div className={scrollPage ? 'app-page app-page--scroll' : 'app-page'}>
            <Suspense fallback={<Loading />}>{page}</Suspense>
          </div>
        </div>
        <AgentModal region={region} route={route} onNavigate={navigate} />
      </div>
    </AgentProvider>
  )
}
