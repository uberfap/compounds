<script lang="ts">
  import { onMount, tick } from 'svelte'
  import { flip } from 'svelte/animate'
  import { api, ApiError, type ArchiveEntry, type Attempt, type Board, type PlayerStats, type PuzzleStats, type PuzzleStatus, type Session, type Tile } from './api'

  const params = new URLSearchParams(location.search)
  const debugRequested = params.get('debug') === '1'
  const colors = ['🟨', '🟩', '🟦', '🟪']
  const today = new Date()
  const day = [today.getFullYear(), today.getMonth() + 1, today.getDate()]
    .map(part => String(part).padStart(2, '0')).join('-')
  let requested = $state(Number(params.get('p')) || undefined)
  type Theme = 'system' | 'light' | 'dark'
  const initialTheme = document.documentElement.dataset.theme
  let theme = $state<Theme>(initialTheme === 'light' || initialTheme === 'dark' ? initialTheme : 'system')
  const nextTheme: Record<Theme, Theme> = { system: 'light', light: 'dark', dark: 'system' }
  const themeLabel = $derived(`Theme: ${theme}. Switch to ${nextTheme[theme]}.`)
  let help = $state<HTMLDialogElement>()
  let siteInfo = $state<HTMLDialogElement>()
  let infoTopic = $state<'privacy' | 'credits'>('privacy')
  let archivePanel = $state<HTMLDetailsElement>()
  let puzzleStatsPanel = $state<HTMLDetailsElement>()
  let statsDialog = $state<HTMLDialogElement>()
  let personal = $state<PlayerStats>()
  let personalError = $state('')
  let personalLoading = $state(false)
  const resultDay = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', timeZone: 'UTC' })
  let session = $state<Session>()
  let authBusy = $state(false)
  let authMessage = $state('')
  const playerKey = 'compounds-player-v1'
  let stats = $state<PuzzleStats>()
  let statsUnavailable = $state(false)
  let archive = $state<ArchiveEntry[]>()
  let archiveError = $state(false)
  let archiveLoading = $state(false)
  const statusLabels: Record<PuzzleStatus, string> = {
    unplayed: 'Unplayed', in_progress: 'In progress', solved: 'Solved', missed: 'Missed',
  }
  const archiveDate = new Intl.DateTimeFormat(undefined, {
    year: 'numeric', month: 'short', day: 'numeric', timeZone: 'UTC',
  })
  let board = $state<Board>()
  let attempt = $state<Attempt>()
  let order = $state<number[]>([])
  let selected = $state<number[]>([])
  let inspecting = $state(false)
  let busy = $state(false)
  let loadError = $state('')
  let message = $state('')
  let structure = $state<{ name: string; url: string }>()
  let dialog = $state<HTMLDialogElement>()
  let range = $state<{ first: number; last: number }>()
  let shaken = $state(false)
  let revealedTier = $state<number>()
  let toastTimer: ReturnType<typeof setTimeout>

  const visible = $derived(order.filter(id => !attempt?.solved.some(group => group.tiles.includes(id))))
  const tiles = $derived(new Map(board?.tiles.map(tile => [tile.id, tile])))
  const storageKey = (puzzle: Pick<Board, 'number' | 'version'>) => {
    const prefix = session?.account ? `compounds-account-${session.account.storage_key}` : 'compounds-api'
    return `${prefix}-${puzzle.number}-${puzzle.version}`
  }

  function setTheme(value: Theme) {
    theme = value
    if (value === 'system') delete document.documentElement.dataset.theme
    else document.documentElement.dataset.theme = value
    try { localStorage.setItem('compounds-theme', value) }
    catch { /* The setting still applies to this page. */ }
  }

  function recall(puzzle: Pick<Board, 'number' | 'version'>): { id?: string; order?: number[]; selected?: number[]; status?: PuzzleStatus } {
    try { return JSON.parse(localStorage.getItem(storageKey(puzzle)) ?? '{}') ?? {} }
    catch { return {} }
  }

  function save() {
    if (!board || !attempt) return
    try { localStorage.setItem(storageKey(board), JSON.stringify({ id: attempt.id, order, selected, status: progress(attempt) })) }
    catch { /* Browser storage is optional. */ }
  }

  function progress(game: Attempt): PuzzleStatus {
    if (game.over) return game.won ? 'solved' : 'missed'
    return game.mistakes || game.solved.length || game.inspected.length || selected.length
      ? 'in_progress' : 'unplayed'
  }

  function playerId() {
    if (session?.account) return undefined
    try {
      const existing = localStorage.getItem(playerKey)
      if (existing) return existing
      const id = crypto.randomUUID()
      localStorage.setItem(playerKey, id)
      return id
    } catch { return undefined }
  }

  async function initialize() {
    loadError = ''
    try { session = await api<Session>('/auth/session') }
    catch (error) { loadError = errorText(error); return }
    const result = params.get('auth')
    if (result === 'success' && session.account) {
      try {
        localStorage.removeItem(playerKey)
      } catch { /* Guest storage is optional. */ }
    } else if (result === 'expired') {
      authMessage = 'Discord sign-in expired or returned to a different browser origin. Start again from this page.'
    } else if (result === 'cancelled') {
      authMessage = 'Discord sign-in was cancelled.'
    } else if (result === 'provider') {
      authMessage = 'Could not complete sign-in with Discord. Try again.'
    } else if (result) authMessage = 'Discord sign-in did not complete.'
    if (result) {
      const url = new URL(location.href)
      url.searchParams.delete('auth')
      history.replaceState(null, '', url)
    }
    await load(requested)
  }

  async function changeSession() {
    if (authBusy) return
    authBusy = true
    authMessage = ''
    try {
      if (session?.account) {
        await api<void>('/auth/logout', 'POST', { confirm: true })
        location.reload()
      } else {
        const result = await api<{ url: string }>('/auth/discord', 'POST', { guest_id: playerId() ?? null })
        location.assign(result.url)
      }
    } catch (error) {
      authMessage = errorText(error)
      authBusy = false
    }
  }

  async function loadArchive() {
    if (archiveLoading) return
    archiveLoading = true
    archiveError = false
    const player = playerId()
    try {
      const entries = await api<ArchiveEntry[]>('/puzzles/archive', 'POST', { player_id: player })
      archive = entries
    } catch { archiveError = true }
    finally { archiveLoading = false }
  }

  async function loadPersonalStats() {
    if (personalLoading) return
    personalLoading = true
    personalError = ''
    try {
      personal = await api<PlayerStats>('/player/stats', 'POST', { player_id: playerId() })
    } catch (error) { personalError = errorText(error) }
    finally { personalLoading = false }
  }

  function jumpTo(panel?: HTMLDetailsElement) {
    if (!panel) return
    panel.open = true
    panel.querySelector('summary')?.focus()
    panel.scrollIntoView({ block: 'nearest' })
  }

  function showSiteInfo(topic: 'privacy' | 'credits') {
    infoTopic = topic
    siteInfo?.showModal()
  }

  function archiveStatus(entry: ArchiveEntry): PuzzleStatus {
    if (attempt && entry.number === board?.number && entry.version === board.version) return progress(attempt)
    if (entry.status !== 'unplayed') return entry.status
    const saved = recall(entry)
    return saved.status ?? (saved.selected?.length ? 'in_progress' : 'unplayed')
  }

  async function loadStats(puzzle: Board) {
    statsUnavailable = false
    try {
      const result = await api<PuzzleStats>('/puzzles/' + puzzle.number + '/stats')
      if (board?.number === puzzle.number && board.version === puzzle.version) stats = result
    } catch {
      if (board?.number === puzzle.number && board.version === puzzle.version) statsUnavailable = true
    }
  }

  function toast(text: string) {
    message = text
    clearTimeout(toastTimer)
    toastTimer = setTimeout(() => { message = '' }, 1600)
  }

  function errorText(error: unknown) {
    return error instanceof ApiError ? error.message : 'Could not reach the game server. Try again.'
  }

  async function load(number?: number) {
    if (busy) return
    busy = true
    loadError = ''
    try {
      const puzzle = await api<Board>(number ? `/puzzles/${number}` : `/puzzles/daily?day=${day}`)
      const saved = recall(puzzle)
      let resumed: Attempt | undefined
      if (saved.id) {
        try { resumed = await api<Attempt>(`/attempts/${saved.id}`) }
        catch (error) { if (!(error instanceof ApiError && error.status === 404)) throw error }
      }
      attempt = resumed?.puzzle_version === puzzle.version ? resumed
        : await api<Attempt>(`/puzzles/${puzzle.number}/attempts`, 'POST', { version: puzzle.version, player_id: playerId() })
      const ids = puzzle.tiles.map(tile => tile.id)
      const savedOrder = saved.order
      order = Array.isArray(savedOrder) && savedOrder.length === ids.length &&
        new Set(savedOrder).size === ids.length && savedOrder.every(id => ids.includes(id))
        ? savedOrder : ids
      board = puzzle
      const playable = ids.filter(id => !attempt?.solved.some(group => group.tiles.includes(id)))
      selected = !attempt.over && Array.isArray(saved.selected)
        ? [...new Set(saved.selected)].filter(id => playable.includes(id)).slice(0, 4) : []
      stats = undefined
      void loadStats(puzzle)
      inspecting = false
      revealedTier = undefined
      requested = number
      if (number) {
        const url = new URL(location.href)
        url.searchParams.set('p', String(puzzle.number))
        history.replaceState(null, '', url)
      }
      save()
    } catch (error) {
      if (number && error instanceof ApiError && error.status === 404) {
        if (!board) {
          busy = false
          await load()
          return
        }
      }
      if (board) toast(errorText(error))
      else loadError = errorText(error)
    } finally { busy = false }
  }

  onMount(() => {
    void initialize()
    if (debugRequested) void api<{ first: number; last: number }>('/puzzles/range')
      .then(value => { range = value }).catch(() => {})
    return () => {
      clearTimeout(toastTimer)
      closeStructure()
    }
  })

  function shuffle() {
    const next = [...order]
    for (let i = next.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1))
      ;[next[i], next[j]] = [next[j], next[i]]
    }
    order = next
    save()
  }

  function closeStructure() {
    if (structure) URL.revokeObjectURL(structure.url)
    structure = undefined
  }

  async function clickTile(tile: Tile) {
    if (!attempt || busy) return
    if (!inspecting) {
      if (selected.includes(tile.id)) selected = selected.filter(id => id !== tile.id)
      else if (selected.length < 4) selected = [...selected, tile.id]
      save()
      return
    }
    inspecting = false
    if (!tile.has_structure) { toast('No structure available for ' + tile.name); return }
    busy = true
    try {
      const result = await api<{ svg: string; state: Attempt }>(`/attempts/${attempt.id}/structures/${tile.id}`, 'POST')
      attempt = result.state
      save()
      structure = { name: tile.name, url: URL.createObjectURL(new Blob([result.svg], { type: 'image/svg+xml' })) }
      await tick()
      dialog?.showModal()
    } catch (error) { toast(errorText(error)) }
    finally { busy = false }
  }

  async function submit() {
    if (!attempt || busy || selected.length !== 4) return
    busy = true
    try {
      const response = await api<{ result: 'correct' | 'one_away' | 'duplicate' | 'incorrect'; state: Attempt }>(
        `/attempts/${attempt.id}/guesses`, 'POST', { tiles: selected })
      attempt = response.state
      if (response.result === 'correct') {
        revealedTier = attempt.solved.at(-1)?.tier
        selected = []
        inspecting = false
      } else if (response.result === 'duplicate') toast('Already guessed')
      else {
        toast(response.result === 'one_away' ? 'One away…' : 'Not quite')
        shaken = false
        await tick()
        shaken = true
      }
      save()
      if (board) void loadStats(board)
    } catch (error) { toast(errorText(error)) }
    finally { busy = false }
  }

  async function share() {
    if (!attempt || !board) return
    const score = attempt.won ? `${attempt.mistakes}/4 mistakes` : 'X/4'
    const rows = attempt.share_rows.map(row => row.map(tier => colors[tier]).join(''))
    const text = [`Compounds #${board.number} ${score}`, ...rows].join('\n')
    if (navigator.share) {
      try { await navigator.share({ text }); return }
      catch (error) { if (error instanceof DOMException && error.name === 'AbortError') return }
    }
    try { await navigator.clipboard.writeText(text); toast('Copied') }
    catch { toast('Could not copy the result') }
  }
