// Thin client for the Flask API. The server owns all game state, rules and clocks.

export type Color = "white" | "black";
export type Mode = "ai" | "local" | "online";
export type Variant = "classic" | "lightning" | "fog";
export type Reason =
  | "checkmate" | "resignation" | "timeout" | "king_captured"
  | "stalemate" | "repetition" | "fifty_move" | "insufficient_material" | "timeout_vs_insufficient";

export type GameEvent =
  | { type: "move"; by: Color; from: string; to: string; notation: string }
  | { type: "lightning"; square: string; piece: string | null; turns: number; missed: boolean }
  | { type: "forecast"; square: string; piece: string }
  | { type: "stun_expired"; square: string; piece: string }
  | { type: "undo"; plies: number }
  | { type: "game_over"; status: string; reason: Reason; winner: Color | null };

export interface TimelineEntry {
  board: string[];
  last_move: { from: string; to: string } | null;
  stuns: Record<string, number>;
  check_square: string | null;
  forecast: string | null;
}

export interface Clock {
  time_control: string;
  increment_ms: number;
  white_ms: number;
  black_ms: number;
  running: Color | null;
}

export interface GameState {
  board: string[]; // 8 rows, rank 8 first, one char per square ("P", "k", " ", "?" = hidden by fog)
  turn: Color;
  status: "playing" | "checkmate" | "stalemate" | "draw" | "resigned" | "timeout" | "king_captured";
  reason: Reason | null;
  winner: Color | null;
  in_check: boolean;
  check_square: string | null;
  stuns: Record<string, number>;
  forecast: string | null; // Lightning: the square the next strike will hit
  fog: boolean; // Fog of War in progress: "?" squares and opponent moves are hidden
  legal_moves: Record<string, string[]>; // empty unless this client may move now
  last_move: { from: string; to: string } | null;
  history: string[];
  timeline_start: number; // timeline holds entries from this index on
  timeline: TimelineEntry[];
  events: GameEvent[];
  rounds: number;
  next_strike_in: number | null;
  clock: Clock | null;
  can_undo: boolean;
  fifty_move_count: number | null;
  settings: {
    mode: Mode;
    variant: Variant;
    difficulty: number | null;
    player_color: Color | null;
    strike_frequency: number | null;
    time_control: string | null;
    takebacks: boolean;
  };
  ai: { depth: number; score: number; nodes: number; seconds: number; level: number } | null;
}

export interface GameResponse {
  id: string;
  version: number;
  state: GameState;
  you: Color | null; // online: the seat this client holds, null for spectators
  seats: Record<Color, boolean> | null; // online: which seats are taken
  token?: string; // online: sent once, when you create or join
}

export interface NewGameSettings {
  mode: Mode;
  variant: Variant;
  difficulty?: number;
  color?: Color | "random";
  strike_frequency?: number;
  time_control: string | null;
  takebacks?: boolean;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public response?: GameResponse,
  ) {
    super(message);
  }
}

interface RequestOptions {
  body?: unknown;
  token?: string | null;
  query?: Record<string, string | number>;
  signal?: AbortSignal;
}

/** Returns null for 204 (a long-poll that saw no change). */
async function request(path: string, opts: RequestOptions = {}): Promise<GameResponse | null> {
  const headers: Record<string, string> = {};
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  if (opts.token) headers["X-Player-Token"] = opts.token;
  const query = opts.query ? "?" + new URLSearchParams(Object.entries(opts.query).map(([k, v]) => [k, String(v)])) : "";
  let response: Response;
  try {
    response = await fetch(path + query, {
      method: opts.body === undefined ? "GET" : "POST",
      headers,
      body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
      signal: opts.signal,
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    throw new ApiError(0, "Can't reach the server. Check your connection.");
  }
  if (response.status === 204) return null;
  let data: { error?: string } & Partial<GameResponse> = {};
  try {
    data = await response.json();
  } catch {
    // non-JSON error page from a proxy; fall through with an empty body
  }
  if (!response.ok) {
    const withState = data.state ? (data as GameResponse) : undefined;
    throw new ApiError(response.status, data.error ?? `Server error (${response.status})`, withState);
  }
  return data as GameResponse;
}

const gamePath = (id: string) => `/api/games/${encodeURIComponent(id)}`;
const must = (r: GameResponse | null) => r as GameResponse; // only long-polls can return null

export const createGame = async (settings: NewGameSettings) => must(await request("/api/games", { body: settings }));

export const getGame = async (id: string, token: string | null, have: number) =>
  must(await request(gamePath(id), { token, query: { have } }));

/** Held by the server until the game changes past `since` (or ~25 s pass, then null). */
export const waitForChange = (id: string, token: string | null, have: number, since: number, signal: AbortSignal) =>
  request(gamePath(id), { token, query: { have, since, wait: 1 }, signal });

export const joinGame = async (id: string) => must(await request(`${gamePath(id)}/join`, { body: {} }));

export const sendMove = async (id: string, token: string | null, have: number, from: string, to: string, promotion: string) =>
  must(await request(`${gamePath(id)}/moves`, { body: { from, to, promotion }, token, query: { have } }));

export const resignGame = async (id: string, token: string | null, have: number) =>
  must(await request(`${gamePath(id)}/resign`, { body: {}, token, query: { have } }));

export const undoMove = async (id: string, have: number) =>
  must(await request(`${gamePath(id)}/undo`, { body: {}, query: { have } }));
