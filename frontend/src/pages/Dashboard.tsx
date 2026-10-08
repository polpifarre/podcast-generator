// The internal dashboard, at /dashboard: usage across all users, for the team.
// Phase 5 fills it with mock data and the real cost and time numbers.
export default function Dashboard() {
  return (
    <>
      <header>
        <h1>Internal dashboard</h1>
      </header>
      <main>
        <p className="meta">
          For the team, not listeners: usage across all users (mock data for demo). In
          production this would be a separate tool behind a staff login.
        </p>
        <p>Coming soon.</p>
      </main>
    </>
  )
}
