/** The broadsheet UI kit — working-page primitives in the landing's language.
 *
 *  One accent, sign-carrying accent/alert pair, tabular figures, dot-leaders,
 *  rules-not-shadows. Every app page composes these so the surface reads as one
 *  paper. Presentational only: no data fetching, no business logic.
 */
import type { ReactNode } from 'react'

/** A page header set as a story head: kicker over a headline over a standfirst,
 *  with a right-hand slot for the page's one action. */
export function StoryHead({
  kicker,
  title,
  standfirst,
  action,
}: {
  kicker: string
  title: ReactNode
  standfirst?: ReactNode
  action?: ReactNode
}) {
  return (
    <header className="story-head">
      <div style={{ minWidth: 0 }}>
        <div className="kicker">{kicker}</div>
        <h1>{title}</h1>
        {standfirst && (
          <p className="mt-2 text-lg leading-snug" style={{ color: 'var(--muted)', maxWidth: '52ch' }}>
            {standfirst}
          </p>
        )}
      </div>
      {action}
    </header>
  )
}

/** A numbered working-paper beat, ruled off from the last. */
export function Beat({
  n,
  title,
  major,
  aside,
  children,
}: {
  n?: number
  title: string
  major?: boolean
  aside?: ReactNode
  children: ReactNode
}) {
  return (
    <section className={major ? 'beat beat--major' : 'beat'}>
      <div className="beat-head">
        {n !== undefined && <span className="kicker" style={{ color: 'var(--accent)' }}>{`0${n}`.slice(-2)}</span>}
        <h2>{title}</h2>
        {aside && <span className="mono text-[11px]" style={{ color: 'var(--muted)', marginLeft: 'auto' }}>{aside}</span>}
      </div>
      {children}
    </section>
  )
}

/** A market-move row: name · dot-leader · signed figure, coloured by sign. */
export function MoveRow({
  name,
  pct,
  sub,
}: {
  name: string
  pct: number
  sub?: string
}) {
  const cls = pct > 0 ? 'move-figure--gain' : pct < 0 ? 'move-figure--loss' : ''
  const text = `${pct >= 0 ? '+' : ''}${pct.toFixed(1)}%`
  return (
    <div className="move-row">
      <span className="move-name">{name}</span>
      <span className="move-leader" aria-hidden="true" />
      {sub && <span className="move-sub">{sub}</span>}
      <span className={`move-figure ${cls}`}>{text}</span>
    </div>
  )
}

/** A tension badge — a temperature word with a trend arrow, coloured by where
 *  it is heading (accent = cooling, alert = heating). */
export function TensionBadge({ label, trend }: { label: string; trend?: 'rising' | 'falling' | 'steady' }) {
  const arrow = trend === 'rising' ? '↑' : trend === 'falling' ? '↓' : '→'
  const cls = trend === 'rising' ? 'badge--hot' : trend === 'falling' ? 'badge--calm' : 'badge--neutral'
  return (
    <span className={`badge ${cls}`}>
      {label} {arrow}
    </span>
  )
}

/** A row of tabular figures under a header. */
export function StatLine({ items }: { items: Array<{ label: string; value: ReactNode }> }) {
  return (
    <dl className="statline">
      {items.map((it) => (
        <div key={it.label}>
          <dt>{it.label}</dt>
          <dd>{it.value}</dd>
        </div>
      ))}
    </dl>
  )
}

/** A quiet "show me why" disclosure that folds evidence under a claim. */
export function Disclosure({ label, children }: { label: string; children: ReactNode }) {
  return (
    <details className="disclosure mt-3">
      <summary>{label}</summary>
      <div className="mt-3">{children}</div>
    </details>
  )
}

/** An honest empty/loading note — never a fabricated zero. */
export function Empty({ children }: { children: ReactNode }) {
  return <p className="note-empty">{children}</p>
}
