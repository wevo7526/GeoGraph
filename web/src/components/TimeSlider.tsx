import { useMemo } from 'react'
import type { GraphEvent, Segmentation } from '../types'

// The signature interaction (build-spec section 15): scrub 120 years and
// watch the network reconfigure through regimes. The range runs PAST the
// present into the forecast horizon — the forward segment renders scenario
// space, not history, and the label says which side of "now" you are on.
export const YEAR_MIN = 1905
export const YEAR_NOW = new Date().getFullYear()
export const YEAR_MAX = YEAR_NOW + 20

interface Props {
  year: number
  onChange: (year: number) => void
  regimes: Segmentation | null
  events?: GraphEvent[] | null
}

export default function TimeSlider({ year, onChange, regimes, events }: Props) {
  const span = YEAR_MAX - YEAR_MIN
  const pct = (y: number) => `${((y - YEAR_MIN) / span) * 100}%`
  const boundaries = (regimes?.polarity_epoch ?? [])
    .map((r) => Number(r.start.slice(0, 4)))
    .filter((y) => y > YEAR_MIN)

  // Coverage, drawn: a mark per year the archive actually holds an event, so
  // an empty stretch of slider reads as "not ingested yet", not "history was
  // quiet". The same honesty rule as the explorer's empty states.
  const coveredYears = useMemo(() => {
    const years = new Set<number>()
    for (const e of events ?? []) {
      const y = Number(e.event_time.slice(0, 4))
      if (y >= YEAR_MIN && y <= YEAR_MAX) years.add(y)
    }
    return [...years]
  }, [events])

  return (
    <div className="px-6 py-4 border-t" style={{ borderColor: 'var(--line)' }}>
      <div className="flex items-baseline justify-between mb-2">
        <span className="mono text-2xl" style={{ color: 'var(--accent)' }}>
          {year}
        </span>
        <span className="text-sm text-right" style={{ color: 'var(--muted)' }}>
          {year > YEAR_NOW
            ? 'forecast horizon — structural scenario space, not history'
            : regimeLabel(regimes, year)}
        </span>
      </div>

      <div className="relative" style={{ height: 30 }}>
        {/* coverage strip sits above the track */}
        <div aria-hidden className="absolute inset-x-0 top-0" style={{ height: 5 }}>
          {coveredYears.map((y) => (
            <span
              key={y}
              className="absolute top-0 h-full"
              style={{ left: pct(y), width: 2, background: 'var(--accent)', opacity: 0.55 }}
            />
          ))}
        </div>
        {/* regime boundaries and the now-marker cross the track itself */}
        {boundaries.map((b) => (
          <span
            key={b}
            aria-hidden
            className="absolute w-px"
            style={{ left: pct(b), top: 7, height: 16, background: 'var(--muted)', opacity: 0.6 }}
            title={`regime boundary ${b}`}
          />
        ))}
        <span
          aria-hidden
          className="absolute w-px"
          style={{ left: pct(YEAR_NOW), top: 7, height: 16, background: 'var(--alert)' }}
          title="now"
        />
        <input
          type="range"
          className="timeline absolute inset-x-0"
          style={{ top: 5 }}
          min={YEAR_MIN}
          max={YEAR_MAX}
          value={year}
          onChange={(e) => onChange(Number(e.target.value))}
          aria-label="archive year"
        />
      </div>

      <div className="flex justify-between text-xs mono" style={{ color: 'var(--muted)' }}>
        <span>{YEAR_MIN}</span>
        <span>{YEAR_NOW} · now</span>
        <span>{YEAR_MAX}</span>
      </div>
    </div>
  )
}

function regimeLabel(regimes: Segmentation | null, year: number): string {
  if (!regimes) return ''
  const date = `${year}-06-30`
  const parts = Object.values(regimes)
    .map((entries) => entries.find((r) => r.start <= date && (r.end === null || date < r.end)))
    .filter((r): r is NonNullable<typeof r> => Boolean(r))
    .map((r) => r.name)
  return parts.join(' · ')
}
