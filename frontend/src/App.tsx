import { useState } from 'react'
import Dashboard from './pages/Dashboard.tsx'
import Episodes from './pages/Episodes.tsx'
import Settings from './pages/Settings.tsx'

// Two audiences, two addresses: the internal dashboard at /dashboard is for the team
// (usage across all users); everything else is the listener's app.
function App() {
  if (window.location.pathname === '/dashboard') return <Dashboard />
  return <ListenerApp />
}

// The listener's two pages, switched with tabs. No router library: nothing to link to.
function ListenerApp() {
  const [page, setPage] = useState<'settings' | 'episodes'>('settings')

  return (
    <>
      <header>
        <h1>Personal Podcast</h1>
        <nav>
          <button
            className={page === 'settings' ? 'active' : ''}
            onClick={() => setPage('settings')}
          >
            Settings
          </button>
          <button
            className={page === 'episodes' ? 'active' : ''}
            onClick={() => setPage('episodes')}
          >
            Episodes
          </button>
        </nav>
      </header>
      <main>
        {page === 'settings' && <Settings onGenerate={() => setPage('episodes')} />}
        {page === 'episodes' && <Episodes />}
      </main>
    </>
  )
}

export default App
