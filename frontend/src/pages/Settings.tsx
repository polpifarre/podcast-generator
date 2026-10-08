import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import type { Profile } from '../api.ts'
import {
  generateEpisode,
  getEpisodes,
  getProfile,
  isInProgress,
  saveProfile,
} from '../api.ts'

// Loads the saved profile, then shows the form filled in with it.
export default function Settings({ onGenerate }: { onGenerate: () => void }) {
  const profile = useQuery({ queryKey: ['profile'], queryFn: getProfile })

  if (profile.error) return <p className="error">{profile.error.message}</p>
  if (!profile.data) return <p>Loading…</p>
  return <SettingsForm saved={profile.data} onGenerate={onGenerate} />
}

function SettingsForm({ saved, onGenerate }: { saved: Profile; onGenerate: () => void }) {
  const queryClient = useQueryClient()
  const [form, setForm] = useState(saved) // what's on screen
  const [newInterest, setNewInterest] = useState('')

  // Only one episode at a time: "Generate now" is disabled while one is being made.
  const episodes = useQuery({ queryKey: ['episodes'], queryFn: getEpisodes })
  const busy = episodes.data?.some(isInProgress) ?? false

  const save = useMutation({
    mutationFn: saveProfile,
    onSuccess: (profile) => queryClient.setQueryData(['profile'], profile),
  })

  const generate = useMutation({
    mutationFn: generateEpisode,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['episodes'] })
      onGenerate() // go to the Episodes page to watch it
    },
  })

  // Every change is saved straight away, so nothing is lost when switching tabs.
  function update(changes: Partial<Profile>) {
    const next = { ...form, ...changes }
    setForm(next)
    save.mutate(next)
  }

  function addInterest() {
    const topic = newInterest.trim()
    if (topic && !form.interests.includes(topic)) {
      update({ interests: [...form.interests, topic] })
    }
    setNewInterest('')
  }

  const error = save.error ?? generate.error

  return (
    <section>
      <h2>Your podcast</h2>

      <p className="label">Interests</p>
      <ul className="interests">
        {form.interests.map((topic) => (
          <li key={topic}>
            {topic}
            {/* The podcast needs at least one interest. */}
            <button
              onClick={() => update({ interests: form.interests.filter((t) => t !== topic) })}
              disabled={form.interests.length === 1}
              aria-label={`Remove ${topic}`}
            >
              ×
            </button>
          </li>
        ))}
      </ul>
      {/* A form, so pressing Enter adds the interest too. */}
      <form
        onSubmit={(e) => {
          e.preventDefault()
          addInterest()
        }}
      >
        <input
          value={newInterest}
          onChange={(e) => setNewInterest(e.target.value)}
          placeholder="e.g. AI in healthcare"
        />
        <button type="submit">Add</button>
      </form>

      <label>
        Length
        <select
          value={form.length_minutes}
          onChange={(e) =>
            update({ length_minutes: Number(e.target.value) as Profile['length_minutes'] })
          }
        >
          <option value={5}>5 minutes</option>
          <option value={10}>10 minutes</option>
          <option value={15}>15 minutes</option>
        </select>
      </label>

      <label>
        Tone
        <select
          value={form.tone}
          onChange={(e) => update({ tone: e.target.value as Profile['tone'] })}
        >
          <option value="casual">Casual</option>
          <option value="analytical">Analytical</option>
          <option value="anchor">News anchor</option>
        </select>
      </label>

      <label>
        New episode every day at
        <input
          type="time"
          value={form.schedule_time}
          onChange={(e) => update({ schedule_time: e.target.value })}
        />
      </label>

      <div className="actions">
        {/* Waits for any save in progress, so the episode uses what's on screen. */}
        <button
          className="primary"
          onClick={() => generate.mutate()}
          disabled={busy || save.isPending || generate.isPending}
        >
          {busy ? 'Generating…' : 'Generate now'}
        </button>
      </div>
      {save.isSuccess && <p className="meta">Changes saved.</p>}
      {error && <p className="error">{error.message}</p>}
    </section>
  )
}
