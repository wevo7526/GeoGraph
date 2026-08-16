/** The markets page: the region's international-finance story, written from
 *  the numbers — what its geopolitics has DONE to prices (the transmission
 *  map, measured), the events that moved markets most, where the solved games
 *  point next, how long the yield curve says a crisis lasts, where sovereign
 *  capital sits, and — last, as one paragraph of the story rather than the
 *  whole page — the paper book that translates the frozen call into P&L.
 *  Everything is persisted server-side (core/reasoning/markets.py builds the
 *  story; the paper book and the standing book are the ledger endpoints);
 *  the page states when it was computed and never recomputes a forecast. */
import { useEffect, useMemo, useState } from 'react'
import { getBacktest, getForward, getMarketsStory, lastFailureFor } from '../api'
import { useRegionLabel } from '../regions'
import type { BacktestLedger, ForwardView, MarketStoryMarket, MarketsStory } from '../types'
import { Beat, Chip, Disclosure, Empty, StoryHead } from '../ui'
import { Bars, Drawdown, EquityCurve, SeriesLine, Tiles, pct } from './charts/Kit'

const money = (v: number) => `${v < 0 ? '−' : ''}$${Math.abs(v).toLocaleString('en-US', { maximumFractionDigits: 0 })}`
const bn = (v: number) => `${v < 0 ? '−' : ''}$${(Math.abs(v) / 1e9).toFixed(1)}bn`
const signedPct = (v: number, digits = 2) => `${v >= 0 ? '+' : '−'}${pct(Math.abs(v), digits)}`

const KIND_WORDS: Record<string, string> = {
  sharp_escalation: 'sharp escalation',
  escalation: 'escalation',
  'de-escalation': 'de-escalation',
  stable: 'no departure',
}
const WINDOW_WORDS: Record<string, string> = {
  car_0_1: '2 sessions', car_0_3: '4 sessions', car_0_5: '6 sessions',
  intraday_open_close: 'intraday', monthly: 'the month', annual: 'the year',
}
const window = (w: string) => WINDOW_WORDS[w] ?? w

