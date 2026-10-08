import { useQuery } from '@tanstack/react-query'
import type { Episode } from '../api.ts'
import { getEpisodes, isInProgress } from '../api.ts'

const HOSTS = { A: 'Alex', B: 'Sam' } // the speaker labels in the script

export default function Episodes() {
  // While an episode is being made, ask the backend again every 3 seconds.
  const episodes = useQuery({
    queryKey: ['episodes'],
    queryFn: getEpisodes,
    refetchInterval: (query) => (query.state.data?.some(isInProgress) ? 3000 : false),
  })

  if (episodes.error) return <p className="error">{episodes.error.message}</p>
  if (!episodes.data) return <p>Loading…</p>

  return (
    <section>
      <h2>Episodes</h2>
      {episodes.data.length === 0 && <p>No episodes yet. Click “Generate now” in Settings.</p>}
      {episodes.data.map((episode) => (
        <EpisodeCard key={episode.id} episode={episode} />
      ))}
    </section>
  )
}

function EpisodeCard({ episode: e }: { episode: Episode }) {
  const date = new Date(e.created_at).toLocaleString()

  if (e.status !== 'done') {
    return (
      <article className="card">
        <p className="meta">{date}</p>
        {e.status === 'failed' ? (
          <p className="error">Failed: {e.error}</p>
        ) : (
          <p>Generating… this takes about 2 minutes.</p>
        )}
      </article>
    )
  }

  // Seconds per stage, added up: how long the episode took to make.
  const seconds = Object.values(e.timings ?? {}).reduce((sum, s) => sum + s, 0)

  return (
    <article className="card">
      <h3>{e.title}</h3>
      <p className="meta">
        {date} · {minutes(e.duration_sec ?? 0)} · made in {Math.round(seconds)} s · cost $
        {e.cost_usd?.toFixed(2)}
      </p>
      <audio controls preload="none" src={`/${e.audio_path}`} />
      <details>
        <summary>Sources ({e.sources?.length ?? 0})</summary>
        <ul>
          {e.sources?.map((s) => (
            <li key={s.url}>
              <a href={s.url} target="_blank" rel="noreferrer">
                {s.title}
              </a>{' '}
              ({s.outlet})
            </li>
          ))}
        </ul>
      </details>
      <details>
        <summary>Transcript</summary>
        {e.script?.map((turn, i) => (
          <p key={i}>
            <strong>{HOSTS[turn.speaker]}:</strong> {turn.text}
          </p>
        ))}
      </details>
    </article>
  )
}

// 293 -> "4:53"
function minutes(seconds: number) {
  const s = Math.round(seconds)
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}
