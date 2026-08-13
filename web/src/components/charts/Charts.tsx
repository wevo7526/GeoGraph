/** Chart primitives, as inline SVG.
 *
 *  No charting library on purpose. The explorer chunk already carries three.js
 *  at ~1.4 MB, and everything both surfaces need is a line, a band, a strip and
 *  a box — a few dozen lines of SVG each. A library would cost more than it
 *  draws, and these share their scales with `PressureLine`, which was already
 *  built this way.
 *
 *  All of them take a viewBox and scale to their container, so the page has no
 *  breakpoints to maintain. Colour comes from the theme tokens only: `--accent`
 *  and `--alert` are a validated diverging pair carrying the SIGN of a number,
 *  never decoration, so nothing here picks a colour by eye. */

const PAD = { top: 8, right: 8, bottom: 18, left: 30 }

function scale(value: number, lo: number, hi: number, size: number): number {
  if (hi === lo) return size / 2
  return ((value - lo) / (hi - lo)) * size
}

/** Nothing to draw is a state, not a blank: an empty chart that looks like a
 *  broken chart is the reason a reader stops trusting the ones that work. */
export function Empty({ note }: { note: string }) {
  return (
    <p className="mono text-[11px] py-6" style={{ color: 'var(--muted)' }}>
      {note}
    </p>
  )
}

export type Point = { x: number; y: number; lo?: number; hi?: number }

/** A line with an optional uncertainty band. The band is drawn first and
 *  never outlined — it is a range, and an outline reads as two more lines. */
export function LineBand({
  points,
  height = 120,
  width = 480,
  color = 'var(--accent)',
  yMax,
  label,
}: {
  points: Point[]
  height?: number
  width?: number
  color?: string
  yMax?: number
  label?: string
}) {
  if (!points.length) return <Empty note={label ?? 'no data'} />
  const w = width - PAD.left - PAD.right
  const h = height - PAD.top - PAD.bottom
  const xs = points.map((p) => p.x)
  const x0 = Math.min(...xs)
  const x1 = Math.max(...xs)
  const top = yMax ?? Math.max(...points.map((p) => p.hi ?? p.y), 0.0001)

  const px = (p: Point) => PAD.left + scale(p.x, x0, x1, w)
  const py = (v: number) => PAD.top + h - scale(v, 0, top, h)

  const line = points.map((p, i) => `${i ? 'L' : 'M'}${px(p)},${py(p.y)}`).join(' ')
  const banded = points.filter((p) => p.hi !== undefined && p.lo !== undefined)
  const band = banded.length
    ? [
        ...banded.map((p, i) => `${i ? 'L' : 'M'}${px(p)},${py(p.hi as number)}`),
        ...banded.reverse().map((p) => `L${px(p)},${py(p.lo as number)}`),
        'Z',
      ].join(' ')
    : null

  return (
    <svg viewBox={`0 0 ${width} ${height}`} width="100%" role="img" aria-label={label}>
      <line x1={PAD.left} y1={PAD.top + h} x2={width - PAD.right} y2={PAD.top + h}
            stroke="var(--line)" strokeWidth={1} />
      {band && <path d={band} fill={color} opacity={0.16} />}
      <path d={line} fill="none" stroke={color} strokeWidth={1.75} />
      <text x={2} y={PAD.top + 8} className="mono" fontSize={9} fill="var(--muted)">
        {top.toFixed(1)}
      </text>
      <text x={2} y={PAD.top + h} className="mono" fontSize={9} fill="var(--muted)">
        0
      </text>
    </svg>
  )
}

/** A dyad's whole history as a strip of bars, with an optional marker set.
 *  Bars rather than a line because the series is mostly zero — a line through
 *  a sparse series draws long flat stretches that read as measurements. */
export function Strip({
  values,
  marks = [],
  height = 64,
  width = 720,
  label,
}: {
  values: { x: number; y: number }[]
  marks?: number[]
  height?: number
  width?: number
  label?: string
}) {
  if (!values.length) return <Empty note={label ?? 'no history'} />
  const w = width - PAD.left - PAD.right
  const h = height - PAD.top - PAD.bottom
  const xs = values.map((v) => v.x)
  const x0 = Math.min(...xs)
  const x1 = Math.max(...xs)
  const top = Math.max(...values.map((v) => v.y), 0.0001)
  const barWidth = Math.max(1, w / Math.max(values.length, 1))
  const marked = new Set(marks)

  return (
    <svg viewBox={`0 0 ${width} ${height}`} width="100%" role="img" aria-label={label}>
      {values.map((v) => {
        const barHeight = scale(v.y, 0, top, h)
        return (
          <rect
            key={v.x}
            x={PAD.left + scale(v.x, x0, x1, w - barWidth)}
            y={PAD.top + h - barHeight}
            width={barWidth}
            height={Math.max(barHeight, v.y > 0 ? 1 : 0)}
            fill={marked.has(v.x) ? 'var(--alert)' : 'var(--accent)'}
            opacity={marked.has(v.x) ? 1 : 0.65}
          />
        )
      })}
      <line x1={PAD.left} y1={PAD.top + h} x2={width - PAD.right} y2={PAD.top + h}
            stroke="var(--line)" strokeWidth={1} />
    </svg>
  )
}

