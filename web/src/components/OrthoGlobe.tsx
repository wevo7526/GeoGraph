/** The rotating network globe — a 2D canvas, deliberately.
 *
 *  NOT three.js, and that is measured rather than preferred. `Landing` is a
 *  STATIC import in App.tsx while every other view is lazy, precisely so the
 *  front door does not pay for the Explorer's ~1.5 MB WebGL bundle; a hero
 *  that imported `three` would silently undo that split. And WebGL clamps
 *  line width to 1px on most drivers, so a graticule in three.js would look
 *  WORSE than a canvas hairline. All the arithmetic lives in lib/project.ts
 *  and is unit-testable without a DOM.
 *
 *  WHAT THE DRAWING ASSERTS, layer by layer:
 *    the sphere    — nothing; it is ground, and carries no landmasses because
 *                    the archive has no landmass data and drawing one would be
 *                    the only unmeasured thing on the page
 *    the nodes     — WHO EXISTS. Bone, uniform: size would imply a weight the
 *                    globe is not measuring
 *    the arcs      — WHAT IS DECLARED. One grey, NOT hued by relation type,
 *                    because painting alliance blue and rivalry oxblood would
 *                    spend the diverging pair on identity
 *    the pulses    — WHAT JUST HAPPENED, and the only thing that moves.
 *                    ALTITUDE carries magnitude so HUE stays free to carry
 *                    sign: --alert escalating, --accent de-escalating, exactly
 *                    as everywhere else on this surface
 *
 *  MOTION IS GUARDED FOUR WAYS (hover, focus-within, tab-hidden, scrolled out
 *  of view) plus prefers-reduced-motion, and the page is COMPLETE without it:
 *  the reduced-motion branch is the same renderer called once. If the static
 *  frame does not stand up, motion would not have saved it.
 */
import { useEffect, useMemo, useRef } from 'react'

import {
  arcPoints,
  graticule,
  midLongitude,
  projectPoint,
  shortestTurn,
  visibleRuns,
  type Projected,
} from '../lib/project'
import type { GlobeBoard, GlobePulse } from '../types'

/** The plate's own inks. Local because a canvas cannot read CSS custom
 *  properties, and a plate that disagreed with its stylesheet would drift. */
const SPHERE = '#171a1e'
const LIMB = '#4a5058'
const GRID = '#2f3238'
const NODE = '#e8e4dc'
const ARC = '#6b7480'
const ESCALATING = '#d9573a'   // --alert, lifted for the dark plate
const DEESCALATING = '#5b9bd8' // --accent, lifted for the dark plate

const BACK_ALPHA = 0.22
const SECONDS_PER_TURN = 52

/** How long each pulse lives, in ms, and how far apart they fire. */
const GROW = 900
const HOLD = 1200
const FADE = 2500
const LIFE = GROW + HOLD + FADE
const GAP = 1400

interface Props {
  board: GlobeBoard
  /** Node id to mark — the rail hovers a row, the globe answers. */
  highlightId?: string | null
  onPulse?: (pulse: GlobePulse | null) => void
}

