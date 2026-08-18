import { useRegionLabel } from '../regions'
import { Beat, StoryHead } from '../ui'

/** Situation is the working home — what just happened, what it means for
 *  markets, what happens next. This shell holds the slot until the briefing
 *  is composed from the live wire, the region's solved games, and packed
 *  market headlines (those endpoints already exist; this page does not invent
 *  a number while they are still unread). Until that compose, the three desks
 *  are one click away rather than a fabricated lede. */
export default function SituationPage({
  region,
  onNavigate,
}: {
  region: string
  onNavigate: (route: string) => void
}) {
  const regionLabel = useRegionLabel(region)

  return (
    <article className="reading-column">
      <StoryHead
        kicker="Situation"
        title={regionLabel}
        standfirst="What broke from routine, what packed markets have done, and what the region's games point to — composed here from the desks below, not guessed in this column."
      />

      <Beat
        title="The desks"
        aside="The briefing will sit on this page. Until it does, open the source rather than read a placeholder as a call."
      >
        <p>
          <button type="button" className="article-link" onClick={() => onNavigate('/wire')}>
            Wire
          </button>
          {' '}— what just came in, and what broke from that pair's baseline.
        </p>
        <p className="mt-3">
          <button type="button" className="article-link" onClick={() => onNavigate('/markets')}>
            Markets
          </button>
          {' '}— what this region's geopolitics has done to prices.
        </p>
        <p className="mt-3">
          <button type="button" className="article-link" onClick={() => onNavigate('/games')}>
            Game theory
          </button>
          {' '}— the region's map of what happens next.
        </p>
      </Beat>
    </article>
  )
}
