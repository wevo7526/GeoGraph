/** Shared conversation for Intel and the corner desk.
 *
 *  One briefing, one argument. Region change clears the thread. Intel
 *  opens a reading on arrival; other desks wait for the reader to ask. */
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

type AgentApi = {
  messages: DeskTurn[]
  asking: boolean
  error: string | null
  darkReason: string | null
  method: string | null
  ask: (question: string) => void
  brief: () => void
  reread: () => void
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
  const gen = useRef(0)
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
    setMethod(null)
  }, [region])

  const ask = useCallback(
    (question: string) => {
      const asked = question.trim() || DEFAULT_QUESTION
      const prior = messagesRef.current.map((turn) => ({
        role: turn.role,
        content: turn.content,
      }))
      const token = ++gen.current
      setAsking(true)
      setError(null)
      setMessages((prev) => [...prev, { role: 'user', content: asked }])
      postAssess(asked, region, {
        history: prior,
        surface: surfaceFromRoute(route),
        focus: focusFromRoute(route),
      }).then((response) => {
        if (token !== gen.current) return
        setAsking(false)
        if (!response.ok || !response.result) {
          setError(response.detail ?? 'the desk did not answer')
          return
        }
        setMethod(response.result.method)
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
    setMessages([])
    setError(null)
    setAsking(false)
    setMethod(null)
  }, [])

  const reread = useCallback(() => {
    reset()
    ask(DEFAULT_QUESTION)
  }, [ask, reset])

  const value = useMemo(
    () => ({ messages, asking, error, darkReason, method, ask, brief, reread }),
    [messages, asking, error, darkReason, method, ask, brief, reread],
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
