/** A Source.url that is actually a document on the web.
 *
 *  Pack sources sometimes store a local path (`packs/mena/marquee_events.yaml`).
 *  GDELT SOURCEURL is a mention string, not a verified article — a well-formed
 *  sports URL is still the wrong document. The live feed must never pass that
 *  field here and treat a parse as a citation. */

export function citableUrl(url: string | null | undefined): string | null {
  if (!url) return null
  const text = url.trim()
  if (!text || text.length > 2048) return null
  try {
    const parsed = new URL(text)
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return null
    const host = parsed.hostname.replace(/^\.+|\.+$/g, '')
    if (!host || !host.includes('.')) return null
    return text
  } catch {
    return null
  }
}