/** The precedent fan: median with p25–p75 and min–max, per offset. */
export function Fan({
  rows,
  height = 140,
  width = 480,
  label,
}: {
  rows: { offset: number; n: number; min: number; p25: number; median: number; p75: number; max: number }[]
  height?: number
  width?: number
  label?: string
}) {
  if (!rows.length) return <Empty note={label ?? 'no comparable episodes'} />
  const w = width - PAD.left - PAD.right
  const h = height - PAD.top - PAD.bottom
  const x0 = Math.min(...rows.map((r) => r.offset))
  const x1 = Math.max(...rows.map((r) => r.offset))
  const top = Math.max(...rows.map((r) => r.max), 0.0001)
  const px = (o: number) => PAD.left + scale(o, x0, x1, w)
  const py = (v: number) => PAD.top + h - scale(v, 0, top, h)
  type Band = 'min' | 'p25' | 'p75' | 'max'
  const area = (lo: Band, hi: Band) =>
    [
      ...rows.map((r, i) => `${i ? 'L' : 'M'}${px(r.offset)},${py(r[hi] as number)}`),
      ...[...rows].reverse().map((r) => `L${px(r.offset)},${py(r[lo] as number)}`),
      'Z',
    ].join(' ')

  return (
    <svg viewBox={`0 0 ${width} ${height}`} width="100%" role="img" aria-label={label}>
      <path d={area('min', 'max')} fill="var(--accent)" opacity={0.1} />
      <path d={area('p25', 'p75')} fill="var(--accent)" opacity={0.22} />
      <path
        d={rows.map((r, i) => `${i ? 'L' : 'M'}${px(r.offset)},${py(r.median)}`).join(' ')}
        fill="none" stroke="var(--accent)" strokeWidth={1.75}
      />
      <line x1={PAD.left} y1={PAD.top + h} x2={width - PAD.right} y2={PAD.top + h}
            stroke="var(--line)" strokeWidth={1} />
      {rows.map((r) => (
        <text key={r.offset} x={px(r.offset)} y={height - 5} fontSize={9}
              textAnchor="middle" className="mono" fill="var(--muted)">
          {r.offset === 0 ? 'Q0' : `+${r.offset}`}
        </text>
      ))}
    </svg>
  )
}

/** One market's measured effect distribution: min–max whisker, p25–p75 box,
 *  median tick. Signed, so the diverging pair does the work. */
export function BoxRow({
  row,
  domain,
}: {
  row: { market_name: string; n: number; min: number; p25: number; median: number; p75: number; max: number }
  domain: [number, number]
}) {
  const [lo, hi] = domain
  const at = (v: number) => `${((v - lo) / (hi - lo || 1)) * 100}%`
  const positive = row.median >= 0
  const color = positive ? 'var(--accent)' : 'var(--alert)'
  return (
    <div className="flex items-center gap-3 text-xs">
      <span className="w-28 shrink-0 truncate" title={row.market_name}>
        {row.market_name}
      </span>
      <span className="relative flex-1 h-4" style={{ background: 'var(--line)', opacity: 0.35 }}>
        <span className="absolute top-1/2 h-px" style={{
          left: at(row.min), width: `calc(${at(row.max)} - ${at(row.min)})`, background: color,
        }} />
        <span className="absolute top-0.5 bottom-0.5" style={{
          left: at(row.p25), width: `calc(${at(row.p75)} - ${at(row.p25)})`,
          background: color, opacity: 0.4,
        }} />
        <span className="absolute top-0 bottom-0 w-0.5" style={{ left: at(row.median), background: color }} />
      </span>
      <span className="mono w-16 text-right" style={{ color }}>
        {(row.median * 100).toFixed(2)}%
      </span>
      <span className="mono w-10 text-right" style={{ color: 'var(--muted)' }}>
        n={row.n}
      </span>
    </div>
  )
}
