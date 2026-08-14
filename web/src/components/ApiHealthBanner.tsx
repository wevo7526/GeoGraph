import { useEffect, useState } from 'react'
import { bannerFailures, onApiFailures, type ApiFailure } from '../api'

/** BROKEN is not EMPTY. Every api helper returns null on failure so panels
 *  can render their empty states — but a 5xx or an unreachable API must not
 *  wear the same face as "the archive holds nothing here". This banner is the
 *  one place that distinction surfaces, on every page, without each panel
 *  having to carry error plumbing. 4xx answers stay with their callers
 *  (lastFailureFor) — a 404 series or a 409 sparse kernel is the API talking,
 *  not the API down. */
export default function ApiHealthBanner() {
  const [failures, setFailures] = useState<ApiFailure[]>([])

  useEffect(() => {
    const refresh = () => setFailures(bannerFailures())
    refresh()
    return onApiFailures(refresh)
  }, [])

  if (!failures.length) return null
  const newest = failures[0]
  return (
    <div
      role="alert"
      className="mono text-[11px] px-4 py-2 border-b"
      style={{
        background: 'var(--panel)',
        borderColor: 'var(--alert)',
        color: 'var(--alert)',
      }}
    >
      The API did not answer {newest.path.split('?')[0]}
      {newest.status !== null ? ` (${newest.status})` : ''} — {newest.detail}.
      {failures.length > 1 && ` ${failures.length - 1} more endpoint${
        failures.length > 2 ? 's are' : ' is'
      } failing.`}{' '}
      Empty panels below may be this outage, not an empty archive.
    </div>
  )
}
