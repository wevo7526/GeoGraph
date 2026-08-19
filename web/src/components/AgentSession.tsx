/** Shared conversation for the corner desk.
 *
 *  One briefing, one argument. Region change clears the thread. The desk
 *  waits for the reader to ask — nothing opens a reading on arrival. */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import { getHealth, postAssess } from '../api'
import { DEFAULT_QUESTION, focusFromRoute, surfaceFromRoute, type DeskTurn } from '../lib/desk'
import type { SituationBriefing } from '../types'

type AgentApi = {
  messages: DeskTurn[]
  asking: boolean
  error: string | null
  darkReason: string | null
  method: string | null
  briefing: SituationBriefing | null
  deskOpen: boolean
  setDeskOpen: (open: boolean) => void
  ask: (question: string) => void
  brief: () => void
  reread: () => void
  summonDesk: () => void
}

const AgentContext = createContext<AgentApi | null>(null)

export function AgentProvider({
  region,
  route,
  children,
}: {
  region: string
  route: string
  children: ReactNode
}) {
  const [messages, setMessages] = useState<DeskTurn[]>([])
  const [asking, setAsking] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [darkReason, setDarkReason] = useState<string | null>(null)
  const [method, setMethod] = useState<string | null>(null)
  const [briefing, setBriefing] = useState<SituationBriefing | null>(null)
  const [deskOpen, setDeskOpen] = useState(false)
  const gen = useRef(0)
  const askingRef = useRef(false)
  const messagesRef = useRef<DeskTurn[]>([])
  messagesRef.current = messages

  useEffect(() => {
    let live = true
    getHealth().then((h) => {
      if (live) setDarkReason(h?.disabled?.reasoning ?? null)
    })
    return () => {
      live = false
    }
  }, [])

  useEffect(() => {
    gen.current += 1
    setMessages([])
    setError(null)
    setAsking(false)
    askingRef.current = false
    setMethod(null)
    setBriefing(null)
  }, [region])

  const ask = useCallback(
    (question: string) => {
      const asked = question.trim() || DEFAULT_QUESTION
      const prior = messagesRef.current.map((turn) => ({
        role: turn.role,
        content: turn.content,
      }))
      const token = ++gen.current
      askingRef.current = true
      setAsking(true)
      setError(null)
      setMessages((prev) => [...prev, { role: 'user', content: asked }])
      postAssess(asked, region, {
        history: prior,
        surface: surfaceFromRoute(route),
        focus: focusFromRoute(route),
      }).then((response) => {
        if (token !== gen.current) return
        askingRef.current = false
        setAsking(false)
        if (!response.ok || !response.result) {
          setError(response.detail ?? 'the desk did not answer')
          return
        }
        setMethod(response.result.method)
        setBriefing(response.result.context ?? null)
        setMessages((prev) => [
          ...prev,
          { role: 'assistant', content: response.result!.assessment },
        ])
      })
    },
    [region, route],
  )

  const brief = useCallback(() => {
    if (darkReason || asking || messagesRef.current.length > 0) return
    ask(DEFAULT_QUESTION)
  }, [ask, asking, darkReason])

  const reset = useCallback(() => {
    gen.current += 1
    messagesRef.current = []
    askingRef.current = false
    setMessages([])
    setError(null)
    setAsking(false)
    setMethod(null)
    setBriefing(null)
  }, [])

  const reread = useCallback(() => {
    reset()
    ask(DEFAULT_QUESTION)
  }, [ask, reset])

  const summonDesk = useCallback(() => {
    setDeskOpen(true)
    if (darkReason) return
    if (askingRef.current) return
    reread()
  }, [darkReason, reread])

  const value = useMemo(
    () => ({
      messages,
      asking,
      error,
      darkReason,
      method,
      briefing,
      deskOpen,
      setDeskOpen,
      ask,
      brief,
      reread,
      summonDesk,
    }),
    [
      messages,
      asking,
      error,
      darkReason,
      method,
      briefing,
      deskOpen,
      ask,
      brief,
      reread,
      summonDesk,
    ],
  )

  return <AgentContext.Provider value={value}>{children}</AgentContext.Provider>
}

export function useAgent(): AgentApi {
  const value = useContext(AgentContext)
  if (!value) {
    throw new Error('useAgent must sit inside AgentProvider')
  }
  return value
}