export default function OrthoGlobe({ board, highlightId, onPulse }: Props) {
  const canvas = useRef<HTMLCanvasElement>(null)
  const wrap = useRef<HTMLDivElement>(null)
  const grid = useMemo(() => graticule(15, 15), [])

  // Every frame reads these; none of them is React state, because a hero that
  // re-rendered sixty times a second would re-render the whole front door with
  // it. React owns `board` and `highlightId`; the loop owns pixels.
  const state = useRef({
    lam0: 0,
    target: null as number | null,
    paused: { hover: false, focus: false, hidden: false, offscreen: false },
    started: 0,
    highlight: null as string | null,
  })
  state.current.highlight = highlightId ?? null

  useEffect(() => {
    const element = canvas.current
    const box = wrap.current
    if (!element || !box) return

    const byId = new Map(board.nodes.map((n) => [n.id, n]))
    const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
    const s = state.current

    // Orient the opening frame at the newest pulse, so the first thing on
    // screen is the thing the rail leads with rather than the Atlantic.
    const first = board.pulses[0]
    if (first) {
      const a = byId.get(first.source)
      const b = byId.get(first.target)
      if (a && b) s.lam0 = midLongitude(a.lng, b.lng)
    }

    let raf = 0
    const draw = (elapsed: number) => {
      const dpr = Math.min(2, window.devicePixelRatio || 1)
      const width = box.clientWidth
      const height = box.clientHeight
      if (element.width !== width * dpr || element.height !== height * dpr) {
        element.width = width * dpr
        element.height = height * dpr
      }
      const ctx = element.getContext('2d')
      if (!ctx) return
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      ctx.clearRect(0, 0, width, height)

      const cx = width / 2
      const cy = height / 2
      const r = Math.min(width, height) * 0.42
      const lam0 = s.lam0
      const phi0 = 18

      // 1 — the body
      ctx.beginPath()
      ctx.arc(cx, cy, r, 0, Math.PI * 2)
      ctx.fillStyle = SPHERE
      ctx.fill()

      // 2 — the graticule, front hemisphere only: a grid drawn through the
      // body makes the sphere read as a wire ball rather than a solid.
      ctx.lineWidth = 0.5
      ctx.strokeStyle = GRID
      for (const line of grid) {
        const points = line.map((p) => projectPoint(p.lat, p.lng, lam0, phi0, cx, cy, r))
        for (const run of visibleRuns(points)) {
          if (!run[0].front) continue
          ctx.beginPath()
          run.forEach((p, i) => (i ? ctx.lineTo(p.sx, p.sy) : ctx.moveTo(p.sx, p.sy)))
          ctx.stroke()
        }
      }

      // 3 — the limb, over the grid so the edge stays crisp
      ctx.beginPath()
      ctx.arc(cx, cy, r, 0, Math.PI * 2)
      ctx.strokeStyle = LIMB
      ctx.lineWidth = 1
      ctx.stroke()

      // 4 — declared standings
      ctx.lineWidth = 1
      for (const link of board.links) {
        const a = byId.get(link.source)
        const b = byId.get(link.target)
        if (!a || !b) continue
        const points = arcPoints(a, b, lam0, phi0, cx, cy, r, { samples: 24 })
        for (const run of visibleRuns(points)) {
          ctx.globalAlpha = run[0].front ? 0.55 : BACK_ALPHA * 0.6
          ctx.strokeStyle = ARC
          ctx.beginPath()
          run.forEach((p, i) => (i ? ctx.lineTo(p.sx, p.sy) : ctx.moveTo(p.sx, p.sy)))
          ctx.stroke()
        }
      }
      ctx.globalAlpha = 1

      // 5 — the pulses. Each fires in turn and lives LIFE ms; altitude carries
      // how far the event sat from that pair's own baseline.
      if (board.pulses.length) {
        for (let i = 0; i < board.pulses.length; i += 1) {
          const pulse = board.pulses[i]
          const a = byId.get(pulse.source)
          const b = byId.get(pulse.target)
          if (!a || !b) continue
          const age = reduced ? HOLD : elapsed - i * GAP
          if (age < 0 || age > LIFE) continue
          const grow = Math.min(1, age / GROW)
          const alpha =
            age < GROW + HOLD ? 1 : Math.max(0, 1 - (age - GROW - HOLD) / FADE)
          const altitude = 0.18 + 0.1 * Math.min(1, pulse.points_from_baseline / 8)
          const points = arcPoints(a, b, lam0, phi0, cx, cy, r, {
            samples: 40,
            altitude,
          }).slice(0, Math.max(2, Math.round(41 * grow)))
          ctx.strokeStyle =
            pulse.direction === 'deescalating' ? DEESCALATING : ESCALATING
          ctx.lineWidth = 1.6
          for (const run of visibleRuns(points)) {
            ctx.globalAlpha = (run[0].front ? 0.95 : BACK_ALPHA) * alpha
            ctx.beginPath()
            run.forEach((p, i2) => (i2 ? ctx.lineTo(p.sx, p.sy) : ctx.moveTo(p.sx, p.sy)))
            ctx.stroke()
          }
        }
        ctx.globalAlpha = 1
      }

      // 6 — the roster. Uniform size: a bigger dot would claim a weight the
      // globe is not measuring.
      for (const node of board.nodes) {
        const p: Projected = projectPoint(node.lat, node.lng, lam0, phi0, cx, cy, r)
        const marked = s.highlight === node.id
        ctx.globalAlpha = p.front ? 1 : BACK_ALPHA
        ctx.beginPath()
        ctx.arc(p.sx, p.sy, marked ? 4 : 2.5, 0, Math.PI * 2)
        ctx.fillStyle = NODE
        ctx.fill()
        if (marked && p.front) {
          ctx.beginPath()
          ctx.arc(p.sx, p.sy, 7.5, 0, Math.PI * 2)
          ctx.strokeStyle = NODE
          ctx.lineWidth = 1
          ctx.stroke()
        }
      }
      ctx.globalAlpha = 1
    }

    if (reduced) {
      // THE PAGE IS COMPLETE WITHOUT MOTION. One frame, every pulse at full
      // opacity, oriented at the newest — the same renderer, called once.
      draw(0)
      return
    }

    let last = performance.now()
    s.started = last
    const tick = (now: number) => {
      const dt = now - last
      last = now
      const p = s.paused
      const still = p.hover || p.focus || p.hidden || p.offscreen
      if (!still) {
        // Ease toward a firing pulse's midpoint when there is one, otherwise
        // drift. `shortestTurn` stops a +170 → −170 hand-off spinning the long
        // way round.
        const cycle = (now - s.started) % (board.pulses.length * GAP + 6000)
        const index = Math.floor(cycle / GAP)
        const pulse = board.pulses[index]
        if (pulse) {
          const a = byId.get(pulse.source)
          const b = byId.get(pulse.target)
          if (a && b) s.target = midLongitude(a.lng, b.lng)
        }
        const drift = (360 / SECONDS_PER_TURN) * (dt / 1000)
        if (s.target !== null) {
          const turn = shortestTurn(s.lam0, s.target)
          s.lam0 += Math.abs(turn) < 0.4 ? drift : Math.max(-2.2, Math.min(2.2, turn * 0.06))
        } else {
          s.lam0 += drift
        }
        if (s.lam0 > 180) s.lam0 -= 360
        if (s.lam0 < -180) s.lam0 += 360
        draw((now - s.started) % (board.pulses.length * GAP + 6000))
      }
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)

    // THE FOUR GUARDS. Hover and focus are what make a moving hero read as an
    // instrument rather than a screensaver; hidden and offscreen are what stop
    // it burning a laptop battery in a background tab.
    const enter = () => { s.paused.hover = true }
    const leave = () => { s.paused.hover = false }
    const focusIn = () => { s.paused.focus = true }
    const focusOut = () => { s.paused.focus = false }
    const visibility = () => { s.paused.hidden = document.hidden }
    box.addEventListener('pointerenter', enter)
    box.addEventListener('pointerleave', leave)
    box.addEventListener('focusin', focusIn)
    box.addEventListener('focusout', focusOut)
    document.addEventListener('visibilitychange', visibility)
    const observer = new IntersectionObserver(
      ([entry]) => { s.paused.offscreen = !entry.isIntersecting },
      { threshold: 0.1 },
    )
    observer.observe(box)

    return () => {
      cancelAnimationFrame(raf)
      box.removeEventListener('pointerenter', enter)
      box.removeEventListener('pointerleave', leave)
      box.removeEventListener('focusin', focusIn)
      box.removeEventListener('focusout', focusOut)
      document.removeEventListener('visibilitychange', visibility)
      observer.disconnect()
      onPulse?.(null)
    }
  }, [board, grid, onPulse])

  return (
    <div className="globe-box" ref={wrap}>
      {/* aria-hidden: the canvas is a picture of data the rail states in words
          beside it, so a screen reader that read both would read it twice. */}
      <canvas ref={canvas} aria-hidden="true" />
    </div>
  )
}
