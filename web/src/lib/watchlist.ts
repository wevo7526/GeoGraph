// The Watchlist: the relationships this user follows. v1 persists locally —
// there are no accounts yet — but every component talks to this interface
// (list/has/add/remove/toggle/subscribe), so a future backend user-store is a
// drop-in that never touches a page.

import { useSyncExternalStore } from 'react'

export interface WatchedRelationship {
  dyadId: string
  name: string
  region: string
  addedAt: number
}

const KEY = 'geograph.watchlist'
const listeners = new Set<() => void>()

function read(): WatchedRelationship[] {
  try {
    const raw = localStorage.getItem(KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) ? (parsed as WatchedRelationship[]) : []
  } catch {
    return []
  }
}

function write(items: WatchedRelationship[]): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(items))
  } catch {
    // private mode / quota — the list simply won't persist this session
  }
  listeners.forEach((l) => l())
}

export function list(): WatchedRelationship[] {
  return read().sort((a, b) => b.addedAt - a.addedAt)
}

export function has(dyadId: string): boolean {
  return read().some((w) => w.dyadId === dyadId)
}

export function add(item: WatchedRelationship): void {
  const items = read()
  if (items.some((w) => w.dyadId === item.dyadId)) return
  write([...items, item])
}

export function remove(dyadId: string): void {
  write(read().filter((w) => w.dyadId !== dyadId))
}

export function toggle(item: WatchedRelationship): void {
  if (has(item.dyadId)) remove(item.dyadId)
  else add(item)
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  // Cross-tab: another tab's write fires a storage event here.
  const onStorage = (e: StorageEvent) => {
    if (e.key === KEY) listener()
  }
  window.addEventListener('storage', onStorage)
  return () => {
    listeners.delete(listener)
    window.removeEventListener('storage', onStorage)
  }
}

// Snapshot must be referentially stable between changes, or useSyncExternalStore
// loops. Cache the last serialization and only rebuild the array when it moves.
let cachedRaw = ''
let cachedList: WatchedRelationship[] = []
function snapshot(): WatchedRelationship[] {
  const raw = (() => {
    try {
      return localStorage.getItem(KEY) ?? ''
    } catch {
      return ''
    }
  })()
  if (raw !== cachedRaw) {
    cachedRaw = raw
    cachedList = list()
  }
  return cachedList
}

/** Live view of the watchlist; re-renders the component on any change. */
export function useWatchlist(): WatchedRelationship[] {
  return useSyncExternalStore(subscribe, snapshot, snapshot)
}

/** Live boolean for a single relationship — for the star toggle. */
export function useIsWatched(dyadId: string): boolean {
  const items = useWatchlist()
  return items.some((w) => w.dyadId === dyadId)
}
