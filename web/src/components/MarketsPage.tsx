/** The markets page: the paper-money model's walk-forward backtest for the
 *  region under the lens — equity curve, drawdown, the quarter ledger, per-
 *  market attribution and the recorded skips — beside the standing book (the
 *  latest frozen call marked at the latest close) and the long-horizon
 *  pressure. Everything here is persisted or frozen server-side; the page
 *  states when it was computed and never recomputes a forecast. */
import { useEffect, useMemo, useState } from 'react'
import { getBacktest, getForward, lastFailureFor } from '../api'
import { useRegionLabel } from '../regions'
import type { BacktestLedger, ForwardView } from '../types'
import { Beat, Disclosure, Empty, StoryHead } from '../ui'
import { Bars, Drawdown, EquityCurve, SeriesLine, Tiles, pct } from './charts/Kit'

const money = (v: number) => `${v < 0 ? '−' : ''}$${Math.abs(v).toLocaleString('en-US', { maximumFractionDigits: 0 })}`

export default function MarketsPage({ region }: { region: string; onNavigate: (route: string) => void }) {
  const label = useRegionLabel(region)
  const [ledger, setLedger] = useState<BacktestLedger | null | undefined>(undefined)
  const [forward, setForward] = useState<ForwardView | null | undefined>(undefined)
  const [showAll, setShowAll] = useState(false)

  useEffect(() => {
    let live = true
    setLedger(undefined)
    setForward(undefined)
    getBacktest(region).then((l) => live && setLedger(l))
    getForward(region).then((f) => live && setForward(f))
    return () => { live = false }
  }, [region])

  const rows = ledger?.rows ?? []
  const summary = ledger?.summary ?? null
  const yearly = useMemo(() => {
    const byYear = new Map<string, { pnl: number; quarters: number; wins: number }>()
    for (const r of rows) {
      const y = r.quarter_end.slice(0, 4)
      const slot = byYear.get(y) ?? { pnl: 0, quarters: 0, wins: 0 }
      slot.pnl += r.pnl_usd
      slot.quarters += 1
      slot.wins += r.pnl_usd > 0 ? 1 : 0
      byYear.set(y, slot)
    }
    return [...byYear.entries()].map(([year, v]) => ({ year, ...v }))
  }, [rows])

  if (ledger === undefined) return <div className="reading-column py-10"><Empty>Reading the ledger…</Empty></div>
  if (ledger === null) {
    const f = lastFailureFor('/api/trading/backtest')
    return (
      <div className="reading-column py-10">
        <StoryHead kicker={`Markets · ${label.toUpperCase()}`} title="No paper model for this region"
                   standfirst={f?.detail ?? 'The API did not answer.'} />
      </div>
    )
  }

  const notional = summary?.notional_usd ?? 1_000_000
  const books = ledger.books

  return (
    <div className="reading-column py-8">
      <StoryHead
        kicker={`Markets · ${label.toUpperCase()} · paper money, $1M notional`}
        title={summary ? `${summary.total_return >= 0 ? '+' : ''}${pct(summary.total_return, 1)} over ${summary.quarters_traded} traded quarters` : 'The paper model has not traded here'}
        standfirst={
          summary
            ? `The near-term call, recomputed at every past quarter end from only what existed then, translated through the pack's books and marked at the next quarter end. ${summary.first_quarter} → ${summary.last_quarter}; ${summary.quarters_skipped ?? 0} quarters were recorded skips.`
            : ledger.note ?? 'No ledger yet.'
        }
        action={
          <span className="mono text-[11px] text-right" style={{ color: 'var(--muted)' }}>
            {ledger.computed_at ? `computed ${ledger.computed_at.slice(0, 16).replace('T', ' ')} UTC` : ''}
          </span>
        }
      />

      {summary && (
        <div className="mt-8">
          <Tiles items={[
            { label: 'total return', value: `${summary.total_return >= 0 ? '+' : ''}${pct(summary.total_return, 1)}`, tone: summary.total_return >= 0 ? 'gain' : 'loss', sub: `${money(summary.final_equity_usd)} final equity` },
            { label: 'hit rate', value: pct(summary.hit_rate, 0), sub: `${summary.quarters_traded} traded · ${summary.quarters_skipped ?? 0} skipped` },
            { label: 'max drawdown', value: `−${pct(summary.max_drawdown, 1)}`, tone: 'loss', sub: 'from the running peak, trade-time' },
            { label: 'best / worst quarter', value: `${summary.best_quarter?.slice(0, 7) ?? '—'} / ${summary.worst_quarter?.slice(0, 7) ?? '—'}` },
          ]} />
        </div>
      )}

      {rows.length > 1 ? (
        <Beat n={1} title="The equity curve" major aside="compounded only through fully-entered books">
          <EquityCurve rows={rows} notional={notional} />
          <div className="mt-4">
            <div className="kicker mb-1">Drawdown from peak</div>
            <Drawdown rows={ledger.drawdown ?? []} />
          </div>
        </Beat>
      ) : (
        <Beat n={1} title="Why nothing compounded" major>
          <p className="text-sm" style={{ maxWidth: '62ch' }}>{ledger.note}</p>
        </Beat>
      )}

      {ledger.skip_reasons && ledger.skip_reasons.length > 0 && (
        <Beat n={2} title="The recorded skips" aside="a quarter that stood aside, and why — never compounded">
          <Bars rows={ledger.skip_reasons.map((r) => ({ key: r.reason, label: r.reason, value: r.quarters, sub: `${r.first?.slice(0, 7)} → ${r.last?.slice(0, 7)}` }))}
                format={(v) => `${v} q`} />
          <p className="mono text-[11px] mt-3" style={{ color: 'var(--muted)', maxWidth: '68ch' }}>
            e.g. “{ledger.skip_reasons[0].example}”
          </p>
        </Beat>
      )}

      {ledger.attribution && ledger.attribution.length > 0 && (
        <Beat n={3} title="Where the P&L came from" aside="per market, over every held position">
          <Bars rows={ledger.attribution.map((a) => ({ key: a.ticker, label: a.ticker, value: a.pnl_usd, sub: `${a.quarters} q · hit ${a.hit_rate !== null ? pct(a.hit_rate, 0) : '—'}` }))}
                signed format={money} />
          {books && (
            <p className="mono text-[11px] mt-3" style={{ color: 'var(--muted)' }}>
              escalation book: {Object.entries(books.escalation).map(([t, w]) => `${w >= 0 ? '+' : ''}${w} ${t}`).join(', ')} ·
              reversion book: {Object.entries(books.reversion).map(([t, w]) => `${w >= 0 ? '+' : ''}${w} ${t}`).join(', ')} ·
              net weight per ticker = p·escalation + (1−p)·reversion
            </p>
          )}
        </Beat>
      )}

      {yearly.length > 0 && (
        <Beat n={4} title="Year by year" aside="P&L on the notional, quarters traded, hit rate">
          <Bars rows={yearly.map((y) => ({ key: y.year, label: y.year, value: y.pnl, sub: `${y.quarters} q · ${y.quarters ? pct(y.wins / y.quarters, 0) : '—'}` }))}
                signed format={money} />
        </Beat>
      )}

      {rows.length > 0 && (
        <Beat n={5} title="The ledger" aside={`${rows.length} quarters · newest first`}>
          <div className="scroll-x">
            <table className="mono text-[11px] w-full" style={{ borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ color: 'var(--muted)', borderBottom: '1px solid var(--rule-strong)' }}>
                  <th className="text-left font-normal py-1">quarter</th><th className="text-right font-normal">P(esc)</th>
                  <th className="text-right font-normal">episodes</th><th className="text-right font-normal">P&L</th>
                  <th className="text-right font-normal">return</th><th className="text-right font-normal">equity</th>
                  <th className="text-left font-normal pl-3">positions</th>
                </tr>
              </thead>
              <tbody>
                {[...rows].reverse().slice(0, showAll ? rows.length : 16).map((r) => (
                  <tr key={r.quarter_end} style={{ borderBottom: '1px dotted var(--line)' }}>
                    <td className="py-1">{r.quarter_end}</td>
                    <td className="text-right">{pct(r.escalation_likelihood, 0)}</td>
                    <td className="text-right">{r.episodes}</td>
                    <td className="text-right" style={{ color: r.pnl_usd >= 0 ? 'var(--accent)' : 'var(--alert)' }}>{money(r.pnl_usd)}</td>
                    <td className="text-right" style={{ color: r.quarter_return >= 0 ? 'var(--accent)' : 'var(--alert)' }}>{r.quarter_return >= 0 ? '+' : ''}{pct(r.quarter_return, 2)}</td>
                    <td className="text-right">{money(r.equity_usd)}</td>
                    <td className="pl-3 truncate" style={{ maxWidth: 320, color: 'var(--muted)' }}>
                      {r.positions.map((p) => `${p.weight >= 0 ? '+' : ''}${p.weight.toFixed(2)} ${p.ticker}${p.status === 'marked' && p.pnl_usd !== undefined ? ` (${money(p.pnl_usd)})` : ''}`).join(' · ')}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {rows.length > 16 && (
            <button className="article-link mono text-[11px] mt-2" onClick={() => setShowAll((v) => !v)}>
              {showAll ? 'show the latest 16' : `show all ${rows.length} quarters`}
            </button>
          )}
        </Beat>
      )}

      <Beat n={6} title="The standing book" aside="the latest frozen call, marked at the latest close">
        {forward === undefined && <Empty>Marking the book…</Empty>}
        {forward === null && <Empty>{lastFailureFor('/api/trading/forward')?.detail ?? 'no frozen near-term call for this region'}</Empty>}
        {forward && (
          <>
            <p className="text-sm" style={{ maxWidth: '62ch' }}>
              The frozen call of {forward.forecast.as_of} puts escalation at <b>{pct(forward.forecast.escalation_likelihood, 0)}</b> over the horizon
              {forward.forecast.horizon_end ? ` to ${forward.forecast.horizon_end}` : ''}; net weights:{' '}
              {Object.entries(forward.net_weights).map(([t, w]) => `${w >= 0 ? '+' : ''}${w.toFixed(2)} ${t}`).join(', ')}.
            </p>
            {forward.book ? (
              <div className="mt-3">
                <Tiles items={[
                  { label: 'P&L on notional', value: money(forward.book.pnl_usd), tone: forward.book.pnl_usd >= 0 ? 'gain' : 'loss', sub: `${pct(forward.book.return_on_notional, 2)} of $1M` },
                  { label: 'deployed', value: money(forward.book.deployed_usd) },
                  { label: 'positions marked', value: `${forward.book.positions.filter((p) => p.status === 'marked').length} / ${forward.book.positions.length}` },
                  { label: 'entered after', value: forward.forecast.as_of },
                ]} />
                <ul className="mt-3 space-y-1 text-xs mono">
                  {forward.book.positions.map((p) => (
                    <li key={p.ticker} className="flex justify-between gap-3">
                      <span>{p.weight >= 0 ? '+' : ''}{p.weight.toFixed(2)} {p.ticker}</span>
                      <span style={{ color: 'var(--muted)' }}>
                        {p.status === 'marked' ? `${p.entry_date} ${p.entry?.toFixed(2)} → ${p.mark_date} ${p.mark?.toFixed(2)}` : p.reason}
                      </span>
                      <span style={{ color: (p.pnl_usd ?? 0) >= 0 ? 'var(--accent)' : 'var(--alert)' }}>{p.pnl_usd !== undefined ? money(p.pnl_usd) : '—'}</span>
                    </li>
                  ))}
                </ul>
              </div>
            ) : (
              <p className="text-xs italic mt-2" style={{ color: 'var(--muted)' }}>{forward.book_unavailable ?? 'book unavailable'}</p>
            )}
            {forward.pressure && (
              <div className="mt-6">
                <div className="kicker mb-1">Long-horizon pressure (structural composite)</div>
                <SeriesLine
                  points={Object.entries(forward.pressure.trajectory).sort(([a], [b]) => a.localeCompare(b)).map(([x, y]) => ({ x, y }))}
                  height={140} format={(v) => v.toFixed(2)} label="structural pressure"
                />
                <p className="text-xs italic mt-2" style={{ color: 'var(--muted)', maxWidth: '68ch' }}>{forward.pressure.boundary_statement}</p>
              </div>
            )}
          </>
        )}
      </Beat>

      <Beat n={7} title="Method">
        <Disclosure label="the rule, in full">
          <p className="text-xs leading-relaxed mt-2" style={{ color: 'var(--muted)', maxWidth: '72ch' }}>{ledger.method}</p>
        </Disclosure>
        <p className="mt-3 text-xs italic" style={{ color: 'var(--muted)', maxWidth: '68ch' }}>
          Mechanical, unfitted, out of sample by construction. Not advice.
        </p>
      </Beat>
    </div>
  )
}
