import type { Segmentation } from '../types'

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
}

export default function TimeSlider({ year, onChange, regimes }: Props) {
  const span = YEAR_MAX - YEAR_MIN
  const boundaries = (regimes?.polarity_epoch ?? [])
    .map((r) => Number(r.start.slice(0, 4)))
    .filter((y) => y > YEAR_MIN)

  return (
    <div className="px-6 py-4 border-t" style={{ borderColor: 'var(--line)' }}>
      <div className="flex items-baseline justify-between mb-2">
        <span className="mono text-2xl" style={{ color: 'var(--accent)' }}>
          {year}
        </span>
        <span className="text-sm" style={{ color: 'var(--muted)' }}>
          {year > YEAR_NOW
            ? 'forecast horizon — structural scenario space, not history'
            : regimeLabel(regimes, year)}
        </span>
      </div>
      <div className="relative">
        {boundaries.map((b) => (
          <span
            key={b}
            className="absolute top-0 h-2 w-px"
            style={{ left: `${((b - YEAR_MIN) / span) * 100}%`, background: 'var(--muted)' }}
            title={`regime boundary ${b}`}
          />
        ))}
        <span
          className="absolute top-0 h-2 w-px"
          style={{ left: `${((YEAR_NOW - YEAR_MIN) / span) * 100}%`, background: 'var(--alert)' }}
          title="now"
        />
        <input
          type="range"
          min={YEAR_MIN}
          max={YEAR_MAX}
          value={year}
          onChange={(e) => onChange(Number(e.target.value))}
          className="w-full accent-[--accent]"
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
