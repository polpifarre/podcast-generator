// Small typed functions that call the backend. In development, Vite forwards these
// URLs to the backend at port 8000 (see vite.config.ts).

export type Profile = {
  interests: string[]
  length_minutes: 5 | 10 | 15
  tone: 'casual' | 'analytical' | 'anchor'
  schedule_time: string // "HH:MM"
}

export type Episode = {
  id: number
  created_at: string
  status: 'pending' | 'working' | 'done' | 'failed'
  title: string | null
  script: { speaker: 'A' | 'B'; text: string }[] | null
  sources: { title: string; outlet: string; url: string }[] | null
  audio_path: string | null // e.g. "media/episode-3.mp3"
  duration_sec: number | null
  cost_usd: number | null
  timings: Record<string, number> | null // seconds per stage
  error: string | null
}

// fetch + JSON. If the backend answers with an error, throw it with its message.
async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const res = await fetch(url, options)
  if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`)
  return res.json()
}

export const getProfile = () => request<Profile>('/profile')

export const saveProfile = (profile: Profile) =>
  request<Profile>('/profile', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(profile),
  })

export const getEpisodes = () => request<Episode[]>('/episodes')

export const generateEpisode = () =>
  request<Episode>('/episodes/generate', { method: 'POST' })

// An episode is being made while it's pending or working.
export const isInProgress = (episode: Episode) =>
  episode.status === 'pending' || episode.status === 'working'
