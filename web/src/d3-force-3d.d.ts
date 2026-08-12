/**
 * Minimal typings for `d3-force-3d`, which ships none and has no @types
 * package. Inherited from MarketGraph, same reasoning:
 *
 * DELIBERATELY NARROW. This declares only the forces Graph3D actually uses
 * and only the methods it calls, rather than a `declare module '...'` with an
 * `any` body. A blanket `any` would silence real mistakes — passing a radius
 * where a strength belongs, or calling a method the library does not have —
 * which is the entire reason this file exists instead of a `// @ts-ignore`.
 */
declare module 'd3-force-3d' {
  interface Force<N> {
    (alpha: number): void
    initialize?: (nodes: N[]) => void
  }

  interface CollideForce<N> extends Force<N> {
    /** Minimum separation, in the same units as the rendered radius. */
    radius(radius: number | ((node: N, i: number, nodes: N[]) => number)): CollideForce<N>
    /** 0–1. Below 1 the constraint is soft and overlaps resolve over several ticks. */
    strength(strength: number): CollideForce<N>
    iterations(iterations: number): CollideForce<N>
  }

  interface CenterForce<N> extends Force<N> {
    x(x: number): CenterForce<N>
    y(y: number): CenterForce<N>
    z(z: number): CenterForce<N>
    strength(strength: number): CenterForce<N>
  }

  interface AxisForce<N> extends Force<N> {
    strength(strength: number): AxisForce<N>
  }

  export function forceCollide<N>(
    radius?: number | ((node: N, i: number, nodes: N[]) => number),
  ): CollideForce<N>

  export function forceCenter<N = unknown>(x?: number, y?: number, z?: number): CenterForce<N>

  export function forceX<N = unknown>(x?: number): AxisForce<N>
  export function forceY<N = unknown>(y?: number): AxisForce<N>
  export function forceZ<N = unknown>(z?: number): AxisForce<N>
}
