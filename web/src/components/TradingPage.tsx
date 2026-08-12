/** Placeholder — replaced by the walk-forward backtest build (epic 5). */
export default function TradingPage({
  region,
}: {
  region: string
  onNavigate: (route: string) => void
}) {
  return (
    <div className="max-w-3xl mx-auto px-6 py-10">
      <p className="mono text-xs uppercase tracking-widest" style={{ color: 'var(--muted)' }}>
        Trading · {region.toUpperCase()}
      </p>
      <p className="mt-6 text-sm" style={{ color: 'var(--muted)' }}>
        The paper-money model is being assembled.
      </p>
    </div>
  )
}
