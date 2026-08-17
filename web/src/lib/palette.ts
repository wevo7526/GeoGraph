/** The categorical palette — colours only, no renderer.
 *
 *  EXTRACTED FROM Graph3D.tsx (2026-08-17) BEFORE ANY SECOND CONSUMER EXISTS,
 *  and the reason is a bundle boundary rather than tidiness. `Graph3D.tsx`
 *  imports `three` on its fifth line, and `Landing` is a STATIC import in
 *  App.tsx while every other view is lazy — so a front-door component reaching
 *  into Graph3D for one hue would drag ~1.5 MB of WebGL into the first paint
 *  and silently undo the code split. A module with zero imports cannot.
 *
 *  These are the validated categorical steps from styles.css, NOT the UI inks:
 *  --text/--muted fail as series colours by design. They are duplicated out of
 *  CSS deliberately — WebGL cannot read custom properties, and a legend that
 *  disagreed with its canvas would mislead in silence (the MarketGraph lesson).
 */
import type { PackActor } from '../types'

/** Actor-type encoding, shared with the legend overlay in Explorer.tsx.
 * Duplicated deliberately — WebGL cannot read CSS custom properties, and a
 * mismatch between legend and canvas would silently mislead (the MarketGraph
 * lesson). These are the validated categorical steps from styles.css, NOT the
 * UI inks: --text/--muted fail as series colors by design. */
export const ACTOR_COLOR: Record<PackActor['actor_type'], string> = {
  state: '#c48a12',
  person: '#b04a5c',
  org: '#4a82d4',
  swf: '#2f9960',
}

export const TYPE_LABEL: Record<PackActor['actor_type'], string> = {
  state: 'state',
  org: 'organisation',
  person: 'person',
  swf: 'sovereign fund',
}

/** One hue per DURABLE relation type — the standing structure drawn under the
 * event flow. Proxy is the brand's gold: patronage is the structure this
 * region's story runs on, and the directed particles show which way the hand
 * points. Swatches for the legend are these hues at full opacity. */
export const RELATION_COLOR: Record<string, string> = {
  proxy: 'rgba(176, 141, 87, 0.55)',
  alliance: 'rgba(74, 130, 212, 0.45)',
  membership: 'rgba(125, 133, 152, 0.35)',
  trade: 'rgba(47, 153, 96, 0.40)',
  rivalry: 'rgba(164, 74, 63, 0.45)',
}
export const RELATION_SWATCH: Record<string, string> = {
  proxy: '#b08d57',
  alliance: '#4a82d4',
  membership: '#7d8598',
  trade: '#2f9960',
  rivalry: '#a44a3f',
}

/** The capital layer: SWF → market deployment from 13F. Fund-green edges,
 * cube nodes — a market is not an actor and does not pretend to be one. */
export const FLOW_EDGE = 'rgba(47, 153, 96, 0.65)'
export const FLOW_SWATCH = '#2f9960'
export const MARKET_NODE = '#8a93a6'

/** Event-flow edges (dyads active in the slider window). */
export const EDGE_ESCALATING = 'rgba(164, 74, 63, 0.85)'
export const EDGE_ACTIVE = 'rgba(90, 98, 115, 0.70)'
export const EDGE_DORMANT = 'rgba(35, 42, 58, 0.18)'
export const EDGE_SWATCH_ESCALATING = '#a44a3f'
export const EDGE_SWATCH_ACTIVE = '#5a6273'

/** What everything unrelated to the selection fades to: just below the white
 * ground, so the shape of the region stays visible as context. The plate is
 * WHITE (styles.css, 2026-08-15) — these constants were inverted with it. */
export const DIM_NODE = '#dcdcdc'
export const DIM_LINK = 'rgba(35, 42, 58, 0.10)'
export const INK = '#ffffff'
export const TEXT = '#111111'
