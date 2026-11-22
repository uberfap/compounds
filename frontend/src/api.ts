export interface Tile { id: number; name: string; has_structure: boolean; has_synonyms: boolean }
export interface Board { number: number; date: string; version: string; tiles: Tile[] }
export type PuzzleStatus = 'unplayed' | 'in_progress' | 'solved' | 'missed'
export interface Session { enabled: boolean; account: { name: string; storage_key: string } | null }
export interface ArchiveEntry {
  number: number
  date: string
  version: string
  status: PuzzleStatus
}
export interface PlayerStats {
  played: number
  finished: number
  solved: number
  missed: number
  in_progress: number
  current_streak: number
  best_streak: number
  mistake_distribution: number[]
  recent: Omit<ArchiveEntry, 'version'>[]
}
export interface Group { tier: number; label: string; tiles: number[] }
export interface Attempt {
  id: string
  player: string
  puzzle_version: string
  mistakes: number
  mistakes_left: number
  inspections_left: number
  inspected: number[]
  synonym_inspected: number[]
  solved: Group[]
  revealed: Group[]
  over: boolean
  won: boolean
  share_rows: number[][]
}

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message) }
}

export async function api<T>(path: string, method = 'GET', body?: object): Promise<T> {
  const base = (import.meta.env.VITE_API_ORIGIN ?? '').replace(/\/$/, '')
  const response = await fetch(base + '/api' + path, {
    method,
    cache: 'no-store',
    ...(body && { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
  })
  if (!response.ok) {
    const error = await response.json().catch(() => null)
    throw new ApiError(response.status, typeof error?.detail === 'string' ? error.detail : 'Request failed. Try again.')
  }
  return response.status === 204 ? undefined as T : response.json() as Promise<T>
}

export interface PuzzleStats {
  played: number
  finished: number
  won: number
  mean_mistakes: number | null
  mean_inspections: number | null
  solved_by_tier: number[]
  leaderboard: { player: string; mistakes: number; inspections: number }[]
}
