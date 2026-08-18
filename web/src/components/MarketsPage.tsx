/** The markets page: what this region's geopolitics has DONE to prices, and
 *  how a fund following the frozen intel book would have marked.
 *
 *  THE TRANSMISSION MAP IS THE PAGE. The paper book is a later beat — pack-
 *  weighted three-year continuation, walk-forward, 10 bps, skips recorded —
 *  not a trading product and not a backtest of the event-impact rule. The
 *  equity curve used to open the lede and made a 90% return look like evidence
 *  the impact gate worked.
 */
import { useEffect, useMemo, useState } from 'react'
import { getBacktest, getCalibration, getForward, getJobs, getMarketsStory, getTradeableEdge, lastFailureFor } from '../api'
import { useRegionLabel } from '../regions'
import type { BacktestLedger, CalibrationWalk, ForwardView, JobsStatus, MarketStoryMarket, MarketsStory, TradeableEdge } from '../types'
import { Beat, Chip, Disclosure, Empty, StoryHead } from '../ui'
import { count, courseSentence, marketsLede, pctWord, signedPct, skillSentence, strategyWord } from '../lib/story'
import { Bars, DotWhisker, Drawdown, EquityCurve, SeriesLine, Tiles, pct } from './charts/Kit'

const money = (v: number) => `${v < 0 ? '−' : ''}$${Math.abs(v).toLocaleString('en-US', { maximumFractionDigits: 0 })}`
const bn = (v: number) => `${v < 0 ? '−' : ''}$${(Math.abs(v) / 1e9).toFixed(1)}bn`

const KIND_WORDS: Record<string, string> = {
  sharp_escalation: 'a sharp escalation',
  escalation: 'an escalation',
  'de-escalation': 'a step down',
  stable: 'no departure',
}
const KIND_SHORT: Record<string, string> = {
  sharp_escalation: 'sharp escalation',
  escalation: 'escalation',
  'de-escalation': 'step down',
  stable: 'no departure',
}
const WINDOW_WORDS: Record<string, string> = {
  car_0_1: '2 sessions', car_0_3: '4 sessions', car_0_5: '6 sessions',
  intraday_open_close: 'intraday', monthly: 'the month', annual: 'the year',
}
const windowWord = (w: string) => WINDOW_WORDS[w] ?? w

const num = (v: number | string | null | undefined) => {
  const n = typeof v === 'string' ? Number(v) : v
  return n == null || !Number.isFinite(n) ? null : n
}

