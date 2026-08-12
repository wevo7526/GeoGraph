import { Suspense, lazy, useEffect, useState } from 'react'
import Landing from './components/Landing'

// Lazy on purpose: the explorer carries three.js (~1.5 MB minified), and the
// front door must not pay for it. The landing renders from the base chunk;
// the 3D workspace loads when someone actually enters it.
const CaseStudyView = lazy(() => import('./components/CaseStudyView'))
const Explorer = lazy(() => import('./components/Explorer'))

function Loading() {
  return (
    <div className="min-h-full grid place-items-center">
      <p className="mono text-xs uppercase tracking-widest" style={{ color: 'var(--muted)' }}>
        Loading the archive…
      </p>
    </div>
  )
}

// Hash routing rather than a router dependency: three views, and the hash
// keeps URLs shareable — a reader can send someone the case study, which is
// the same reason node_ids are typed and stable rather than rowids.
function currentRoute(): string {
  const hash = window.location.hash.replace(/^#/, '')
  return hash.startsWith('/') ? hash : '/'
}

export default function App() {
  const [route, setRoute] = useState(currentRoute)

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

  if (route.startsWith('/case/')) {
    return (
      <Suspense fallback={<Loading />}>
        <CaseStudyView slug={route.slice('/case/'.length)} onNavigate={navigate} />
      </Suspense>
    )
  }
  if (route.startsWith('/explore')) {
    return (
      <Suspense fallback={<Loading />}>
        <Explorer onNavigate={navigate} />
      </Suspense>
    )
  }
  return <Landing onEnter={navigate} />
}
