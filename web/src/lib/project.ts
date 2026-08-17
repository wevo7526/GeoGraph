/** Orthographic projection, great circles, and visibility — the globe's
 *  arithmetic, kept out of the component that draws it.
 *
 *  NO three.js, AND THAT IS A DECISION RATHER THAN A SHORTCUT. Two measured
 *  reasons. `Landing` is a STATIC import in App.tsx while every other view is
 *  lazy, precisely so the front door does not pay for the Explorer's ~1.5 MB
 *  WebGL bundle — a hero that imported `three` would silently undo that split.
 *  And WebGL clamps line width to 1px on most drivers, so a graticule drawn in
 *  three.js would look WORSE than a 2D canvas hairline, not better. If a
 *  WebGL globe is ever wanted it belongs in the Explorer's existing canvas,
 *  behind the lazy boundary.
 *
 *  Everything here is a pure function of numbers so it can be tested without a
 *  canvas, a DOM, or a frame.
 */

export interface Vec3 {
  x: number
  y: number
  z: number
}

export interface Projected {
  /** Canvas pixels, already centred and scaled. */
  sx: number
  sy: number
  /** Toward the viewer? The back hemisphere is drawn faint, not culled — a
   *  sphere that hides its far side reads as a disc. */
  front: boolean
}

const RAD = Math.PI / 180

/** Geographic degrees → a unit vector, with the globe's own rotation applied.
 *
 *  `lam0` is the longitude currently facing the viewer; advancing it is what
 *  spins the globe. `phi0` tilts the pole toward or away.
 */
export function toVector(lat: number, lng: number, lam0: number, phi0: number): Vec3 {
  const phi = lat * RAD
  const lam = (lng - lam0) * RAD
  const p0 = phi0 * RAD
  const cosPhi = Math.cos(phi)
  // Standard orthographic: rotate about the polar axis by the current
  // longitude, then about the horizontal axis by the tilt.
  const x = cosPhi * Math.sin(lam)
  const yRaw = Math.sin(phi)
  const zRaw = cosPhi * Math.cos(lam)
  return {
    x,
    y: yRaw * Math.cos(p0) - zRaw * Math.sin(p0),
    z: yRaw * Math.sin(p0) + zRaw * Math.cos(p0),
  }
}

/** A unit vector → canvas pixels about a centre, at a radius. */
export function project(v: Vec3, cx: number, cy: number, r: number): Projected {
  return { sx: cx + v.x * r, sy: cy - v.y * r, front: v.z >= 0 }
}

export function projectPoint(
  lat: number, lng: number, lam0: number, phi0: number,
  cx: number, cy: number, r: number,
): Projected {
  return project(toVector(lat, lng, lam0, phi0), cx, cy, r)
}

/** Spherical linear interpolation between two unit vectors.
 *
 *  This is what makes an arc a GREAT CIRCLE rather than a straight line in
 *  screen space: two points on a sphere are joined by the shortest path over
 *  its surface, and a straight screen line would cut through the body of the
 *  earth and read as wrong at any tilt.
 */
export function slerp(a: Vec3, b: Vec3, t: number): Vec3 {
  const dot = Math.max(-1, Math.min(1, a.x * b.x + a.y * b.y + a.z * b.z))
  const omega = Math.acos(dot)
  if (omega < 1e-6) return a
  const sin = Math.sin(omega)
  const wa = Math.sin((1 - t) * omega) / sin
  const wb = Math.sin(t * omega) / sin
  return { x: a.x * wa + b.x * wb, y: a.y * wa + b.y * wb, z: a.z * wa + b.z * wb }
}

/** Lift a point off the surface — how a pulse arc carries magnitude.
 *
 *  ALTITUDE, NOT HUE. Magnitude rides the arc's height so that colour stays
 *  free to carry SIGN, which is what `--accent`/`--alert` mean everywhere else
 *  on this surface. An arc that encoded size in colour would need a second
 *  scale and would collide with the diverging pair.
 */
export function lift(v: Vec3, altitude: number): Vec3 {
  const k = 1 + altitude
  return { x: v.x * k, y: v.y * k, z: v.z * k }
}

/** Sample a great-circle arc between two geographic points.
 *
 *  Returns points in canvas space with their front/back flags, so the caller
 *  can split the polyline where it crosses the limb rather than drawing a
 *  chord across the globe.
 */
export function arcPoints(
  from: { lat: number; lng: number },
  to: { lat: number; lng: number },
  lam0: number, phi0: number,
  cx: number, cy: number, r: number,
  { samples = 24, altitude = 0 }: { samples?: number; altitude?: number } = {},
): Projected[] {
  const a = toVector(from.lat, from.lng, lam0, phi0)
  const b = toVector(to.lat, to.lng, lam0, phi0)
  const out: Projected[] = []
  for (let i = 0; i <= samples; i += 1) {
    const t = i / samples
    // A sine bow so the arc leaves and meets the surface flat rather than
    // kinking at its endpoints.
    const h = altitude > 0 ? altitude * Math.sin(Math.PI * t) : 0
    out.push(project(h > 0 ? lift(slerp(a, b, t), h) : slerp(a, b, t), cx, cy, r))
  }
  return out
}

/** Split a projected polyline into runs that are all on the same side.
 *
 *  Drawing an arc straight through would paint the half that is behind the
 *  earth over the half in front of it. Runs let the caller stroke the far side
 *  faint (or not at all) without losing the near side.
 */
export function visibleRuns(points: Projected[]): Projected[][] {
  const runs: Projected[][] = []
  let current: Projected[] = []
  let side: boolean | null = null
  for (const point of points) {
    if (side === null || point.front === side) {
      current.push(point)
      side = point.front
      continue
    }
    if (current.length > 1) runs.push(current)
    current = [point]
    side = point.front
  }
  if (current.length > 1) runs.push(current)
  return runs
}

/** The graticule, as geographic polylines — meridians then parallels.
 *
 *  Built once and projected per frame: the lines themselves never change, only
 *  where they land.
 */
export function graticule(
  meridianStep = 15, parallelStep = 15,
): Array<Array<{ lat: number; lng: number }>> {
  const lines: Array<Array<{ lat: number; lng: number }>> = []
  for (let lng = -180; lng < 180; lng += meridianStep) {
    const line = []
    for (let lat = -90; lat <= 90; lat += 5) line.push({ lat, lng })
    lines.push(line)
  }
  for (let lat = -90 + parallelStep; lat < 90; lat += parallelStep) {
    const line = []
    for (let lng = -180; lng <= 180; lng += 5) line.push({ lat, lng })
    lines.push(line)
  }
  return lines
}

/** The shortest signed rotation from one longitude to another, in degrees.
 *
 *  Used to ease the globe toward a firing pulse: without wrapping, a turn from
 *  +170° to −170° spins the long way round, 340° of travel for 20° of
 *  distance.
 */
export function shortestTurn(fromLng: number, toLng: number): number {
  let delta = (toLng - fromLng) % 360
  if (delta > 180) delta -= 360
  if (delta < -180) delta += 360
  return delta
}

/** The longitude that puts a pair on screen together: their midpoint along the
 *  shorter way round. */
export function midLongitude(a: number, b: number): number {
  return a + shortestTurn(a, b) / 2
}