export default function MarketsPage({ region, onNavigate }: { region: string; onNavigate: (route: string) => void }) {
  const label = useRegionLabel(region)
  const [story, setStory] = useState<MarketsStory | null | undefined>(undefined)
  const [ledger, setLedger] = useState<BacktestLedger | null | undefined>(undefined)
  const [forward, setForward] = useState<ForwardView | null | undefined>(undefined)
  const [edge, setEdge] = useState<TradeableEdge | null | undefined>(undefined)
  const [walk, setWalk] = useState<CalibrationWalk | null | undefined>(undefined)
  const [jobs, setJobs] = useState<JobsStatus | null>(null)
  const [showAll, setShowAll] = useState(false)
  const [focus, setFocus] = useState<string | null>(null)
  const [cleanRead, setCleanRead] = useState(false)

  useEffect(() => {
    let live = true
    setStory(undefined); setLedger(undefined); setForward(undefined); setEdge(undefined); setWalk(undefined); setFocus(null)
    getMarketsStory(region).then((s) => live && setStory(s))
    getBacktest(region).then((l) => live && setLedger(l))
    getForward(region).then((f) => live && setForward(f))
    getTradeableEdge().then((e) => live && setEdge(e))
    getCalibration(region).then((c) => live && setWalk(c))
    getJobs().then((j) => live && setJobs(j))
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

  // The payload's own caption where it carries one (a pack's `region_label`),
  // the /api/packs caption otherwise. Never the pack key.
  const name = story.region_label ?? label
  const headlined = markets.filter((m) => m.headline)
  const gulf = markets.find((m) => m.trading_calendar === 'gulf' && Object.keys(m.first_mover_share).length)
  const cov = story.coverage?.summary
  const rows = ledger?.rows ?? []
  const summary = ledger?.summary ?? null
  const lede = marketsLede(story, name)
  const measuredTotal = markets.reduce((a, m) => a + m.measured, 0)
  const scored = skillSentence(story.transmission_skill, name)

  // ADVERSARY COURSES ONLY. The forward beat is read as "where the region's
  // risk points", and until 2026-08-17 it was led by Syria–Lebanon and
  // Egypt–Israel withholding from each other — allied pairs whose game is
  // burden-sharing — priced to US natural gas on the Middle East page.
  // The backend already drops allied courses from this beat (and says how
  // many); this is the belt for a payload written before it did.
  const forwardCourses = (story.forward?.courses ?? []).filter(
    (c) => c.family?.family !== 'ally',
  )
  const studyStopped = jobs?.jobs?.find((j) => j.name === 'study')?.last_result?.stopped
  const diskFree = jobs?.memory?.disk_free_gb

  return (
    <div className="reading-column py-8">
      <StoryHead
        kicker={`Markets · ${name.toUpperCase()}`}
        title={lede?.headline ?? `How ${name} moves markets`}
        standfirst={lede?.support}
      />

      <div className="mt-8">
        <Tiles items={[
          {
            label: 'events with a measured effect',
            value: cov ? count(cov.events_measured) : '—',
            sub: cov ? `of ${count(cov.events)} coded in ${name}` : 'coverage pending',
          },
          {
            label: 'market reactions measured',
            value: count(measuredTotal),
            sub: `across ${markets.filter((m) => m.measured).length} of ${markets.length} markets`,
          },
          {
            label: 'measured through',
            value: story.measured_through ? story.measured_through.slice(0, 7) : '—',
            sub: story.game_as_of
              ? `games open at ${story.game_as_of.slice(0, 7)}`
              : story.computed_at ? `written ${story.computed_at.slice(0, 10)}` : undefined,
          },
        ]}         />
      </div>

      {scored && (
        <Beat
          title="How the map has scored"
          aside="Leave-one-out of stored reactions — the archive already measured; this only asks whether those measurements predicted the next one. Not a new event study."
        >
          <p>{scored}</p>
        </Beat>
      )}

      {studyStopped && (
        <p className="figure-note mt-4">
          The event study is paused ({studyStopped}
          {diskFree != null ? ` · ${diskFree.toFixed(2)} GB free on the volume` : ''}).
          Coverage will not grow until the volume has headroom.
        </p>
      )}

      {/* 1 — THE TRANSMISSION MAP */}
      <Beat
        title="What a sharp escalation does to prices"
        major
        aside={`Each market's measured response over the four sessions after a sharp escalation in ${name}. The dot is the typical move; the bar holds the middle half of what actually happened.${cleanRead ? ' Showing the clean read: overlapping windows dropped.' : ''}`}
      >
        {headlined.length ? (
          <>
            <DotWhisker
              rows={markets
                .filter((m) => m.headline || m.measured > 0)
                .map((m) => {
                  const h = (cleanRead ? m.clean_headline : m.headline) ?? m.response?.sharp_escalation?.car_0_3
                  return {
                    key: m.ticker,
                    label: m.name,
                    median: h?.median ?? 0,
                    p25: h?.p25 ?? 0,
                    p75: h?.p75 ?? 0,
                    n: h?.n ?? 0,
                    thin: !m.headline,
                  }
                })}
              onPick={setFocus}
            />
            <p className="figure-note">
              A dot is a <em>median</em> over every measured event of that kind — the direction and
              size the market typically moved beyond what its own estimation window expected. Grey
              rows hold too few measurements to read as a number. Click a market for its full
              response and the events that moved it most.
            </p>
            <div className="toolbar mt-3" style={{ borderTop: 'none' }}>
              <button
                type="button"
                className="btn btn--quiet"
                aria-pressed={cleanRead}
                onClick={() => setCleanRead((v) => !v)}
              >
                {cleanRead ? 'showing the clean read' : 'excluding overlapping windows'}
              </button>
            </div>
          </>
        ) : (
          <Empty>No market holds enough measured effects for a headline yet — the transmission engine is still measuring this region.</Empty>
        )}
        {gulf && (
          <p className="figure-note">
            {gulf.name} trades Sunday–Thursday, so an escalation that lands on a Friday or Saturday
            reaches it before it reaches New York — it printed first{' '}
            {pct(gulf.first_mover_share.sharp_escalation ?? gulf.first_mover_share.escalation ?? 0, 0)}{' '}
            of the time on weekend escalations. The gap between the two calendars is real
            information, not bookkeeping.
          </p>
        )}
      </Beat>

      {/* 2 — ONE MARKET IN FULL */}
      {focused && (
        <Beat
          title={`${focused.name}, in full`}
          aside={`Every kind of event this market has been measured against, at each window the archive holds. ${count(focused.measured)} measured reactions${focused.inception_date ? `, from ${focused.inception_date.slice(0, 4)}` : ''}.`}
        >
          <div className="toolbar mb-3" style={{ borderTop: 'none' }}>
            {markets.map((m) => (
              <button key={m.ticker} className="btn btn--quiet" aria-pressed={m.ticker === focused.ticker} onClick={() => setFocus(m.ticker)}>{m.name}</button>
            ))}
          </div>
          <div className="scroll-x">
            <table className="rule-table" style={{ minWidth: 460 }}>
              <thead>
                <tr>
                  <th className="text-left">after…</th>
                  {focused.windows.map((w) => <th key={w} className="text-right">{windowWord(w)}</th>)}
                </tr>
              </thead>
              <tbody>
                {Object.entries((cleanRead ? focused.clean_response : focused.response) ?? focused.response).map(([kind, byWindow]) => (
                  <tr key={kind}>
                    <td>{KIND_WORDS[kind] ?? kind}</td>
                    {focused.windows.map((w) => {
                      const c = byWindow[w]
                      if (!c) return <td key={w} className="text-right mono" style={{ color: 'var(--muted)' }}>—</td>
                      return (
                        <td key={w} className="text-right mono"
                            title={`middle half ${signedPct(c.p25, 2)} to ${signedPct(c.p75, 2)} · ${pct(c.share_positive, 0)} positive`}
                            style={{ color: c.thin ? 'var(--muted)' : c.median >= 0 ? 'var(--accent)' : 'var(--alert)' }}>
                          {signedPct(c.median, 2)}{' '}
                          <span style={{ color: 'var(--muted)' }}>{c.n}{c.thin ? ' · thin' : ''}</span>
                        </td>
                      )
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="figure-note">
            Each cell is the median move and the number of events behind it; hover for the middle
            half. A cell marked thin has too few measurements to read.
          </p>
          {strategyWord(focused.strategy_signal) && (
            <p className="figure-note mt-2">{strategyWord(focused.strategy_signal)}</p>
          )}
          {focused.biggest_moves.length > 0 && (
            <div className="mt-6">
              <div className="kicker mb-2">The events that moved it most</div>
              <ul className="space-y-1 text-sm">
                {focused.biggest_moves.map((e) => (
                  <li key={e.event_id} className="flex items-baseline gap-3">
                    <span className="mono w-16 shrink-0 text-right" style={{ color: e.abnormal_return >= 0 ? 'var(--accent)' : 'var(--alert)' }}>{signedPct(e.abnormal_return, 1)}</span>
                    <span className="mono w-24 shrink-0" style={{ color: 'var(--muted)' }}>{e.date}</span>
                    <button
                      type="button"
                      className="article-link truncate text-left"
                      onClick={() => onNavigate(
                        `/case/dynamic?event=${encodeURIComponent(e.event_id)}&region=${encodeURIComponent(region)}`,
                      )}
                    >
                      {e.pair || e.name}
                    </button>
                    <Chip label={KIND_SHORT[e.kind] ?? e.kind} tone={e.kind.includes('escalation') && e.kind !== 'de-escalation' ? 'bad' : 'muted'} />
                    {e.first_mover && <Chip label="printed first" tone="ink" />}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </Beat>
      )}

      {/* 3 — WHERE THE GAMES POINT */}
      <Beat
        title="Where the solved games point next"
        aside="The courses the region's games put the most mass on, priced to the measured map above. A direction the archive supports, never a forecast of a price."
      >
        {story.forward && story.forward.direction.length ? (
          <>
            <Bars
              rows={story.forward.direction.slice(0, 8).map((d) => ({
                key: d.market_id, label: d.market_name, value: d.expected_abnormal_return,
                sub: `${count(d.measurements)} moves`,
              }))}
              signed format={(v) => signedPct(v, 2)}
            />
            {forwardCourses.length > 0 && (
              <ul className="mt-5 space-y-2 text-sm">
                {forwardCourses.slice(0, 4).map((c, i) => (
                  <li key={i}>
                    <b>{c.dyad_name}</b> — {courseSentence(c, c.family) ?? c.kind_label ?? c.kind.replace(/_/g, ' ')} at {pct(c.likelihood, 0)}
                    {c.market_implications.length
                      ? <span style={{ color: 'var(--muted)' }}> · historically moved {c.market_implications.slice(0, 3).map((m) => `${m.market_name} ${signedPct(m.median, 2)}`).join(', ')}</span>
                      : <span style={{ color: 'var(--muted)' }}> · no market has been priced to this course</span>}
                  </li>
                ))}
              </ul>
            )}
          </>
        ) : (
          <Empty>{story.forward ? 'No course carries a priced market yet.' : 'No persisted game map for this region yet — the games job solves it on its first pass.'}</Empty>
        )}
      </Beat>

      {/* 4 — THE CURVE AND SOVEREIGN CAPITAL, one beat */}
      {((story.duration && story.duration.dyads.length) ||
        (story.sovereign_capital && story.sovereign_capital.funds.length > 0)) && (
        <Beat
          title="How long the market expects it to last, and where the money sits"
          aside="A crisis the market expects to pass moves the front end of the yield curve; one it expects to last moves the ten-year."
        >
          {story.duration && story.duration.dyads.length > 0 && (
            <>
              <Bars
                rows={story.duration.dyads.map((d) => ({
                  key: d.dyad_id, label: d.dyad_name ?? d.dyad_id, value: d.implied_persistence,
                  sub: `${count(d.n)} events`,
                }))}
                format={(v) => pct(v, 0)}
              />
              <p className="figure-note">
                The share of each pair's yield-curve response that lands at the long end.{' '}
                {count(story.duration.events_with_a_curve_response ?? 0)} events carry both ends.
                Read it to compare pairs against each other, not as a number of quarters.
              </p>
            </>
          )}
          {story.sovereign_capital && story.sovereign_capital.funds.length > 0 && (
            <div className="mt-6">
              <div className="kicker mb-2">Sovereign wealth in US equity, latest filing</div>
              <Bars
                rows={story.sovereign_capital.funds.map((f) => ({
                  key: f.actor_id, label: f.name, value: f.value_usd,
                  sub: `${f.as_of}${f.change_usd != null ? ` · ${bn(f.change_usd)} q/q` : ''}`,
                }))}
                format={bn}
              />
              <p className="figure-note">{story.sovereign_capital.note}</p>
            </div>
          )}
        </Beat>
      )}

      {/* 4b — WHAT IS STILL IN THE PRICE AFTER THE NEWS IS PUBLIC */}
      <Beat
        title="What is still tradeable after the news is public"
        aside="A measurement, not a blotter. GDELT 1.0 arrives a day late, so session 0 — the impact itself — is already in the price. Drift is sessions 2–3. The Gulf/US weekend gap is a published price the follower has not responded to yet."
      >
        {edge === undefined && <Empty>Reading the measured edge…</Empty>}
        {edge === null && <Empty>{lastFailureFor('/api/trading/edge')?.detail ?? 'the edge measurement did not answer'}</Empty>}
        {edge && (
          <>
            {edge.drift.length ? (
              <div className="scroll-x">
                <table className="rule-table" style={{ minWidth: 520 }}>
                  <thead>
                    <tr>
                      <th className="text-left">market</th>
                      <th className="text-right">sample</th>
                      <th className="text-right">drift after an up move</th>
                      <th className="text-right">drift after a down move</th>
                    </tr>
                  </thead>
                  <tbody>
                    {edge.drift.slice(0, 8).map((row) => {
                      const up = num(row.drift_after_up)
                      const down = num(row.drift_after_down)
                      return (
                        <tr key={row.market_ticker}>
                          <td className="mono">{row.market_ticker}</td>
                          <td className="text-right mono">{row.n}</td>
                          <td className="text-right mono">{up == null ? '—' : signedPct(up, 2)}</td>
                          <td className="text-right mono">{down == null ? '—' : signedPct(down, 2)}</td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            ) : <Empty>No ticker has enough paired windows to read a drift.</Empty>}
            {edge.leadlag && (
              <p className="figure-note mt-3">
                {edge.leadlag.leader} leading {edge.leadlag.follower}: correlation{' '}
                {num(edge.leadlag.correlation) == null ? '—' : num(edge.leadlag.correlation)!.toFixed(2)}
                {' '}over {count(edge.leadlag.n)} shared events
                {edge.leadlag.n_leader_moved_first
                  ? ` · the leader printed first on ${count(edge.leadlag.n_leader_moved_first)}`
                  : ''}.
              </p>
            )}
            <p className="figure-note">{edge.method}</p>
          </>
        )}
      </Beat>

      {/* 5 — HOW A FUND FOLLOWING THIS INTEL WOULD HAVE MARKED */}
      <Beat
        title="How a fund following this intel would have done"
        aside="Pack-weighted three-year continuation, walked forward: each quarter the frozen call is translated through the pack's books, entered after the cutoff, marked at the next quarter end, 10 bps round-trip, skips recorded. Not a trading product, and not a backtest of the event-impact rule."
      >
        {ledger === undefined && <Empty>Reading the ledger…</Empty>}
        {ledger === null && <Empty>{lastFailureFor('/api/trading/backtest')?.detail ?? 'no paper model for this region'}</Empty>}
        {ledger && summary && (
          <>
            <Tiles items={[
              { label: 'total return', value: `${summary.total_return >= 0 ? '+' : ''}${pct(summary.total_return, 1)}`, tone: summary.total_return >= 0 ? 'gain' : 'loss', sub: `${money(summary.final_equity_usd)} final equity` },
              { label: 'quarters it called right', value: pct(summary.hit_rate, 0), sub: `${summary.quarters_traded} traded · ${summary.quarters_skipped ?? 0} skipped` },
              { label: 'worst fall from a peak', value: `−${pct(summary.max_drawdown, 1)}`, tone: 'loss', sub: 'peak to trough' },
              { label: 'the standing book', value: forward?.book ? money(forward.book.pnl_usd) : '—', tone: forward?.book ? (forward.book.pnl_usd >= 0 ? 'gain' : 'loss') : 'plain', sub: forward ? `on the call of ${forward.forecast.as_of}` : forward === null ? 'no frozen call' : 'marking…' },
            ]} />
            {(ledger.skip_reasons?.length ?? 0) > 0 && (
              <p className="figure-note mt-3">
                Skipped quarters, grouped:{' '}
                {ledger.skip_reasons!.map((s) => `${s.reason} (${s.quarters})`).join(' · ')}.
              </p>
            )}
            {rows.length > 1 && (
              <figure className="mt-4">
                <EquityCurve rows={rows} notional={summary.notional_usd ?? 1_000_000} width={720} height={140} />
                <figcaption className="figure-note">
                  Equity on $1M of paper positions, {summary.first_quarter?.slice(0, 4)}–{summary.last_quarter?.slice(0, 4)}.
                  A fund following the frozen intel book — not a live blotter.
                </figcaption>
              </figure>
            )}
            {walk && !walk.pending && walk.recent && (
              <p className="figure-note">
                Over the last {walk.recent.years} years this question&rsquo;s skill is{' '}
                {walk.recent.skill == null ? '—' : walk.recent.skill.toFixed(2)}
                {walk.recent.observed_rate != null
                  ? ` against a ${pctWord(walk.recent.observed_rate, 0)} base rate`
                  : ''}
                — the call is nearly vacuous in the modern era, which is why the
                book is not evidence on the harder departure question.
              </p>
            )}
            {(ledger.question || forward?.question) && (
              <p className="figure-note">{ledger.question ?? forward?.question}</p>
            )}
            {rows.length > 1 && (
              <div className="mt-6">
                <div className="kicker mb-1">Fall from the running peak</div>
                <Drawdown rows={ledger.drawdown ?? []} />
              </div>
            )}
            {ledger.attribution && ledger.attribution.length > 0 && (
              <div className="mt-6">
                <div className="kicker mb-1">Where the P&L came from</div>
                <Bars rows={ledger.attribution.map((a) => ({ key: a.ticker, label: a.ticker, value: a.pnl_usd, sub: `${a.quarters} q` }))}
                      signed format={money} />
              </div>
            )}
            <Disclosure label={`the ledger, quarter by quarter · ${rows.length} quarters`}>
              <div className="scroll-x mt-2">
                <table className="mono text-[11px] w-full" style={{ borderCollapse: 'collapse' }}>
                  <thead>
                    <tr style={{ color: 'var(--muted)', borderBottom: '1px solid var(--rule-strong)' }}>
                      <th className="text-left font-normal py-1">quarter</th><th className="text-right font-normal">call</th>
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
          </>
        )}
        {ledger && !summary && <p className="text-sm" style={{ maxWidth: '62ch' }}>{ledger.note}</p>}
      </Beat>

      {/* 6 — THE METHOD */}
      <Beat title="How this was measured" aside="The archive's own account of every number above.">
        <Disclosure label="the method, and the story in the estimator's words">
          {story.explanation.map((p, i) => (
            <p key={i} className="text-sm leading-relaxed mt-3" style={{ maxWidth: '68ch' }}>{p}</p>
          ))}
          <p className="text-xs leading-relaxed mt-4" style={{ color: 'var(--muted)', maxWidth: '72ch' }}>{story.method}</p>
          {ledger?.method && <p className="text-xs leading-relaxed mt-2" style={{ color: 'var(--muted)', maxWidth: '72ch' }}>Paper book: {ledger.method}</p>}
          {story.duration?.calibration && (
            <p className="text-xs leading-relaxed mt-2" style={{ color: 'var(--muted)', maxWidth: '72ch' }}>Duration: {story.duration.calibration}</p>
          )}
          {forward?.pressure && (
            <div className="mt-5">
              <div className="kicker mb-1">Long-horizon structural pressure</div>
              <SeriesLine
                points={Object.entries(forward.pressure.trajectory).sort(([a], [b]) => a.localeCompare(b)).map(([x, y]) => ({ x, y }))}
                height={120} format={(v) => v.toFixed(2)} label="structural pressure"
              />
              <p className="text-xs italic mt-2" style={{ color: 'var(--muted)', maxWidth: '68ch' }}>{forward.pressure.boundary_statement}</p>
            </div>
          )}
        </Disclosure>
      </Beat>

      <p className="page-boundary">
        Every figure on this page is a quantile of measured abnormal returns or a field of a
        persisted solve — measured, mechanical, and out of sample by construction. Not advice.
      </p>
    </div>
  )
}

// Keeps the type import in use for readers of this file's exports.
export type { MarketStoryMarket }