export default function MarketsPage({ region }: { region: string; onNavigate: (route: string) => void }) {
  const label = useRegionLabel(region)
  const [story, setStory] = useState<MarketsStory | null | undefined>(undefined)
  const [ledger, setLedger] = useState<BacktestLedger | null | undefined>(undefined)
  const [forward, setForward] = useState<ForwardView | null | undefined>(undefined)
  const [showAll, setShowAll] = useState(false)
  const [focus, setFocus] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    setStory(undefined); setLedger(undefined); setForward(undefined); setFocus(null)
    getMarketsStory(region).then((s) => live && setStory(s))
    getBacktest(region).then((l) => live && setLedger(l))
    getForward(region).then((f) => live && setForward(f))
    return () => { live = false }
  }, [region])

  const markets = story?.markets ?? []
  const focused = useMemo(
    () => markets.find((m) => m.ticker === focus) ?? markets.find((m) => m.headline) ?? markets[0] ?? null,
    [markets, focus],
  )

  if (story === undefined) return <div className="reading-column py-10"><Empty>Reading the measured record…</Empty></div>
  if (story === null) {
    const f = lastFailureFor('/api/markets/story')
    return (
      <div className="reading-column py-10">
        <StoryHead kicker={`Markets · ${label.toUpperCase()}`} title="The markets story did not answer"
                   standfirst={f?.detail ?? 'The API did not answer.'} />
      </div>
    )
  }
  if (story.pending) {
    return (
      <div className="reading-column py-10">
        <StoryHead kicker={`Markets · ${label.toUpperCase()}`} title="The markets story is being written"
                   standfirst={story.note ?? 'The markets job builds it on its first pass; come back in a few minutes.'} />
      </div>
    )
  }

  const headlined = markets.filter((m) => m.headline)
  const lead = headlined[0]
  const gulf = markets.find((m) => m.trading_calendar === 'gulf' && Object.keys(m.first_mover_share).length)
  const cov = story.coverage?.summary
  const rows = ledger?.rows ?? []
  const summary = ledger?.summary ?? null

  return (
    <div className="reading-column py-8">
      <StoryHead
        kicker={`Markets · ${label.toUpperCase()} · measured, never modelled`}
        title={lead
          ? `${lead.name} moves ${signedPct(lead.headline!.median)} when ${label} escalates sharply`
          : `How ${label} moves markets`}
        standfirst={story.explanation[0]}
        action={
          <span className="mono text-[11px] text-right" style={{ color: 'var(--muted)' }}>
            {story.computed_at ? `written ${story.computed_at.slice(0, 16).replace('T', ' ')} UTC` : ''}<br />
            {story.as_of ? `archive as of ${story.as_of}` : ''}
          </span>
        }
      />

      <div className="mt-8">
        <Tiles items={[
          { label: 'markets measured', value: String(markets.filter((m) => m.measured).length), sub: `${markets.length} in the pack` },
          { label: 'measured effects', value: markets.reduce((a, m) => a + m.measured, 0).toLocaleString('en-US'), sub: 'event × market × window' },
          { label: 'events with an effect', value: cov ? `${cov.events_measured.toLocaleString('en-US')}` : '—', sub: cov ? `${pct(cov.share_measured, 0)} of ${cov.events.toLocaleString('en-US')} in the region` : 'coverage pending' },
          { label: gulf ? 'Gulf prints first' : 'first mover', value: gulf ? pct(gulf.first_mover_share.sharp_escalation ?? gulf.first_mover_share.escalation ?? 0, 0) : '—', sub: gulf ? `${gulf.name}, Sun–Thu, on sharp escalations` : 'no Gulf market in this pack' },
        ]} />
      </div>

      {/* 1 — THE TRANSMISSION MAP */}
      <Beat n={1} title="The transmission map" major aside="median abnormal return over 4 sessions, per market, when the region's coded record shows a sharp escalation">
        {headlined.length ? (
          <Bars
            rows={headlined.map((m) => ({
              key: m.ticker, label: m.name, value: m.headline!.median,
              sub: `n=${m.headline!.n} · ${pct(m.headline!.share_positive, 0)} up`,
            }))}
            signed format={(v) => signedPct(v)} onPick={setFocus}
          />
        ) : (
          <Empty>No market holds enough measured effects for a headline yet — the transmission engine is still measuring this region.</Empty>
        )}
        {markets.some((m) => !m.headline) && (
          <p className="mono text-[11px] mt-3" style={{ color: 'var(--muted)' }}>
            not yet headlined: {markets.filter((m) => !m.headline).map((m) => `${m.name} (${m.measured.toLocaleString('en-US')} measured)`).join(' · ')}
          </p>
        )}
        <p className="text-sm mt-4" style={{ maxWidth: '64ch' }}>
          A bar is a <em>median</em> over every measured event of that kind — the direction and size the market
          typically moved beyond what its own estimation window expected. Click a market to open its full response
          by kind and window, and the events that moved it most.
        </p>
      </Beat>

      {/* 2 — ONE MARKET IN FULL */}
      {focused && (
        <Beat n={2} title={`${focused.name} — the response by kind of event`} aside={`${focused.measured.toLocaleString('en-US')} measured effects · ${focused.market_type ?? ''} · since ${focused.inception_date ?? '—'}`}>
          <div className="toolbar mb-3" style={{ borderTop: 'none' }}>
            {markets.map((m) => (
              <button key={m.ticker} className="btn btn--quiet" aria-pressed={m.ticker === focused.ticker} onClick={() => setFocus(m.ticker)}>{m.ticker}</button>
            ))}
          </div>
          <div className="scroll-x">
            <table className="mono text-[11px]" style={{ borderCollapse: 'separate', borderSpacing: '10px 3px' }}>
              <thead>
                <tr style={{ color: 'var(--muted)' }}>
                  <th className="text-left font-normal">kind of event</th>
                  {focused.windows.map((w) => <th key={w} className="text-right font-normal">{window(w)}</th>)}
                </tr>
              </thead>
              <tbody>
                {Object.entries(focused.response).map(([kind, byWindow]) => (
                  <tr key={kind}>
                    <td>{KIND_WORDS[kind] ?? kind}</td>
                    {focused.windows.map((w) => {
                      const c = byWindow[w]
                      if (!c) return <td key={w} className="text-right" style={{ color: 'var(--muted)' }}>—</td>
                      return (
                        <td key={w} className="text-right" title={`p25 ${signedPct(c.p25)} · p75 ${signedPct(c.p75)} · ${pct(c.share_positive, 0)} positive`}
                            style={{ color: c.thin ? 'var(--muted)' : c.median >= 0 ? 'var(--accent)' : 'var(--alert)' }}>
                          {signedPct(c.median)} <span style={{ color: 'var(--muted)' }}>n={c.n}{c.thin ? ' thin' : ''}</span>
                        </td>
                      )
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {Object.keys(focused.first_mover_share).length > 0 && (
            <p className="mono text-[11px] mt-2" style={{ color: 'var(--muted)' }}>
              printed first: {Object.entries(focused.first_mover_share).map(([k, v]) => `${pct(v, 0)} of ${KIND_WORDS[k] ?? k}s`).join(' · ')}
            </p>
          )}
          {focused.biggest_moves.length > 0 && (
            <div className="mt-5">
              <div className="kicker mb-2">The events that moved {focused.name} most (4 sessions)</div>
              <ul className="space-y-1 text-xs">
                {focused.biggest_moves.map((e) => (
                  <li key={e.event_id} className="flex items-baseline gap-3">
                    <span className="mono w-16 shrink-0 text-right" style={{ color: e.abnormal_return >= 0 ? 'var(--accent)' : 'var(--alert)' }}>{signedPct(e.abnormal_return)}</span>
                    <span className="mono w-24 shrink-0" style={{ color: 'var(--muted)' }}>{e.date}</span>
                    <span className="truncate">{e.name}{e.pair ? <span style={{ color: 'var(--muted)' }}> · {e.pair}</span> : null}</span>
                    <Chip label={KIND_WORDS[e.kind] ?? e.kind} tone={e.kind.includes('escalation') && e.kind !== 'de-escalation' ? 'bad' : 'muted'} />
                    {e.first_mover && <Chip label="first" tone="ink" />}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </Beat>
      )}

      {/* 3 — WHERE THE GAMES POINT */}
      <Beat n={3} title="Where the solved games point" aside="the region's escalatory courses with the most mass, priced to the measured map">
        {story.forward && story.forward.direction.length ? (
          <>
            <Bars
              rows={story.forward.direction.slice(0, 8).map((d) => ({
                key: d.market_id, label: d.market_name, value: d.expected_abnormal_return,
                sub: `${d.courses} courses · ${d.measurements} moves`,
              }))}
              signed format={(v) => signedPct(v)}
            />
            <ul className="mt-4 space-y-2 text-sm">
              {story.forward.courses.slice(0, 4).map((c, i) => (
                <li key={i}>
                  <b>{c.dyad_name}</b> — {c.kind_label ?? c.kind.replace(/_/g, ' ')} at {pct(c.likelihood, 0)}, ending {c.end_label}
                  {c.market_implications.length
                    ? <span style={{ color: 'var(--muted)' }}> · {c.market_implications.slice(0, 3).map((m) => `${m.market_name} ${signedPct(m.median)} (n=${m.n})`).join(', ')}</span>
                    : <span style={{ color: 'var(--muted)' }}> · no priced market clears the evidence bar</span>}
                </li>
              ))}
            </ul>
            <p className="mono text-[11px] mt-3" style={{ color: 'var(--muted)', maxWidth: '68ch' }}>{story.forward.note}</p>
          </>
        ) : (
          <Empty>{story.forward ? 'No escalatory course carries a priced market yet.' : 'No persisted game map for this region yet — the games job solves it on its first pass.'}</Empty>
        )}
      </Beat>

      {/* 4 — THE CURVE */}
      <Beat n={4} title="How long the curve says it lasts" aside="the long end's share of the yield curve's response, per pair">
        {story.duration && story.duration.dyads.length ? (
          <>
            <Bars
              rows={story.duration.dyads.map((d) => ({
                key: d.dyad_id, label: d.dyad_name ?? d.dyad_id, value: d.implied_persistence,
                sub: `${d.n} events · p25 ${pct(d.p25, 0)} p75 ${pct(d.p75, 0)}`,
              }))}
              format={(v) => pct(v, 0)}
            />
            <p className="text-sm mt-3" style={{ maxWidth: '64ch' }}>
              A crisis the market expects to pass moves the front end; one it expects to last moves the ten-year.
              {' '}{story.duration.events_with_a_curve_response ?? 0} events carry both, over {story.duration.tenors_measured?.join(', ')}.
            </p>
            <p className="mono text-[11px] mt-2" style={{ color: 'var(--muted)', maxWidth: '68ch' }}>{story.duration.calibration}</p>
          </>
        ) : (
          <Empty>{story.duration?.note ?? 'No pair holds enough curve measurements yet.'}</Empty>
        )}
      </Beat>

      {/* 5 — SOVEREIGN CAPITAL */}
      {story.sovereign_capital && story.sovereign_capital.funds.length > 0 && (
        <Beat n={5} title="Where sovereign capital sits" aside="the pack's sovereign wealth funds' reported US equity, latest quarter">
          <Bars
            rows={story.sovereign_capital.funds.map((f) => ({
              key: f.actor_id, label: f.name, value: f.value_usd,
              sub: `${f.as_of}${f.change_usd != null ? ` · ${bn(f.change_usd)} q/q` : ''}`,
            }))}
            format={bn}
          />
          <p className="mono text-[11px] mt-3" style={{ color: 'var(--muted)', maxWidth: '68ch' }}>{story.sovereign_capital.note}</p>
        </Beat>
      )}

      {/* 6 — THE PAPER BOOK, condensed */}
      <Beat n={6} title="The paper book" aside="the frozen call, translated mechanically into $1M of positions and marked — one paragraph of the story, not the page">
        {ledger === undefined && <Empty>Reading the ledger…</Empty>}
        {ledger === null && <Empty>{lastFailureFor('/api/trading/backtest')?.detail ?? 'no paper model for this region'}</Empty>}
        {ledger && summary && (
          <>
            <Tiles items={[
              { label: 'total return', value: `${summary.total_return >= 0 ? '+' : ''}${pct(summary.total_return, 1)}`, tone: summary.total_return >= 0 ? 'gain' : 'loss', sub: `${money(summary.final_equity_usd)} final equity · ${summary.first_quarter} → ${summary.last_quarter}` },
              { label: 'hit rate', value: pct(summary.hit_rate, 0), sub: `${summary.quarters_traded} traded · ${summary.quarters_skipped ?? 0} skipped` },
              { label: 'max drawdown', value: `−${pct(summary.max_drawdown, 1)}`, tone: 'loss', sub: 'from the running peak' },
              { label: 'standing book', value: forward?.book ? money(forward.book.pnl_usd) : '—', tone: forward?.book ? (forward.book.pnl_usd >= 0 ? 'gain' : 'loss') : 'plain', sub: forward ? `call of ${forward.forecast.as_of} at ${pct(forward.forecast.escalation_likelihood, 0)}` : forward === null ? 'no frozen call' : 'marking…' },
            ]} />
            {rows.length > 1 && (
              <div className="mt-4">
                <EquityCurve rows={rows} notional={summary.notional_usd ?? 1_000_000} />
                <div className="mt-3">
                  <div className="kicker mb-1">Drawdown from peak</div>
                  <Drawdown rows={ledger.drawdown ?? []} />
                </div>
              </div>
            )}
            {ledger.attribution && ledger.attribution.length > 0 && (
              <div className="mt-4">
                <div className="kicker mb-1">Where the P&L came from</div>
                <Bars rows={ledger.attribution.map((a) => ({ key: a.ticker, label: a.ticker, value: a.pnl_usd, sub: `${a.quarters} q · hit ${a.hit_rate !== null ? pct(a.hit_rate, 0) : '—'}` }))}
                      signed format={money} />
              </div>
            )}
            <Disclosure label={`the ledger · ${rows.length} quarters`}>
              <div className="scroll-x mt-2">
                <table className="mono text-[11px] w-full" style={{ borderCollapse: 'collapse' }}>
                  <thead>
                    <tr style={{ color: 'var(--muted)', borderBottom: '1px solid var(--rule-strong)' }}>
                      <th className="text-left font-normal py-1">quarter</th><th className="text-right font-normal">P(esc)</th>
                      <th className="text-right font-normal">P&L</th><th className="text-right font-normal">return</th>
                      <th className="text-right font-normal">equity</th><th className="text-left font-normal pl-3">positions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[...rows].reverse().slice(0, showAll ? rows.length : 12).map((r) => (
                      <tr key={r.quarter_end} style={{ borderBottom: '1px dotted var(--line)' }}>
                        <td className="py-1">{r.quarter_end}</td>
                        <td className="text-right">{pct(r.escalation_likelihood, 0)}</td>
                        <td className="text-right" style={{ color: r.pnl_usd >= 0 ? 'var(--accent)' : 'var(--alert)' }}>{money(r.pnl_usd)}</td>
                        <td className="text-right" style={{ color: r.quarter_return >= 0 ? 'var(--accent)' : 'var(--alert)' }}>{r.quarter_return >= 0 ? '+' : ''}{pct(r.quarter_return, 2)}</td>
                        <td className="text-right">{money(r.equity_usd)}</td>
                        <td className="pl-3 truncate" style={{ maxWidth: 320, color: 'var(--muted)' }}>
                          {r.positions.map((p) => `${p.weight >= 0 ? '+' : ''}${p.weight.toFixed(2)} ${p.ticker}`).join(' · ')}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {rows.length > 12 && (
                  <button className="article-link mono text-[11px] mt-2" onClick={() => setShowAll((v) => !v)}>
                    {showAll ? 'show the latest 12' : `show all ${rows.length} quarters`}
                  </button>
                )}
              </div>
            </Disclosure>
            {forward?.pressure && (
              <div className="mt-5">
                <div className="kicker mb-1">Long-horizon pressure (structural composite)</div>
                <SeriesLine
                  points={Object.entries(forward.pressure.trajectory).sort(([a], [b]) => a.localeCompare(b)).map(([x, y]) => ({ x, y }))}
                  height={120} format={(v) => v.toFixed(2)} label="structural pressure"
                />
                <p className="text-xs italic mt-2" style={{ color: 'var(--muted)', maxWidth: '68ch' }}>{forward.pressure.boundary_statement}</p>
              </div>
            )}
          </>
        )}
        {ledger && !summary && <p className="text-sm" style={{ maxWidth: '62ch' }}>{ledger.note}</p>}
      </Beat>

      {/* 7 — THE REST OF THE STORY, AND THE METHOD */}
      <Beat n={7} title="In words" aside="written from the numbers above — every figure is a field">
        {story.explanation.slice(1).map((p, i) => (
          <p key={i} className="text-sm leading-relaxed mb-3" style={{ maxWidth: '68ch' }}>{p}</p>
        ))}
        <Disclosure label="method">
          <p className="text-xs leading-relaxed mt-2" style={{ color: 'var(--muted)', maxWidth: '72ch' }}>{story.method}</p>
          {ledger?.method && <p className="text-xs leading-relaxed mt-2" style={{ color: 'var(--muted)', maxWidth: '72ch' }}>Paper book: {ledger.method}</p>}
        </Disclosure>
        <p className="mt-3 text-xs italic" style={{ color: 'var(--muted)', maxWidth: '68ch' }}>
          Measured, mechanical, out of sample by construction. Not advice.
        </p>
      </Beat>
    </div>
  )
}

// Keeps the type import in use for readers of this file's exports.
export type { MarketStoryMarket }
