/** The broadsheet UI kit — working-page primitives in the landing's language.
 *
 *  One accent, sign-carrying accent/alert pair, tabular figures, dot-leaders,
 *  rules-not-shadows. Every app page composes these so the surface reads as one
 *  paper. Presentational only: no data fetching, no business logic.
 */
import type { ReactNode } from 'react'

/* ── TEXT-REGISTER PRIMITIVES (2026-08-21) ───────────────────────────────────
 *  The serif/label/figure registers as components, so a page composes the one
 *  type scale instead of hand-rolling `text-sm`, an unclassed <p>, `mono
 *  text-[11px]`, or an inline maxWidth. Each maps to a helper class in
 *  styles.css. Presentational only.
 */

/** Body prose — the default reading register, measure-capped. Replaces the
 *  `text-sm` / unclassed <p> that carried working-page sentences. */
export function Prose({
  children,
  wide,
  className,
  style,
}: {
  children: ReactNode
  wide?: boolean
  className?: string
  style?: React.CSSProperties
}) {
  return (
    <p className={`prose-body${wide ? ' prose-body--wide' : ''}${className ? ` ${className}` : ''}`} style={style}>
      {children}
    </p>
  )
}

/** A mid-page lede: serif, muted, a rung above body (not the page header's
 *  standfirst, which StoryHead owns). */
export function Lede({ children, className }: { children: ReactNode; className?: string }) {
  return <p className={`prose-lede${className ? ` ${className}` : ''}`}>{children}</p>
}

/** A caption / figure note — the ONE caption tier. Everything that was a
 *  `text-xs` or `mono text-[11px]` caption is this. */
export function Caption({
  children,
  className,
  style,
}: {
  children: ReactNode
  className?: string
  style?: React.CSSProperties
}) {
  return <p className={`figure-note${className ? ` ${className}` : ''}`} style={style}>{children}</p>
}

/** The canonical LABEL: mono caps, one tracking, muted (or sign-coloured).
 *  Anything that was `mono uppercase tracking-[…]` by hand is this. Inline by
 *  default so it can lead a line; pass `as="div"` for a block label. */
export function Label({
  children,
  tone,
  as: Tag = 'span',
  className,
}: {
  children: ReactNode
  tone?: 'gain' | 'loss'
  as?: 'span' | 'div'
  className?: string
}) {
  const toneCls = tone === 'gain' ? ' kicker--gain' : tone === 'loss' ? ' kicker--loss' : ''
  return <Tag className={`kicker${toneCls}${className ? ` ${className}` : ''}`}>{children}</Tag>
}

/** A serif in-beat subsection title — NOT a label. The thing a kicker was being
 *  misused as when it sat above a table or a list to name it. */
export function SubHead({ children, className }: { children: ReactNode; className?: string }) {
  return <h3 className={`subhead${className ? ` ${className}` : ''}`}>{children}</h3>
}

/** A number set inside a sentence: tabular, optional emphasis and sign colour.
 *  The figure still comes from the payload — this only styles it. */
export function Num({
  children,
  emph,
  tone,
  className,
}: {
  children: ReactNode
  emph?: boolean
  tone?: 'gain' | 'loss'
  className?: string
}) {
  const toneCls = tone === 'gain' ? ' num--gain' : tone === 'loss' ? ' num--loss' : ''
  return <span className={`num${emph ? ' num--emph' : ''}${toneCls}${className ? ` ${className}` : ''}`}>{children}</span>
}

/** The important READ — a measured finding lifted out of the caption tier so it
 *  does not sit at the same level as a loading or error note. */
export function Read({ children, className }: { children: ReactNode; className?: string }) {
  return <p className={`read${className ? ` ${className}` : ''}`}>{children}</p>
}

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
          <p className="story-standfirst">
            {standfirst}
          </p>
        )}
      </div>
      {action}
    </header>
  )
}

