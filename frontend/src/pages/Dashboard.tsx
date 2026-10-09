import { useQuery } from '@tanstack/react-query'
import type { Metrics } from '../api.ts'
import { getMetrics } from '../api.ts'

// The internal dashboard, at /dashboard: for the team, not listeners.
export default function Dashboard() {
  const metrics = useQuery({ queryKey: ['metrics'], queryFn: getMetrics })

  return (
    <>
      <header>
        <h1>Internal dashboard</h1>
      </header>
      <main>
        <p className="meta">
          For the team, not listeners. In production this would be a separate tool behind a
          staff login.
        </p>
        {metrics.error && <p className="error">{metrics.error.message}</p>}
        {metrics.data && <DashboardContent metrics={metrics.data} />}
      </main>
    </>
  )
}

function DashboardContent({ metrics: { mock, real } }: { metrics: Metrics }) {
  return (
    <>
      <h2>
        Usage across all listeners <span className="badge">Mock data for demo</span>
      </h2>
      <div className="stats">
        <Stat label="Listeners" value={mock.users.toLocaleString()} />
        <Stat label="Episodes, last 30 days" value={mock.episodes.toLocaleString()} />
        <Stat label="Average completion" value={percent(mock.completion_rate)} />
        <Stat label="Minutes listened" value={mock.minutes_listened.toLocaleString()} />
      </div>
      <EpisodesPerDay days={mock.episodes_per_day} />
      <CompletionByTopic topics={mock.completion_by_topic} />

      <h2>
        This app <span className="badge real">Real data</span>
      </h2>
      <div className="stats">
        <Stat
          label="Episodes made"
          value={String(real.episodes_done)}
          note={`${real.episodes_failed} failed`}
        />
        <Stat label="Cost per episode" value={dollars(real.avg_cost_usd)} note="average" />
        <Stat
          label="Time to make one"
          value={real.avg_generation_sec === null ? '–' : `${real.avg_generation_sec} s`}
          note="average"
        />
        <Stat
          label="Cost per audio minute"
          value={dollars(real.cost_per_audio_minute_usd)}
          note="OpenAI + ElevenLabs"
        />
      </div>

    </>
  )
}

function Stat({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="stat">
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      {note && <div className="stat-note">{note}</div>}
    </div>
  )
}

// One column per day, on an axis from 0 to a round number above the busiest day.
function EpisodesPerDay({ days }: { days: Metrics['mock']['episodes_per_day'] }) {
  const max = Math.max(...days.map((d) => d.episodes))
  const top = Math.ceil(max / 500) * 500

  return (
    <figure className="chart">
      <figcaption>Episodes generated per day, last 30 days</figcaption>
      <div className="plot">
        {[0, 0.5, 1].map((f) => (
          <div key={f} className="gridline" style={{ bottom: `${f * 100}%` }}>
            <span>{(top * f).toLocaleString()}</span>
          </div>
        ))}
        <div className="columns">
          {days.map((d) => (
            <div
              key={d.date}
              className="column"
              style={{ height: `${(d.episodes / top) * 100}%` }}
              title={`${shortDate(d.date)}: ${d.episodes.toLocaleString()} episodes`}
            />
          ))}
        </div>
      </div>
      <div className="x-axis">
        <span>{shortDate(days[0].date)}</span>
        <span>{shortDate(days[days.length - 1].date)}</span>
      </div>
      <details>
        <summary>Show as table</summary>
        <table>
          <tbody>
            {days.map((d) => (
              <tr key={d.date}>
                <td>{shortDate(d.date)}</td>
                <td>{d.episodes.toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </figure>
  )
}

// One bar per topic, with its value at the tip (so no table is needed).
function CompletionByTopic({ topics }: { topics: Metrics['mock']['completion_by_topic'] }) {
  return (
    <figure className="chart">
      <figcaption>Completion rate by topic: how much of each episode people listen to</figcaption>
      {topics.map((t) => (
        <div key={t.topic} className="hbar-row">
          <span>{t.topic}</span>
          <div className="hbar-track">
            <div className="hbar" style={{ width: `calc((100% - 3rem) * ${t.rate})` }} />
            <span className="hbar-value">{percent(t.rate)}</span>
          </div>
        </div>
      ))}
    </figure>
  )
}

const percent = (rate: number) => `${Math.round(rate * 100)}%`
const dollars = (usd: number | null) => (usd === null ? '–' : `$${usd.toFixed(2)}`)
// "2026-10-08" -> "Oct 8" (noon avoids the date shifting across time zones)
const shortDate = (iso: string) =>
  new Date(`${iso}T12:00`).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
