/** Placeholder — replaced by the reasoning surface build (epic 6). */
export default function ReasoningPage({
  region,
}: {
  region: string
  onNavigate: (route: string) => void
}) {
  return (
    <div className="max-w-3xl mx-auto px-6 py-10">
      <p className="mono text-xs uppercase tracking-widest" style={{ color: 'var(--muted)' }}>
        Reasoning · {region.toUpperCase()}
      </p>
      <p className="mt-6 text-sm" style={{ color: 'var(--muted)' }}>
        The reasoning surface is being assembled.
      </p>
    </div>
  )
}