/** A working-paper beat, ruled off from the last.
 *
 *  THE ASIDE SITS UNDER THE TITLE, NOT BESIDE IT. It used to share the
 *  headline's flex row with `margin-left:auto`, and a long aside then squeezed
 *  the h2 into two lines on every page that had one — "Where coercion is being
 *  / measured", "The transmission / map", "The paper / book". A subhead is also
 *  what the aside actually is: it explains what the beat measures, in a
 *  sentence, which is a serif job rather than an 11px monospace one.
 *
 *  NUMBERING IS OPTIONAL AND MEANS SEQUENCE. A numbered beat says "this is
 *  step n of a walk the reader takes in order"; most pages are not that, so
 *  `n` is passed only where the order carries information. */
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
        <div className="beat-title">
          {n !== undefined && (
            <span className="kicker" style={{ color: 'var(--accent)' }}>{`0${n}`.slice(-2)}</span>
          )}
          <h2>{title}</h2>
        </div>
        {aside && <p className="beat-aside">{aside}</p>}
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


/* ── THE THREE-BLOCK NARRATIVE (2026-08-21) ──────────────────────────────────
 *  Every analytic surface is read in three phases — History (what the record
 *  shows), Work (what the system did, and how far to trust it) and Forecast
 *  (where it points next). The AI-composed lead argues; the charts and tables
 *  beneath it are the evidence it argues from. When no lead is available (no
 *  key, or not generated yet) the phase still frames its evidence.
 */

/** Bold only names the model marked with **…**; everything else is plain. */
function withBold(text: string): ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*)/g).map((part, i) =>
    part.startsWith('**') && part.endsWith('**') ? (
      <strong key={i}>{part.slice(2, -2)}</strong>
    ) : (
      <span key={i}>{part}</span>
    ),
  )
}

/** The AI lead for a phase: the desk's prose, then a quiet provenance line so a
 *  reader knows it was composed (and whether the numbers have since moved). */
export function NarrativeLead({
  prose,
  model,
  generatedAt,
  stale,
}: {
  prose: string
  model?: string
  generatedAt?: string
  stale?: boolean | null
}) {
  const paragraphs = prose.split(/\n\n+/).filter((p) => p.trim())
  if (!paragraphs.length) return null
  const when = generatedAt ? generatedAt.slice(0, 10) : null
  return (
    <div className="narrative-lead">
      {paragraphs.map((p, i) => (
        <p key={i} className="prose-body">{withBold(p)}</p>
      ))}
      {(model || when || stale) && (
        <p className="narrative-prov">
          composed by the desk{model ? ` · ${model}` : ''}{when ? ` · ${when}` : ''}
          {stale ? ' · the numbers have moved since; a refresh is queued' : ''}
          {' '}· argued from the measured figures shown; no number originates here
        </p>
      )}
    </div>
  )
}

/** One phase of a page: a labelled head, the optional AI lead, then the evidence
 *  (existing beats/charts) as children. */
export function PhaseSection({
  phase,
  tagline,
  lead,
  children,
}: {
  phase: 'History' | 'Work' | 'Forecast'
  tagline?: string
  lead?: SurfaceNarrativeLead | null
  children: ReactNode
}) {
  return (
    <section className="phase">
      <div className="phase-head">
        <span className="phase-label">{phase}</span>
        {tagline && <span className="phase-tagline">{tagline}</span>}
      </div>
      {lead?.prose && (
        <NarrativeLead
          prose={lead.prose}
          model={lead.model}
          generatedAt={lead.generatedAt}
          stale={lead.stale}
        />
      )}
      {children}
    </section>
  )
}

/** The lead shape a page passes to a PhaseSection — the block's prose plus the
 *  narrative's shared provenance. */
export type SurfaceNarrativeLead = {
  prose: string | null | undefined
  model?: string
  generatedAt?: string
  stale?: boolean | null
}

/** A state word as a chip: the ABSOLUTE tone of a pair (cooperative … conflictual)
 *  beside a relative departure band, a solver name, a status. Colour is the
 *  diverging pair carrying sign; anything neutral wears ink. */
export function Chip({ label, tone }: { label: string; tone?: 'good' | 'bad' | 'ink' | 'muted' }) {
  const cls = tone === 'good' ? 'chip chip--good' : tone === 'bad' ? 'chip chip--bad' : tone === 'ink' ? 'chip chip--ink' : 'chip'
  return <span className={cls}>{label}</span>
}

// `toneOf` lived here: it turned the wire's mean-Goldstein `tone_label`
// ("friendly", "strained") into a colour. Removed 2026-08-17 with its last
// caller. That label ranks pairs by how much they TALK — it scored the United
// States and China, a declared rivalry, as "friendly" — and core/games/
// scenarios.py says in as many words that nothing user-facing may present it as
// a characterisation. What a pair IS comes from its declared standing.
