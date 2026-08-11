import type { Health, Pack, Segmentation } from './types'

// Null on any failure, deliberately: the explorer renders its built-in sample
// when the API is absent (vite dev with no backend), and every caller shows
// what is live versus placeholder rather than crashing.
async function get<T>(path: string): Promise<T | null> {
  try {
    const res = await fetch(path)
    if (!res.ok) return null
    return (await res.json()) as T
  } catch {
    return null
  }
}

export const getHealth = () => get<Health>('/api/health')
export const getRegimes = () => get<Segmentation>('/api/regimes')
export const getPack = (name: string) => get<Pack>(`/api/packs/${name}`)
