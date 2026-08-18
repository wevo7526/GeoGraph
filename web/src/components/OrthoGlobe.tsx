/** The globe — a 2D canvas, deliberately.
 *
 *  NOT three.js, and that is measured rather than preferred. `Landing` is a
 *  STATIC import in App.tsx while every other view is lazy, precisely so the
 *  front door does not pay for the Explorer's ~1.5 MB WebGL bundle; a hero
 *  that imported `three` would silently undo that split. And WebGL clamps
 *  line width to 1px on most drivers, so a graticule in three.js would look
 *  WORSE than a canvas hairline. The arithmetic lives in lib/project.ts and is
 *  testable without a DOM.
 *
 *  WHAT EACH LAYER IS ALLOWED TO ASSERT:
 *    the land      — geography. Natural Earth 110m, public domain, committed
 *                    rather than fetched. A DRAWING at the same standing as
 *                    the actor centroids: it orients, it does not measure
 *    the nodes     — WHO EXISTS. Ink, uniform: size would imply a weight the
 *                    globe is not measuring
 *    the arcs      — WHAT IS DECLARED. One grey, NOT hued by relation type,
 *                    because painting alliance blue and rivalry oxblood would
 *                    spend the diverging pair on identity
 *    the pulses    — WHAT JUST HAPPENED. ALTITUDE carries magnitude so HUE
 *                    stays free to carry sign: --alert escalating, --accent
 *                    de-escalating, as everywhere else on this surface
 *
 *  THE MOTION IS THE READER'S (rewritten 2026-08-17). The first version eased
 *  the globe toward each firing pulse's longitude, which hopped between
 *  unrelated pairs every 1.4 seconds and read as jumpy and arbitrary — because
 *  it was: the camera was driven by data the reader had not asked to look at.
 *  Now there is one slow constant drift, and DRAG is the real control: grab it
 *  and it tracks the pointer exactly, let go and it carries its momentum and
 *  settles back into the drift. Nothing chases anything.
 *
 *  Guarded four ways (hover, focus-within, hidden tab, scrolled out of view)
 *  plus prefers-reduced-motion, under which the loop never starts and the same
 *  renderer draws one frame. The page is complete without motion.
 */
import { useEffect, useMemo, useRef, useState } from 'react'

import { LAND } from '../lib/land'
import {
  arcPoints,
  graticule,
  projectClamped,
  projectPoint,
  ringIsVisible,
  visibleRuns,
} from '../lib/project'
import type { GlobeBoard, GlobeNode } from '../types'

/** The globe's inks. Local constants because a 2D canvas cannot read CSS
 *  custom properties — but they are the STYLESHEET's values, so the map and
 *  the page around it stay one surface. */
const OCEAN = '#ffffff'
const LAND_FILL = '#ebe9e4'
const COAST = '#b9b5ac'
const LIMB = '#000000'
const GRID = '#e2e0da'
const NODE = '#111111'
const ARC = 'rgba(17, 17, 17, 0.28)'
const ESCALATING = '#9e3418' // --alert
const DEESCALATING = '#2a5fa8' // --accent

const BACK_ALPHA = 0.18
/** Degrees per second of idle drift — one revolution in about two minutes.
 *  Slow enough to read as a globe turning rather than a thing spinning. */
const DRIFT = 3
/** How fast released momentum bleeds off, per 1/60 s. */
const FRICTION = 0.94
/** Pointer pixels → degrees. */
const DRAG_LAM = 0.32
const DRAG_PHI = 0.26
/** Hover radius for a node, in CSS pixels. */
const HIT = 12

const GROW = 900
const HOLD = 1600
const FADE = 2600
const LIFE = GROW + HOLD + FADE
const GAP = 1500

