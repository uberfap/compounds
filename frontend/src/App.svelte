<script lang="ts">
  import { onMount, tick } from 'svelte'
  import { flip } from 'svelte/animate'
  import { api, ApiError, type ArchiveEntry, type Attempt, type Board, type PuzzleStats, type PuzzleStatus, type Tile } from './api'

  const params = new URLSearchParams(location.search)
  const debugRequested = params.get('debug') === '1'
  const colors = ['🟨', '🟩', '🟦', '🟪']
  const today = new Date()
  const day = [today.getFullYear(), today.getMonth() + 1, today.getDate()]
    .map(part => String(part).padStart(2, '0')).join('-')
  let requested = $state(Number(params.get('p')) || undefined)
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
  const storageKey = (puzzle: Pick<Board, 'number' | 'version'>) => `compounds-api-${puzzle.number}-${puzzle.version}`

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
    try {
      const key = 'compounds-player-v1'
      const existing = localStorage.getItem(key)
      if (existing) return existing
      const id = crypto.randomUUID()
      localStorage.setItem(key, id)
      return id
    } catch { return undefined }
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
    void load(requested)
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
  <header><h1>Compounds</h1>{#if board}<span id="num">#{board.number} · {board.date}</span>{/if}</header>
  <nav class="puzzle-nav" aria-label="Puzzle pages">
    {#if requested}<a href={location.pathname}>Today’s puzzle</a>{/if}
    <details class="puzzle-archive" ontoggle={event => { if (event.currentTarget.open) void loadArchive() }}>
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
      <p class="how"><strong>Find four groups of four.</strong> Links can be origin, effect, target, structure, or the name.</p>
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
    <button class="btn" onclick={() => load(requested)}>Retry</button>
  {:else}
    <p role="status">Loading puzzle…</p>
  {/if}
  {#if board}
    <details class="puzzle-stats">
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
  <footer>Substance names and classes derived in part from <a href="https://psychonautwiki.org">PsychonautWiki</a> (CC BY-SA 4.0).</footer>
</main>

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
