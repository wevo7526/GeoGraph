import { useEffect, useState } from 'react'
import { getPacks } from './api'

/** Pack KEY vs region LABEL, kept apart on purpose.
 *
 *  The key (`mena`, `china`, `eurasia`) is what every `region=` parameter
 *  takes and what every record carries in `region_pack`; it is written into
 *  the deployed volume, so it is not free to change. The label is what a
 *  reader is shown, declared by the pack itself (`region_label` in its
 *  actors.yaml) and free to change — `china` reads as ASIA because the lens
 *  covers Taiwan, Japan and Korea too.
 *
 *  Fetched ONCE per page load and shared: the label map is the same for every
 *  component that asks, and four components each firing their own request for
 *  a three-entry dictionary is just noise in the network tab. */
let pending: Promise<Record<string, string>> | null = null

function labelMap(): Promise<Record<string, string>> {
  if (!pending) {
    pending = getPacks().then((r) => {
      // A failed fetch resolves null — do NOT cache it, or every header shows
      // raw pack keys for the rest of the session while the underlying packs
      // memo (which does retry) recovers without us.
      if (r === null) pending = null
      return r?.labels ?? {}
    })
  }
  return pending
}

/** The display name for a pack key. Falls back to the key itself — before the
 *  fetch lands and if the API is down, so a header reads CHINA rather than
 *  going blank. */
export function useRegionLabel(region: string): string {
  const [labels, setLabels] = useState<Record<string, string>>({})
  useEffect(() => {
    let live = true
    labelMap().then((m) => live && setLabels(m))
    return () => {
      live = false
    }
  }, [])
  return labels[region] ?? region
}