export default function OrthoGlobe({
  board,
  onNodeClick,
}: {
  board: GlobeBoard
  onNodeClick?: (node: GlobeNode) => void
}) {
  const canvas = useRef<HTMLCanvasElement>(null)
  const wrap = useRef<HTMLDivElement>(null)
  const grid = useMemo(() => graticule(15, 15), [])

  // The tooltip is React; the globe is not. It is set only when the node under
  // the pointer CHANGES, so a sixty-per-second loop never re-renders the page
  // — which is the whole reason the loop owns pixels directly.
  const [hover, setHover] = useState<
    { node: GlobeNode; sx: number; sy: number } | null
  >(null)
  const hoverId = useRef<string | null>(null)
  const onNodeClickRef = useRef(onNodeClick)
  onNodeClickRef.current = onNodeClick

  const state = useRef({
    lam0: 20,
    phi0: 18,
    /** Degrees per second. Idles at DRIFT; a throw sets it higher and friction
     *  brings it back — which is what makes a release feel like a globe. */
    vLam: DRIFT,
    vPhi: 0,
    dragging: false,
    moved: false,
    lastX: 0,
    lastY: 0,
    paused: { hover: false, focus: false, hidden: false, offscreen: false },
    /** Where each node landed on the last frame. The hit test reads this
     *  rather than re-projecting: hover costs a loop over 56 points, not a
     *  second projection per pointer event. */
    screen: new Map<string, { sx: number; sy: number; front: boolean }>(),
  })

  useEffect(() => {
    const element = canvas.current
    const box = wrap.current
    if (!element || !box) return

    const byId = new Map(board.nodes.map((n) => [n.id, n]))
    const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
    const s = state.current

    const draw = (elapsed: number) => {
      const dpr = Math.min(2, window.devicePixelRatio || 1)
      const width = box.clientWidth
      const height = box.clientHeight
      if (!width || !height) return
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
      const r = Math.min(width, height) * 0.46
      const { lam0, phi0 } = s

      // 1 — the ocean: the page's own white, so the globe sits ON the surface
      ctx.beginPath()
      ctx.arc(cx, cy, r, 0, Math.PI * 2)
      ctx.fillStyle = OCEAN
      ctx.fill()

      ctx.save()
      ctx.clip()

      // 2 — the land. Hidden vertices are clamped onto the limb rather than
      // dropped: dropping them closes the polygon with a chord straight across
      // the globe, which is the classic orthographic artefact.
      ctx.fillStyle = LAND_FILL
      ctx.strokeStyle = COAST
      ctx.lineWidth = 0.6
      for (const ring of LAND) {
        if (!ringIsVisible(ring, lam0, phi0)) continue
        ctx.beginPath()
        for (let i = 0; i < ring.length; i += 2) {
          const p = projectClamped(ring[i + 1], ring[i], lam0, phi0, cx, cy, r)
          if (i === 0) ctx.moveTo(p.sx, p.sy)
          else ctx.lineTo(p.sx, p.sy)
        }
        ctx.closePath()
        ctx.fill()
        ctx.stroke()
      }

      // 3 — the graticule, over the land: a projection ruled onto the map
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
      ctx.restore()

      // 4 — the limb, last, so the instrument's edge stays crisp ink
      ctx.beginPath()
      ctx.arc(cx, cy, r, 0, Math.PI * 2)
      ctx.strokeStyle = LIMB
      ctx.lineWidth = 1
      ctx.stroke()

      // 5 — declared standings
      ctx.lineWidth = 1
      ctx.strokeStyle = ARC
      for (const link of board.links) {
        const a = byId.get(link.source)
        const b = byId.get(link.target)
        if (!a || !b) continue
        for (const run of visibleRuns(arcPoints(a, b, lam0, phi0, cx, cy, r, { samples: 24 }))) {
          ctx.globalAlpha = run[0].front ? 0.6 : BACK_ALPHA
          ctx.beginPath()
          run.forEach((p, i) => (i ? ctx.lineTo(p.sx, p.sy) : ctx.moveTo(p.sx, p.sy)))
          ctx.stroke()
        }
      }
      ctx.globalAlpha = 1

      // 6 — the pulses, each in turn. They no longer steer the camera; they
      // happen where they happen, and the reader may turn the globe to look.
      for (let i = 0; i < board.pulses.length; i += 1) {
        const pulse = board.pulses[i]
        const a = byId.get(pulse.source)
        const b = byId.get(pulse.target)
        if (!a || !b) continue
        const age = reduced ? HOLD : elapsed - i * GAP
        if (age < 0 || age > LIFE) continue
        const grow = Math.min(1, age / GROW)
        const alpha = age < GROW + HOLD ? 1 : Math.max(0, 1 - (age - GROW - HOLD) / FADE)
        const altitude = 0.16 + 0.1 * Math.min(1, pulse.points_from_baseline / 8)
        const points = arcPoints(a, b, lam0, phi0, cx, cy, r, { samples: 40, altitude })
          .slice(0, Math.max(2, Math.round(41 * grow)))
        ctx.strokeStyle = pulse.direction === 'deescalating' ? DEESCALATING : ESCALATING
        ctx.lineWidth = 1.6
        for (const run of visibleRuns(points)) {
          ctx.globalAlpha = (run[0].front ? 0.95 : BACK_ALPHA) * alpha
          ctx.beginPath()
          run.forEach((p, j) => (j ? ctx.lineTo(p.sx, p.sy) : ctx.moveTo(p.sx, p.sy)))
          ctx.stroke()
        }
      }
      ctx.globalAlpha = 1

      // 7 — the roster. Uniform size: a bigger dot would claim a weight the
      // globe is not measuring.
      s.screen.clear()
      for (const node of board.nodes) {
        const p = projectPoint(node.lat, node.lng, lam0, phi0, cx, cy, r)
        s.screen.set(node.id, { sx: p.sx, sy: p.sy, front: p.front })
        const marked = hoverId.current === node.id
        ctx.globalAlpha = p.front ? 1 : BACK_ALPHA
        ctx.beginPath()
        ctx.arc(p.sx, p.sy, marked ? 4.5 : 2.5, 0, Math.PI * 2)
        ctx.fillStyle = NODE
        ctx.fill()
        if (marked && p.front) {
          ctx.beginPath()
          ctx.arc(p.sx, p.sy, 8, 0, Math.PI * 2)
          ctx.strokeStyle = NODE
          ctx.lineWidth = 1
          ctx.stroke()
        }
      }
      ctx.globalAlpha = 1
    }

    /** Nearest front-facing node within HIT pixels, or nothing. */
    const pick = (x: number, y: number) => {
      let best: { id: string; d: number } | null = null
      for (const [id, p] of s.screen) {
        if (!p.front) continue
        const d = Math.hypot(p.sx - x, p.sy - y)
        if (d <= HIT && (!best || d < best.d)) best = { id, d }
      }
      const id = best?.id ?? null
      if (id === hoverId.current) return
      hoverId.current = id
      const node = id ? byId.get(id) : null
      const at = id ? s.screen.get(id) : null
      setHover(node && at ? { node, sx: at.sx, sy: at.sy } : null)
      box.style.cursor = id && onNodeClickRef.current ? 'pointer' : ''
    }

    let raf = 0
    let last = performance.now()
    const started = last
    const tick = (now: number) => {
      const dt = Math.min(0.05, (now - last) / 1000)
      last = now
      const p = s.paused
      if (!p.hidden && !p.offscreen) {
        if (!s.dragging) {
          // Momentum bleeds toward the idle DRIFT rather than to zero, so a
          // throw settles into the globe's own turn instead of stopping dead.
          const decay = FRICTION ** (dt * 60)
          s.vLam = DRIFT + (s.vLam - DRIFT) * decay
          s.vPhi *= decay
          // Hover and focus hold it still: an instrument the reader is
          // pointing at should not walk out from under the pointer.
          if (!p.hover && !p.focus) {
            s.lam0 += s.vLam * dt
            s.phi0 = Math.max(-75, Math.min(75, s.phi0 + s.vPhi * dt))
          }
        }
        if (s.lam0 > 180) s.lam0 -= 360
        if (s.lam0 < -180) s.lam0 += 360
        draw((now - started) % (Math.max(1, board.pulses.length) * GAP + 5000))
      }
      raf = requestAnimationFrame(tick)
    }
    if (reduced) draw(0)
    else raf = requestAnimationFrame(tick)

    // ── drag to turn, hover to name ──────────────────────────────────────
    const down = (e: PointerEvent) => {
      s.dragging = true
      s.moved = false
      s.lastX = e.clientX
      s.lastY = e.clientY
      s.vLam = 0
      s.vPhi = 0
      box.setPointerCapture?.(e.pointerId)
      const rect = box.getBoundingClientRect()
      pick(e.clientX - rect.left, e.clientY - rect.top)
    }
    const move = (e: PointerEvent) => {
      if (s.dragging) {
        const dx = e.clientX - s.lastX
        const dy = e.clientY - s.lastY
        if (Math.hypot(dx, dy) > 4) s.moved = true
        s.lastX = e.clientX
        s.lastY = e.clientY
        // BOTH AXES ARE NEGATED, and that is the fix rather than a taste.
        // Worked from the projection: `toVector` puts a point at
        // sin(lng - lam0), so RAISING lam0 sweeps points LEFT — a globe dragged
        // right must therefore have lam0 DECREASE to follow the pointer. The
        // tilt is the same story through the cos(phi0)/sin(phi0) rotation:
        // raising phi0 moves the centre point DOWN the screen. Shipped with
        // both signs the wrong way round, which is precisely why it felt
        // inverted and unusable.
        //
        // Direct, not eased: a dragged globe must track the pointer exactly.
        // The velocity is recorded in the same sense, so a release carries on
        // the way the hand was going.
        s.lam0 -= dx * DRAG_LAM
        s.phi0 = Math.max(-75, Math.min(75, s.phi0 + dy * DRAG_PHI))
        s.vLam = -dx * DRAG_LAM * 60
        s.vPhi = dy * DRAG_PHI * 60
        return
      }
      const rect = box.getBoundingClientRect()
      pick(e.clientX - rect.left, e.clientY - rect.top)
    }
    const up = (e: PointerEvent) => {
      if (!s.dragging) return
      s.dragging = false
      box.releasePointerCapture?.(e.pointerId)
      if (!s.moved && hoverId.current && onNodeClickRef.current) {
        const node = byId.get(hoverId.current)
        if (node) onNodeClickRef.current(node)
        s.vLam = DRIFT
        s.vPhi = 0
        return
      }
      // Cap the throw so a fast flick does not spin the globe into a blur.
      s.vLam = Math.max(-200, Math.min(200, s.vLam))
      s.vPhi = Math.max(-110, Math.min(110, s.vPhi))
    }

    const enter = () => { s.paused.hover = true }
    const leave = () => {
      s.paused.hover = false
      if (hoverId.current !== null) {
        hoverId.current = null
        setHover(null)
      }
    }
    const focusIn = () => { s.paused.focus = true }
    const focusOut = () => { s.paused.focus = false }
    const visibility = () => { s.paused.hidden = document.hidden }

    box.addEventListener('pointerdown', down)
    box.addEventListener('pointermove', move)
    box.addEventListener('pointerup', up)
    box.addEventListener('pointercancel', up)
    box.addEventListener('pointerenter', enter)
    box.addEventListener('pointerleave', leave)
    box.addEventListener('focusin', focusIn)
    box.addEventListener('focusout', focusOut)
    document.addEventListener('visibilitychange', visibility)
    const observer = new IntersectionObserver(
      ([entry]) => { s.paused.offscreen = !entry.isIntersecting },
      { threshold: 0.05 },
    )
    observer.observe(box)

    return () => {
      cancelAnimationFrame(raf)
      box.removeEventListener('pointerdown', down)
      box.removeEventListener('pointermove', move)
      box.removeEventListener('pointerup', up)
      box.removeEventListener('pointercancel', up)
      box.removeEventListener('pointerenter', enter)
      box.removeEventListener('pointerleave', leave)
      box.removeEventListener('focusin', focusIn)
      box.removeEventListener('focusout', focusOut)
      document.removeEventListener('visibilitychange', visibility)
      observer.disconnect()
    }
  }, [board, grid])

  return (
    <div className="globe-box" ref={wrap}>
      {/* aria-hidden: the canvas is a picture. The tooltip carries the name in
          text, which is what a screen reader should meet. */}
      <canvas ref={canvas} aria-hidden="true" />
      {hover && (
        <div
          className="globe-tip"
          role="status"
          style={{ left: `${hover.sx}px`, top: `${hover.sy}px` }}
        >
          <span className="globe-tip-name">{hover.node.name}</span>
          {hover.node.regions?.length > 0 && (
            <span className="globe-tip-line">{hover.node.regions.join(' · ')}</span>
          )}
          {hover.node.standings?.length > 0 && (
            <ul className="globe-tip-standings">
              {hover.node.standings.map((st) => (
                <li key={`${st.relation_type}-${st.with}`}>
                  {st.relation_type} with {st.with}
                  {st.since ? ` since ${st.since}` : ''}
                </li>
              ))}
            </ul>
          )}
          <span className="globe-tip-line">
            {hover.node.departures > 0
              ? `${hover.node.departures} recent departure${hover.node.departures === 1 ? '' : 's'} from a usual band`
              : 'nothing lately outside a usual band'}
          </span>
        </div>
      )}
    </div>
  )
}