</script>

<main>
  <header class="site-header">
    <div><h1>Compounds</h1>{#if board}<span id="num">#{board.number} · {board.date}</span>{/if}</div>
    <div class="utilities">
      <button class="btn icon-btn" disabled={!session} aria-label="My stats" title="My stats"
        onclick={() => { statsDialog?.showModal(); void loadPersonalStats() }}>
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 19v-5m7 5V5m7 14V9"/></svg>
      </button>
      <button class="btn icon-btn" aria-label="How to play" title="How to play" onclick={() => help?.showModal()}>
        <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M9 9a3 3 0 0 1 6 0c0 2-3 2.5-3 4m0 3h.01"/></svg>
      </button>
      <button class="btn icon-btn" aria-label={themeLabel} title={themeLabel} onclick={() => setTheme(nextTheme[theme])}>
        <svg viewBox="0 0 24 24" aria-hidden="true">
          {#if theme === 'system'}<rect x="3" y="4" width="18" height="13" rx="2"/><path d="M8 21h8m-4-4v4"/>
          {:else if theme === 'light'}<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5"/>
          {:else}<path d="M20.7 15a8.8 8.8 0 0 1-11.7-11.7A9 9 0 1 0 20.7 15Z"/>{/if}
        </svg>
      </button>
      {#if session?.account}
        <button class="btn icon-btn" aria-label="Sign out" title={`Sign out of ${session.account.name}`} disabled={authBusy} onclick={changeSession}>
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M10 4H5v16h5m-1-8h12m-4-4 4 4-4 4"/></svg>
        </button>
      {:else if session?.enabled}
        <button class="btn icon-btn discord" aria-label="Sign in with Discord" title="Sign in with Discord" disabled={authBusy} onclick={changeSession}>
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 6 6 5C4 7 3 11 3 15l4 2 2-2m7-9 2-1c2 2 3 6 3 10l-4 2-2-2M7 14c3 2 7 2 10 0M8 6c2-1 6-1 8 0m-7 5h.01m6 0h.01"/></svg>
        </button>
      {/if}
    </div>
  </header>
  {#if authMessage}<p class="auth-message" role="status">{authMessage}</p>{/if}
  <nav class="puzzle-nav" aria-label="Puzzle pages">
    {#if requested}<a href={location.pathname}>Today’s puzzle</a>{/if}
    <details class="puzzle-archive" bind:this={archivePanel} ontoggle={event => { if (event.currentTarget.open) void loadArchive() }}>
      <summary>Archive</summary>
      {#if archiveError}
        <p role="alert">Could not load the archive.</p>
        <button class="btn" onclick={loadArchive}>Retry</button>
      {:else if archive?.length}
        <ul class="archive-list">
          {#each archive as entry (entry.number)}
            {@const status = archiveStatus(entry)}
            <li><a href={'?p=' + entry.number} aria-current={entry.number === board?.number ? 'page' : undefined}>
              <span><time datetime={entry.date}>{archiveDate.format(new Date(entry.date))}</time> <small>#{entry.number}</small></span>
              <span class="archive-status" class:unplayed={status === 'unplayed'}>{statusLabels[status]}</span>
            </a></li>
          {/each}
        </ul>
      {:else if archiveLoading}
        <p role="status">Loading archive.</p>
      {:else if archive}
        <p>No past puzzles yet.</p>
      {/if}
    </details>
  </nav>
  {#if board && attempt}
    {#if range}
      <nav class="debug-nav" aria-label="Puzzle navigation">
        <button class="btn" aria-label="Previous puzzle" disabled={busy || board.number <= range.first}
          onclick={() => board && load(board.number - 1)}>←</button>
        <input type="range" min={range.first} max={range.last} value={board.number}
            aria-label="Choose puzzle day" disabled={busy}
            onchange={event => load(event.currentTarget.valueAsNumber)}>
        <button class="btn" aria-label="Next puzzle" disabled={busy || board.number === range.last}
          onclick={() => board && load(board.number + 1)}>→</button>
      </nav>
    {/if}
    <div class="intro">
      <p class="how"><strong>Find four groups of four.</strong></p>
      <div class="difficulty">
        <span class="legend" aria-hidden="true"><i class="t0"></i><i class="t1"></i><i class="t2"></i><i class="t3"></i></span>
        Easiest to trickiest
      </div>
    </div>
    {#if attempt.revealed.length}
      <section class="groups" aria-label="Revealed groups">
        {#each attempt.revealed as group (group.tier)}
          <div class="solved t{group.tier}" class:reveal={group.tier === revealedTier}>
            <b>{group.label}</b>
            <div>{group.tiles.map(id => tiles.get(id)?.name).join(', ')}</div>
          </div>
        {/each}
      </section>
    {/if}
    {#if !attempt.over}
      <div class="board" class:inspect-mode={inspecting} class:shake={shaken}
        onanimationend={() => { shaken = false }}>
        {#each visible as id (id)}
          {@const tile = tiles.get(id)!}
          <button class="tile" animate:flip={{ duration: matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 240 }}
            class:sel={selected.includes(id)} aria-pressed={selected.includes(id)}
            disabled={busy} onclick={() => clickTile(tile)}>
            {tile.name}
            {#if attempt.inspected.includes(id)}<span class="svg-pip" aria-hidden="true" title="Structure unlocked"></span>{/if}
          </button>
        {/each}
      </div>
      <div id="lives" aria-live="polite">
        <span>Mistakes left</span>
        <span class="life-dots" aria-hidden="true">
          {#each [1, 2, 3, 4] as life}<i class:spent={life > attempt.mistakes_left}></i>{/each}
        </span>
        <span>{attempt.mistakes_left}/4</span>
      </div>
      <div class="row">
        <button class="btn" disabled={busy} onclick={shuffle}>Shuffle</button>
        <button class="btn" disabled={busy || !selected.length} onclick={() => { selected = []; save() }}>Deselect</button>
        {#if visible.some(id => tiles.get(id)?.has_structure)}
          <button class="btn" class:active={inspecting} aria-pressed={inspecting}
            disabled={busy || (!inspecting && attempt.inspections_left === 0 && !visible.some(id => attempt?.inspected.includes(id)))}
            onclick={() => { inspecting = !inspecting }}>
            {inspecting ? 'Cancel' : 'Structure'} ({attempt.inspections_left})
          </button>
        {/if}
        <button class="btn primary" disabled={busy || selected.length !== 4} onclick={submit}>Submit</button>
      </div>
      {#if inspecting}<p id="msg">Tap a compound to view its skeletal structure.</p>{/if}
    {:else}
      <div class="result">
        <p>{attempt.won ? `Solved with ${attempt.mistakes} mistake${attempt.mistakes === 1 ? '' : 's'}.` : 'Out of mistakes. Here are the groups.'}</p>
        <button class="btn primary" onclick={share}>Share result</button>
      </div>
    {/if}
  {:else if loadError}
    <p role="alert">{loadError}</p>
    <button class="btn" onclick={() => session ? load(requested) : initialize()}>Retry</button>
  {:else}
    <p role="status">Loading puzzle…</p>
  {/if}
  {#if board}
    <details class="puzzle-stats" bind:this={puzzleStatsPanel}>
      <summary>Puzzle statistics and leaderboard</summary>
      {#if stats}
        <dl class="metrics">
          <div><dt>Started</dt><dd>{stats.played}</dd></div>
          <div><dt>Finished</dt><dd>{stats.finished}</dd></div>
          <div><dt>Solved</dt><dd>{stats.won}</dd></div>
        </dl>
        {#if stats.finished}
          <p>{Math.round(100 * stats.won / stats.finished)}% solved ·
            {stats.mean_mistakes?.toFixed(1)} average mistakes ·
            {stats.mean_inspections?.toFixed(1)} average inspections</p>
          <p>Group solve rates among finished attempts</p>
          <ul class="tier-rates">
            {#each stats.solved_by_tier as count, tier}
              <li><span class="t{tier}">{tier + 1}</span> {Math.round(100 * count / stats.finished)}%</li>
            {/each}
          </ul>
        {/if}
        {#if stats.leaderboard.length}
          <h2>Leaderboard</h2>
          <ol class="leaderboard">
            {#each stats.leaderboard as entry (entry.player)}
              <li><strong>{entry.player}{entry.player === attempt?.player ? ' (you)' : ''}</strong>
                <span>{entry.mistakes} mistakes · {entry.inspections} inspections</span></li>
            {/each}
          </ol>
        {:else}
          <p>No ranked solves yet.</p>
        {/if}
      {:else if statsUnavailable}
        <p>Statistics are temporarily unavailable.</p>
      {:else}
        <p>Loading statistics…</p>
      {/if}
    </details>
  {/if}
  <footer class="site-footer">
    <p>Simplified chemical data for gameplay only.</p>
    <nav aria-label="Footer">
      {#if board}<button onclick={() => jumpTo(puzzleStatsPanel)}>Puzzle statistics</button>{/if}
      <button onclick={() => jumpTo(archivePanel)}>Archive</button>
      <button onclick={() => showSiteInfo('privacy')}>Privacy</button>
      <button onclick={() => showSiteInfo('credits')}>License and credits</button>
    </nav>
  </footer>
</main>

<dialog class="help-dialog" bind:this={siteInfo} aria-labelledby="site-info-title">
  <div class="dialog-heading">
    <h2 id="site-info-title">{infoTopic === 'privacy' ? 'Privacy' : 'License and credits'}</h2>
    <form method="dialog"><button class="btn">Close</button></form>
  </div>
  {#if infoTopic === 'privacy'}
    <p>Your browser stores your theme, board selections and shuffle order, and a random guest identifier. The server stores submitted guesses, structure inspections, and results under that identifier or your signed-in account.</p>
    <p>Optional Discord sign-in stores your Discord user ID and display name. A session cookie keeps you signed in. Eligible guest attempts follow your account across devices. Public leaderboards show anonymous labels.</p>
  {:else}
    <p>Compounds’ original code is available under the MIT License. <a href="https://github.com/uberfap/compounds/blob/main/LICENSE">Read the license</a>.</p>
    <p>Substance names and classifications are adapted in part from <a href="https://psychonautwiki.org">PsychonautWiki</a> contributors under <a href="https://creativecommons.org/licenses/by-sa/4.0/">CC BY-SA 4.0</a>. Compounds selects and normalizes records and adds its own categorization.</p>
  {/if}
</dialog>

<dialog class="help-dialog" bind:this={help} aria-labelledby="help-title">
  <div class="dialog-heading">
    <h2 id="help-title">How to play</h2>
    <form method="dialog"><button class="btn">Close</button></form>
  </div>
  <p>Find four groups of four compounds. Connections can come from their names, structures, origins, effects, or targets.</p>
  <ul>
    <li>Select four tiles and press <strong>Submit</strong>. Correct groups move above the board.</li>
    <li><strong>One away</strong> means three of your tiles belong together. An incorrect guess uses one of your four mistakes.</li>
    <li>Difficulty runs from yellow (easiest) through green and blue to purple (trickiest).</li>
    <li>Choose <strong>Structure</strong> and then a tile to inspect its skeletal structure. You can unlock four distinct structures per puzzle and revisit an unlocked structure for free.</li>
  </ul>
  <div class="help-example">
    <h3>Example group</h3>
    <div class="solved t0">
      <b>Noble gases</b>
      <div>Helium, Neon, Argon, Krypton</div>
    </div>
  </div>
</dialog>

<dialog class="help-dialog player-stats" bind:this={statsDialog} aria-labelledby="player-stats-title">
  <div class="dialog-heading">
    <h2 id="player-stats-title">My stats</h2>
    <form method="dialog"><button class="btn">Close</button></form>
  </div>
  {#if personalError}
    <p role="alert">{personalError}</p>
    <button class="btn" onclick={loadPersonalStats}>Retry</button>
  {:else if personalLoading}
    <p role="status">Loading stats.</p>
  {:else if personal}
    <dl class="metrics">
      <div><dt>Played</dt><dd>{personal.played}</dd></div>
      <div><dt>Solved</dt><dd>{personal.solved}</dd></div>
      <div><dt>Win rate</dt><dd>{personal.finished ? Math.round(100 * personal.solved / personal.finished) + '%' : '—'}</dd></div>
      <div><dt>In progress</dt><dd>{personal.in_progress}</dd></div>
      <div><dt>Current streak</dt><dd>{personal.current_streak}</dd></div>
      <div><dt>Best streak</dt><dd>{personal.best_streak}</dd></div>
    </dl>
    <p class="stats-note">Win rate uses finished puzzles. Daily streaks count solves on the release date in UTC.</p>
    <h3>Mistakes on solved puzzles</h3>
    <ul class="mistake-chart">
      {#each personal.mistake_distribution as count, mistakes}
        <li><span>{mistakes}</span><meter min={0} max={Math.max(1, ...personal.mistake_distribution)} value={count} aria-label={`${mistakes} mistakes`}></meter><strong>{count}</strong></li>
      {/each}
    </ul>
    <h3>Recent results</h3>
    {#if personal.recent.length}
      <ul class="recent-results">
        {#each personal.recent as entry (entry.number)}
          {@const label = statusLabels[entry.status]}
          <li>
            <a href={'?p=' + entry.number} class={entry.status}
              title={`#${entry.number} · ${label}`}
              aria-label={`Puzzle ${entry.number}, ${archiveDate.format(new Date(entry.date))}, ${label}`}>
              <time datetime={entry.date}>{resultDay.format(new Date(entry.date))}</time><span>{label}</span>
            </a>
          </li>
        {/each}
      </ul>
    {:else}
      <p>No puzzles published yet.</p>
    {/if}
  {/if}
</dialog>

{#if structure}
  <dialog class="svg-modal" bind:this={dialog} onclose={closeStructure} aria-labelledby="structure-title">
    <div class="svg-modal-head">
      <span id="structure-title">{structure.name}</span>
      <form method="dialog"><button class="svg-modal-close" aria-label="Close">×</button></form>
    </div>
    <div class="svg-canvas"><img src={structure.url} alt={`Skeletal structure of ${structure.name}`}></div>
  </dialog>
{/if}
<div id="toast" class:on={message !== ''} role="status">{message}</div>
