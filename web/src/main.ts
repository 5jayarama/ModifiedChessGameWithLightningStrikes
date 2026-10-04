import {
  ApiError,
  createGame,
  getGame,
  joinGame,
  resignGame,
  sendMove,
  undoMove,
  waitForChange,
  type Color,
  type GameEvent,
  type GameResponse,
  type GameState,
  type NewGameSettings,
  type TimelineEntry,
} from "./api";

const FILES = "abcdefgh";
const PIECE_NAMES: Record<string, string> = { p: "pawn", n: "knight", b: "bishop", r: "rook", q: "queen", k: "king" };
// Filled symbols for both colors (CSS colors them); U+FE0E keeps phones from drawing emoji.
const GLYPHS: Record<string, string> = { p: "♟", n: "♞", b: "♝", r: "♜", q: "♛", k: "♚" };
const LOW_TIME_MS = 20_000;   // clock turns red, and shows tenths, below this
const HIDDEN = "?";           // Fog of War: a square this player can't see

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
const boardEl = $<HTMLDivElement>("board");
const overlayEl = $<HTMLDivElement>("board-overlay");
const statusEl = $<HTMLDivElement>("status");
const toastsEl = $<HTMLDivElement>("toasts");
const moveListEl = $<HTMLOListElement>("move-list");
const moveScrollEl = $<HTMLDivElement>("move-scroll");
const startEl = $<HTMLDivElement>("start");
const startForm = $<HTMLFormElement>("start-form");
const frequencyEl = $<HTMLSelectElement>("frequency");
const promoEl = $<HTMLDivElement>("promo");
const inviteEl = $<HTMLDivElement>("invite");
const inviteLinkEl = $<HTMLInputElement>("invite-link");
const undoButton = $<HTMLButtonElement>("undo-button");
const resignButton = $<HTMLButtonElement>("resign-button");

const START: GameState["board"] = [
  "rnbqkbnr", "pppppppp", "        ", "        ", "        ", "        ", "PPPPPPPP", "RNBQKBNR",
];

// Piece images in web/src/pieces: the bundled "maestro" SVG set, or your own PNGs with the same
// names (wK.png, bQ.png, ...), which take priority. Resolved at build time, so the browser never
// requests images that don't exist; without any images the board falls back to Unicode symbols.
const PIECE_IMAGES = import.meta.glob<string>("./pieces/*.{png,svg}", { eager: true, query: "?url", import: "default" });
const imageFor = (code: string) => PIECE_IMAGES[`./pieces/${code}.png`] ?? PIECE_IMAGES[`./pieces/${code}.svg`];
const useImages = ["w", "b"].every((c) => "PNBRQK".split("").every((p) => imageFor(c + p)));

let game: GameResponse | null = null;
let timeline: TimelineEntry[] = [];     // every position so far, for move review
let reviewIndex: number | null = null;  // timeline index being reviewed, null = live
let selected: string | null = null;
let busy = false;
let flipped = false;
let premove: { from: string; to: string } | null = null;
let premoveFrom: string | null = null;  // piece picked for a premove, target not chosen yet
let announcedVersion = -1;
let clockSnapshot: { clock: NonNullable<GameState["clock"]>; at: number } | null = null;
let flagRequested = false;
let pollGeneration = 0;
let pollAbort: AbortController | null = null;
let resignArmed: number | undefined;
let revealedAt = -1;          // same-device Fog of War: move count the current player revealed
const squares = new Map<string, HTMLButtonElement>();

const state = () => game?.state ?? null;
const reviewing = () => reviewIndex !== null;

// ------------------------------------------------------------------ seat tokens (online games)

const tokenKey = (id: string) => `chess-seat:${id}`;
const memoryTokens = new Map<string, string>(); // fallback when storage is blocked

function saveToken(id: string, token: string) {
  memoryTokens.set(id, token);
  try {
    localStorage.setItem(tokenKey(id), token);
  } catch {
    // private mode or blocked storage: the seat lasts until the page is closed
  }
}

function loadToken(id: string): string | null {
  try {
    return localStorage.getItem(tokenKey(id)) ?? memoryTokens.get(id) ?? null;
  } catch {
    return memoryTokens.get(id) ?? null;
  }
}

const token = () => (game ? loadToken(game.id) : null);

// ------------------------------------------------------------------ helpers

function pieceAt(board: string[], name: string): string {
  return board[8 - Number(name[1])][FILES.indexOf(name[0])];
}

const colorOf = (ch: string): Color => (ch === ch.toUpperCase() ? "white" : "black");
const other = (c: Color): Color => (c === "white" ? "black" : "white");
const cap = (s: string) => s[0].toUpperCase() + s.slice(1);
const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

function describePiece(ch: string): string {
  if (ch === " ") return "empty";
  if (ch === HIDDEN) return "hidden by fog";
  return `${colorOf(ch)} ${PIECE_NAMES[ch.toLowerCase()]}`;
}

function toast(message: string, kind: "info" | "lightning" | "error" = "info") {
  const el = document.createElement("div");
  el.className = `toast toast-${kind}`;
  el.textContent = message;
  toastsEl.append(el);
  window.setTimeout(() => el.classList.add("leaving"), 3600);
  window.setTimeout(() => el.remove(), 4000);
}

const isPiece = (ch: string) => ch !== " " && ch !== HIDDEN;

/** The color shown at the bottom: yours, the player to move in same-device Fog of War, else white. */
function bottomColor(): Color {
  const s = state();
  if (s?.settings.mode === "local") return s.fog ? s.turn : "white";
  return myColor() ?? "white";
}

/** Same-device Fog of War: cover the board until the next player is looking. */
function curtained(): boolean {
  const s = state();
  return !!s && s.settings.mode === "local" && s.fog && s.status === "playing" && revealedAt !== s.history.length;
}

/** The color this browser plays: yours vs the computer or online; null on a shared device. */
function myColor(): Color | null {
  if (!game) return null;
  const s = game.state.settings;
  if (s.mode === "ai") return s.player_color;
  if (s.mode === "online") return game.you;
  return null;
}

// ------------------------------------------------------------------ board

function buildBoard(flip: boolean) {
  flipped = flip;
  boardEl.replaceChildren();
  squares.clear();
  for (let i = 0; i < 8; i++) {
    for (let j = 0; j < 8; j++) {
      const row = flip ? 7 - i : i;
      const col = flip ? 7 - j : j;
      const name = FILES[col] + String(8 - row);
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = `square ${(row + col) % 2 === 0 ? "dark" : "light"}`;
      btn.dataset.square = name;
      btn.setAttribute("role", "gridcell");
      btn.addEventListener("click", () => void onSquareClick(name));
      if (i === 7) btn.dataset.file = FILES[col];
      if (j === 0) btn.dataset.rank = String(8 - row);
      boardEl.append(btn);
      squares.set(name, btn);
    }
  }
  boardEl.setAttribute("aria-label", `Chess board, ${flip ? "black" : "white"} at the bottom`);
}

function pieceElement(ch: string): HTMLElement {
  const white = colorOf(ch) === "white";
  if (useImages) {
    const img = document.createElement("img");
    img.src = imageFor(`${white ? "w" : "b"}${ch.toUpperCase()}`);
    img.alt = "";
    img.className = "piece-img";
    return img;
  }
  const span = document.createElement("span");
  span.className = `piece ${white ? "white" : "black"}`;
  span.textContent = GLYPHS[ch.toLowerCase()] + "︎";
  span.setAttribute("aria-hidden", "true");
  return span;
}

/** What the board should show: a reviewed position, an optimistic preview, or the live game. */
function shownPosition(preview?: string[]): TimelineEntry {
  const s = state();
  if (reviewIndex !== null && timeline[reviewIndex]) return timeline[reviewIndex];
  return {
    board: preview ?? s?.board ?? START,
    last_move: s?.last_move ?? null,
    stuns: s?.stuns ?? {},
    check_square: preview ? null : s?.check_square ?? null,
    forecast: s?.forecast ?? null,
  };
}

function render(preview?: string[]) {
  const s = state();
  const shouldFlip = bottomColor() === "black";
  if (shouldFlip !== flipped || squares.size === 0) buildBoard(shouldFlip);
  const pos = shownPosition(preview);
  const live = !reviewing();
  const targets = new Set(live && selected && s ? s.legal_moves[selected] ?? [] : []);

  for (const [name, btn] of squares) {
    const ch = pieceAt(pos.board, name);
    btn.replaceChildren();
    for (const kind of ["rank", "file"] as const) {
      const label = btn.dataset[kind];
      if (label) {
        const span = document.createElement("span");
        span.className = `coord ${kind}`;
        span.textContent = label;
        span.setAttribute("aria-hidden", "true");
        btn.append(span);
      }
    }
    if (isPiece(ch)) btn.append(pieceElement(ch));
    if (pos.forecast === name) {
      const cloud = document.createElement("span");
      cloud.className = "cloud";
      cloud.textContent = "\u2601\uFE0E\u26A1\uFE0E";
      cloud.title = "Lightning strikes this square at the end of the round";
      btn.append(cloud);
    }

    const stun = pos.stuns[name];
    if (stun) {
      const badge = document.createElement("span");
      badge.className = "stun";
      badge.textContent = `⚡︎${stun}`;
      badge.title = `Stunned for ${plural(stun, "more turn")}`;
      btn.append(badge);
    }
    if (targets.has(name)) {
      const hint = document.createElement("span");
      hint.className = isPiece(ch) ? "hint capture" : "hint";
      btn.append(hint);
    }
    const isPremove = live && (name === premoveFrom || name === premove?.from || name === premove?.to);

    btn.classList.toggle("selected", live && name === selected);
    btn.classList.toggle("premove", isPremove);
    btn.classList.toggle("last", !!pos.last_move && (pos.last_move.from === name || pos.last_move.to === name));
    btn.classList.toggle("check", pos.check_square === name);
    btn.classList.toggle("stunned", !!stun);
    btn.classList.toggle("fog", ch === HIDDEN);
    btn.classList.toggle("forecast", pos.forecast === name);
    const extra = [stun ? `stunned ${stun}` : "", pos.forecast === name ? "lightning strikes here next" : "",
      targets.has(name) ? "move here" : "", isPremove ? "premove" : ""]
      .filter(Boolean)
      .join(", ");
    btn.setAttribute("aria-label", `${name}, ${describePiece(ch)}${extra ? ", " + extra : ""}`);
  }
  boardEl.classList.toggle("locked", !canPlay() && !premoveAllowed());
  boardEl.classList.toggle("reviewing", reviewing());
  const curtain = $<HTMLButtonElement>("curtain");
  curtain.hidden = !curtained();
  if (curtained()) curtain.textContent = `Pass the device to ${cap(state()!.turn)}. Click when ready.`;
}

/** The server only sends legal moves when this client is allowed to move. */
function canPlay(): boolean {
  const s = state();
  return !!s && !busy && !reviewing() && !curtained() && s.status === "playing" && Object.keys(s.legal_moves).length > 0;
}

/** Premoves: while waiting for the computer or an online opponent. */
function premoveAllowed(): boolean {
  const s = state();
  const me = myColor();
  if (!s || !game || !me || reviewing() || s.status !== "playing") return false;
  if (s.settings.mode === "online" && !(game.seats?.white && game.seats?.black)) return false;
  return s.turn !== me || busy;
}

// ------------------------------------------------------------------ clocks and player bars

function formatClock(ms: number): string {
  const total = Math.max(0, ms);
  const minutes = Math.floor(total / 60_000);
  const seconds = Math.floor((total % 60_000) / 1000);
  if (total < LOW_TIME_MS) return `${minutes}:${String(seconds).padStart(2, "0")}.${Math.floor((total % 1000) / 100)}`;
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

function renderBars() {
  const s = state();
  const bottom: Color = bottomColor();
  const top = other(bottom);
  const mode = s?.settings.mode;
  const names: Record<Color, string> = { white: "White", black: "Black" };
  if (mode === "ai" && s) {
    names[s.settings.player_color!] = "You";
    names[other(s.settings.player_color!)] = s.settings.difficulty === 0 ? "Computer (dynamic)" : `Computer (level ${s.settings.difficulty})`;
  } else if (mode === "online" && game?.you) {
    names[game.you] = `You (${game.you})`;
    names[other(game.you)] = `Opponent (${other(game.you)})`;
  }
  $("name-top").textContent = s ? names[top] : "";
  $("name-bottom").textContent = s ? names[bottom] : "";
  $("bar-top").dataset.color = top;
  $("bar-bottom").dataset.color = bottom;
  tickClocks();
}

function tickClocks() {
  const show = !!clockSnapshot;
  for (const pos of ["top", "bottom"] as const) {
    const el = $(`clock-${pos}`);
    el.hidden = !show;
    if (!clockSnapshot) continue;
    const color = $(`bar-${pos}`).dataset.color as Color;
    const { clock, at } = clockSnapshot;
    let ms = color === "white" ? clock.white_ms : clock.black_ms;
    const running = clock.running === color;
    if (running) ms -= performance.now() - at;
    el.textContent = formatClock(ms);
    el.classList.toggle("running", running);
    el.classList.toggle("low", ms < LOW_TIME_MS);
    if (running && ms <= 0 && !flagRequested) {
      flagRequested = true;
      void refresh(); // the server decides; it ends the game if the flag really fell
    }
  }
}

window.setInterval(tickClocks, 100);

// ------------------------------------------------------------------ panel

function renderPanel() {
  const s = state();
  if (!s || !game) return;
  const lightning = s.settings.variant === "lightning";
  $("facts").hidden = false;
  $("fact-round").textContent = String(s.rounds);
  $("fact-strike-box").hidden = !lightning;
  $("fact-strike").textContent =
    s.status !== "playing" || s.next_strike_in === null ? "-"
      : s.forecast ? `${s.forecast}, end of round` : `in ${plural(s.next_strike_in, "round")}`;
  $("fact-ai-box").hidden = s.settings.mode !== "ai";
  $("fact-ai").textContent = s.ai ? `level ${s.ai.level}, depth ${s.ai.depth}, ${s.ai.seconds.toFixed(2)} s` : "-";

  const modeText = { ai: "against the computer", local: "two players on this device", online: "online" }[s.settings.mode];
  const clockText = s.settings.time_control ? `, ${s.settings.time_control.replace("+", " | ")}` : "";
  const variantText = { classic: "Classic chess", lightning: "Lightning chess", fog: "Fog of War" }[s.settings.variant];
  $("tagline").textContent = `${variantText}, ${modeText}${clockText}.`;

  const waiting = s.settings.mode === "online" && game.seats && !(game.seats.white && game.seats.black);
  inviteEl.hidden = !waiting || game.you === null;
  inviteLinkEl.value = `${location.origin}${location.pathname}#join=${game.id}`;

  // Undo (vs computer, takebacks on) and resign
  const playing = s.status === "playing";
  const canResign = playing && (s.settings.mode !== "online" || (!!game.you && !waiting));
  $("actions").hidden = !canResign && !(s.settings.takebacks && s.settings.mode === "ai");
  undoButton.hidden = !(s.settings.mode === "ai" && s.settings.takebacks);
  undoButton.disabled = busy || !s.can_undo;
  resignButton.hidden = !canResign;
  if (!canResign) disarmResign();

  renderMoveList();
  overlayEl.hidden = playing || reviewing();
  if (!playing) overlayEl.textContent = resultText(s);
  setStatus();
}

function renderMoveList() {
  const s = state();
  if (!s) return;
  if (curtained()) {
    moveListEl.replaceChildren(); // the list shows the next player's own moves: keep it covered too
    return;
  }
  const current = reviewIndex ?? timeline.length - 1; // timeline index = number of moves played
  moveListEl.replaceChildren();
  for (let i = 0; i < s.history.length; i += 2) {
    const li = document.createElement("li");
    for (const ply of [i, i + 1]) {
      if (ply >= s.history.length) break;
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "move";
      btn.textContent = s.history[ply];
      btn.classList.toggle("current", current === ply + 1);
      btn.addEventListener("click", () => setReview(ply + 1));
      li.append(btn);
    }
    moveListEl.append(li);
  }
  const currentBtn = moveListEl.querySelector(".move.current");
  if (currentBtn && reviewing()) currentBtn.scrollIntoView({ block: "nearest" });
  else if (!reviewing()) moveScrollEl.scrollTop = moveScrollEl.scrollHeight;
  const atEnd = !reviewing();
  ($("nav-first") as HTMLButtonElement).disabled = current === 0;
  ($("nav-prev") as HTMLButtonElement).disabled = current === 0;
  ($("nav-next") as HTMLButtonElement).disabled = atEnd;
  ($("nav-last") as HTMLButtonElement).disabled = atEnd;
}

function winnerPhrase(s: GameState): string {
  const me = myColor();
  if (s.settings.mode === "ai") return s.winner === me ? "You win" : "The computer wins";
  if (me) return s.winner === me ? "You win" : "You lose";
  return `${cap(s.winner ?? "")} wins`;
}

function resultText(s: GameState): string {
  switch (s.reason) {
    case "checkmate":
      return `Checkmate. ${winnerPhrase(s)}!`;
    case "king_captured":
      return `King captured. ${winnerPhrase(s)}!`;
    case "resignation": {
      const loser = other(s.winner!);
      const who = loser === myColor() ? "You resigned" : s.settings.mode === "ai" ? "The computer resigned" : `${cap(loser)} resigned`;
      return `${who}. ${winnerPhrase(s)}.`;
    }
    case "timeout":
      return `${winnerPhrase(s)} on time.`;
    case "stalemate":
      return "Draw by stalemate.";
    case "repetition":
      return "Draw by threefold repetition.";
    case "fifty_move":
      return "Draw by the 50-move rule.";
    case "insufficient_material":
      return "Draw by insufficient material.";
    case "timeout_vs_insufficient":
      return "Draw. Time ran out, but the other side can't checkmate.";
    default:
      return "Game over.";
  }
}

function setStatus(text?: string) {
  statusEl.classList.remove("alert");
  if (text) {
    statusEl.textContent = text;
    return;
  }
  const s = state();
  if (!s || !game) return;
  if (curtained()) {
    statusEl.textContent = `${cap(s.turn)} to move. The board is hidden until ${s.turn} is ready.`;
    return;
  }
  if (reviewing()) {
    statusEl.textContent = reviewIndex === 0
      ? "Reviewing the starting position. Press → or ⏭︎ to return."
      : `Reviewing move ${reviewIndex}. Press → or ⏭︎ to return.`;
    return;
  }
  if (s.status !== "playing") {
    statusEl.textContent = resultText(s);
    return;
  }
  const check = s.in_check ? "Check! " : "";
  const mode = s.settings.mode;
  const pre = premove ? " Premove set." : "";
  let msg: string;
  if (mode === "ai") {
    msg = s.turn === s.settings.player_color && !busy ? `${check}Your move.` : `The computer is thinking...${pre}`;
  } else if (mode === "local") {
    msg = `${check}${cap(s.turn)} to move.`;
  } else if (game.seats && !(game.seats.white && game.seats.black)) {
    msg = game.you ? `You play ${game.you}. Waiting for your opponent to join...` : "Waiting for a second player.";
  } else if (!game.you) {
    msg = `Watching. ${cap(s.turn)} to move.`;
  } else {
    msg = s.turn === game.you ? `${check}Your move.` : `Waiting for ${s.turn} to move...${pre}`;
  }
  statusEl.textContent = msg;
  statusEl.classList.toggle("alert", s.in_check);
}

function announce(events: GameEvent[]) {
  for (const e of events) {
    if (e.type === "forecast") {
      toast(`Storm cloud over ${e.square}: lightning strikes there at the end of the next round.`, "lightning");
    } else if (e.type === "lightning" && e.missed) {
      toast(`Lightning strikes ${e.square} and hits nothing.`, "lightning");
    } else if (e.type === "lightning") {
      toast(`Lightning strikes the ${describePiece(e.piece!)} on ${e.square}! Stunned for ${e.turns} turns.`, "lightning");
      const btn = squares.get(e.square);
      btn?.classList.remove("flash");
      void btn?.offsetWidth; // restart the animation
      btn?.classList.add("flash");
    } else if (e.type === "stun_expired") {
      toast(`The ${describePiece(e.piece)} on ${e.square} can move again.`);
    } else if (e.type === "undo") {
      toast(`Took back ${plural(e.plies, "move")}.`);
    }
  }
}

function applyGame(next: GameResponse, announceEvents = true) {
  if (game && next.id === game.id && next.version < game.version) return; // stale long-poll answer
  const sameGame = game?.id === next.id;
  if (next.token) saveToken(next.id, next.token);
  if (!sameGame) {
    timeline = [];
    reviewIndex = null;
    premove = null;
    premoveFrom = null;
  }
  timeline = timeline.slice(0, next.state.timeline_start).concat(next.state.timeline);
  if (reviewIndex !== null && reviewIndex >= timeline.length - 1) reviewIndex = null;
  game = next;
  selected = null;
  clockSnapshot = next.state.clock ? { clock: next.state.clock, at: performance.now() } : null;
  flagRequested = false;
  if (next.state.status !== "playing") {
    premove = null;
    premoveFrom = null;
  }
  history.replaceState(null, "", `#g=${next.id}`);
  render();
  renderBars();
  renderPanel();
  if (announceEvents && sameGame && next.version > announcedVersion) announce(next.state.events);
  announcedVersion = sameGame ? Math.max(announcedVersion, next.version) : next.version;
  syncPolling();
  void tryPremove();
}

async function refresh() {
  if (!game) return;
  try {
    applyGame(await getGame(game.id, token(), timeline.length));
  } catch (err) {
    await handleError(err);
  }
}

// ------------------------------------------------------------------ online: long-polling

function wantsLongPoll(): boolean {
  const s = state();
  return !!game && !!s && s.settings.mode === "online" && s.status === "playing";
}

/** Keep exactly one held request open while an online game is in progress. The server answers
 * the moment anything changes (a move, a join, a resignation, a flag). */
function syncPolling() {
  if (!wantsLongPoll()) {
    pollGeneration++;
    pollAbort?.abort();
    pollAbort = null;
    return;
  }
  if (pollAbort) return; // already waiting
  const generation = ++pollGeneration;
  void (async () => {
    while (generation === pollGeneration && wantsLongPoll()) {
      const id = game!.id;
      pollAbort = new AbortController();
      try {
        const res = await waitForChange(id, loadToken(id), timeline.length, game!.version, pollAbort.signal);
        if (generation !== pollGeneration || game?.id !== id) return;
        if (res) {
          pollAbort = null;
          applyGame(res);
          if (generation !== pollGeneration) return;
        }
      } catch (err) {
        if (err instanceof DOMException && err.name === "AbortError") return;
        await new Promise((r) => window.setTimeout(r, 2000)); // server unreachable: retry gently
      }
    }
    if (generation === pollGeneration) pollAbort = null;
  })();
}

// ------------------------------------------------------------------ input

async function onSquareClick(name: string) {
  const s = state();
  if (!s || !game) return;
  if (reviewing()) {
    setReview(null); // clicking the board while reviewing jumps back to the game
    return;
  }
  if (canPlay()) return void playClick(s, name);
  if (premoveAllowed()) return premoveClick(s, name);
}

async function playClick(s: GameState, name: string) {
  const ch = pieceAt(s.board, name);
  const own = isPiece(ch) && colorOf(ch) === s.turn;

  if (selected && s.legal_moves[selected]?.includes(name)) {
    const from = selected;
    const pawn = pieceAt(s.board, from).toLowerCase() === "p";
    let promotion = "q";
    if (pawn && (name[1] === "8" || name[1] === "1")) {
      const choice = await choosePromotion(s.turn);
      if (!choice) {
        selected = null;
        render();
        return;
      }
      promotion = choice;
    }
    await play(from, name, promotion);
    return;
  }
  if (own && name !== selected) {
    if (s.stuns[name]) {
      toast(`That piece is stunned for ${plural(s.stuns[name], "more turn")}.`);
      selected = null;
    } else if (!s.legal_moves[name]) {
      toast("That piece has no legal moves.");
      selected = null;
    } else {
      selected = name;
    }
  } else {
    selected = null;
  }
  render();
}

/** chess.com style: pick one of your pieces and a target while the opponent thinks. The move is
 * checked and played the moment your turn starts; if it isn't legal by then it is dropped. */
function premoveClick(s: GameState, name: string) {
  const me = myColor()!;
  const ch = pieceAt(s.board, name);
  const own = isPiece(ch) && colorOf(ch) === me;
  if (premove && !premoveFrom) {
    cancelPremove();
    if (!own) return;
  }
  if (premoveFrom === null) {
    if (own) premoveFrom = name;
  } else if (name === premoveFrom) {
    premoveFrom = null;
  } else if (own) {
    premoveFrom = name;
  } else {
    premove = { from: premoveFrom, to: name };
    premoveFrom = null;
  }
  render();
  setStatus();
}

function cancelPremove() {
  if (!premove && !premoveFrom) return;
  premove = null;
  premoveFrom = null;
  render();
  setStatus();
}

async function tryPremove() {
  const s = state();
  if (!premove || !s || !canPlay()) return;
  const { from, to } = premove;
  premove = null;
  if (s.legal_moves[from]?.includes(to)) {
    await play(from, to, "q"); // premoved promotions become queens, like chess.com
  } else {
    render();
    setStatus();
  }
}

function choosePromotion(color: Color): Promise<string | null> {
  return new Promise((resolve) => {
    const choices = $("promo-choices");
    choices.replaceChildren();
    const finish = (value: string | null) => {
      promoEl.hidden = true;
      document.removeEventListener("keydown", onKey);
      resolve(value);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") finish(null);
    };
    for (const p of ["q", "r", "b", "n"]) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "promo-choice";
      btn.setAttribute("aria-label", PIECE_NAMES[p]);
      btn.append(pieceElement(color === "white" ? p.toUpperCase() : p));
      btn.addEventListener("click", () => finish(p));
      choices.append(btn);
    }
    $("promo-cancel").onclick = () => finish(null);
    document.addEventListener("keydown", onKey);
    promoEl.hidden = false;
    (choices.firstElementChild as HTMLButtonElement).focus();
  });
}

async function play(from: string, to: string, promotion: string) {
  const s = state();
  if (!s || !game) return;
  busy = true;
  // Show the move right away while the server checks it (and runs the AI)
  const preview = s.board.map((row) => row.split(""));
  const [fc, fr, tc, tr] = [FILES.indexOf(from[0]), 8 - +from[1], FILES.indexOf(to[0]), 8 - +to[1]];
  const moving = preview[fr][fc];
  const promoted = moving.toLowerCase() === "p" && (tr === 0 || tr === 7);
  preview[tr][tc] = promoted ? (colorOf(moving) === "white" ? promotion.toUpperCase() : promotion) : moving;
  preview[fr][fc] = " ";
  selected = null;
  render(preview.map((r) => r.join("")));
  if (s.settings.mode === "ai") setStatus("The computer is thinking...");
  try {
    const res = await sendMove(game.id, token(), timeline.length, from, to, promotion);
    busy = false;
    applyGame(res);
  } catch (err) {
    busy = false;
    await handleError(err);
  }
}

async function handleError(err: unknown) {
  if (!(err instanceof ApiError)) throw err;
  if (err.status === 429) toast("Slow down a little and try again.", "error");
  else toast(err.message, "error");
  if (err.response) {
    applyGame(err.response, false);
  } else if (game && (err.status === 409 || err.status === 422 || err.status === 0 || err.status >= 500)) {
    try {
      applyGame(await getGame(game.id, token(), timeline.length), false);
    } catch {
      render();
      setStatus("Lost the connection. Reload the page to continue.");
    }
  } else if (err.status === 404) {
    game = null;
    history.replaceState(null, "", location.pathname);
    syncPolling();
    render();
    setStatus("That game no longer exists.");
    openStart();
  } else {
    render();
    setStatus();
  }
}

// ------------------------------------------------------------------ move review

function setReview(index: number | null) {
  if (!game || curtained()) return; // reviewing would show the next player's view early
  const last = timeline.length - 1;
  reviewIndex = index === null || index >= last ? null : Math.max(0, index);
  selected = null;
  premoveFrom = null;
  render();
  renderPanel();
}

const reviewStep = (delta: number) => setReview((reviewIndex ?? timeline.length - 1) + delta);

$("nav-first").addEventListener("click", () => setReview(0));
$("nav-prev").addEventListener("click", () => reviewStep(-1));
$("nav-next").addEventListener("click", () => reviewStep(1));
$("nav-last").addEventListener("click", () => setReview(null));

document.addEventListener("keydown", (e) => {
  if (!startEl.hidden || !promoEl.hidden || !game) return;
  if (e.target instanceof HTMLInputElement || e.target instanceof HTMLSelectElement) return;
  const keys: Record<string, () => void> = {
    ArrowLeft: () => reviewStep(-1),
    ArrowRight: () => reviewStep(1),
    Home: () => setReview(0),
    End: () => setReview(null),
    Escape: cancelPremove,
  };
  if (keys[e.key]) {
    e.preventDefault();
    keys[e.key]();
  }
});

$("curtain").addEventListener("click", () => {
  const s = state();
  if (!s) return;
  revealedAt = s.history.length;
  render();
  renderPanel();
});

boardEl.addEventListener("contextmenu", (e) => {
  if (premove || premoveFrom) {
    e.preventDefault(); // right-click cancels a premove, as on chess.com
    cancelPremove();
  }
});

// ------------------------------------------------------------------ undo and resign

undoButton.addEventListener("click", async () => {
  if (!game || busy) return;
  busy = true;
  cancelPremove();
  try {
    const res = await undoMove(game.id, timeline.length);
    busy = false;
    applyGame(res);
  } catch (err) {
    busy = false;
    await handleError(err);
  }
});

function disarmResign() {
  window.clearTimeout(resignArmed);
  resignArmed = undefined;
  resignButton.classList.remove("armed");
  resignButton.textContent = "Resign";
}

resignButton.addEventListener("click", async () => {
  if (!game || busy) return;
  if (resignArmed === undefined) {
    // two-step: the first click arms the button for 3 seconds
    const s = state()!;
    resignButton.textContent = s.settings.mode === "local" ? `Confirm: ${s.turn} resigns` : "Confirm resign";
    resignButton.classList.add("armed");
    resignArmed = window.setTimeout(disarmResign, 3000);
    return;
  }
  disarmResign();
  busy = true;
  try {
    const res = await resignGame(game.id, token(), timeline.length);
    busy = false;
    applyGame(res);
  } catch (err) {
    busy = false;
    await handleError(err);
  }
});

// ------------------------------------------------------------------ start screen

const radio = (name: string) =>
  (startForm.querySelector(`input[name="${name}"]:checked`) as HTMLInputElement).value;

function syncStartForm() {
  const mode = radio("mode");
  const lightning = radio("variant") === "lightning";
  $("difficulty-group").hidden = mode !== "ai";
  $("takebacks-group").hidden = mode !== "ai";
  $("color-group").hidden = mode === "local";
  $("frequency-group").hidden = !lightning;
  const variant = radio("variant");
  $("variant-hint").textContent = {
    classic: "Standard chess rules.",
    lightning: "Every few rounds lightning stuns a piece for 3 turns. A storm cloud marks the square one round ahead.",
    fog: "You only see squares your pieces can move to. No check: capture the king to win." +
      (mode === "ai" ? " The computer can see through the fog." : mode === "local" ? " The board is covered between turns." : ""),
  }[variant]!;
}

function openStart() {
  $("start-cancel").hidden = !game;
  syncStartForm();
  startEl.hidden = false;
  (startForm.querySelector("input:checked") as HTMLInputElement).focus();
}

function closeStart() {
  startEl.hidden = true;
}

startForm.addEventListener("change", syncStartForm);
$("new-game-button").addEventListener("click", openStart);
$("start-cancel").addEventListener("click", closeStart);
startEl.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && game) closeStart();
});

startForm.addEventListener("submit", async (ev) => {
  ev.preventDefault();
  if (busy) return;
  const mode = radio("mode") as NewGameSettings["mode"];
  const variant = radio("variant") as NewGameSettings["variant"];
  const timeControl = ($("time-control") as HTMLSelectElement).value || null;
  const settings: NewGameSettings = { mode, variant, time_control: timeControl };
  if (mode === "ai") {
    settings.difficulty = Number(($("difficulty") as HTMLSelectElement).value);
    settings.takebacks = radio("takebacks") === "on";
  }
  if (mode !== "local") settings.color = radio("color") as NewGameSettings["color"];
  if (variant === "lightning") settings.strike_frequency = Number(frequencyEl.value);

  busy = true;
  const submit = startForm.querySelector("button[type=submit]") as HTMLButtonElement;
  submit.disabled = true;
  setStatus("Starting...");
  try {
    const res = await createGame(settings);
    busy = false;
    game = null; // a fresh game: no carried-over events or review state
    syncPolling();
    closeStart();
    applyGame(res, false);
    if (mode === "online") toast("Game created. Send the invite link to your opponent.");
  } catch (err) {
    busy = false;
    await handleError(err);
  } finally {
    submit.disabled = false;
  }
});

$("invite-copy").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(inviteLinkEl.value);
    toast("Invite link copied.");
  } catch {
    inviteLinkEl.select();
    toast("Press Ctrl+C (or Cmd+C) to copy the link.");
  }
});

// ------------------------------------------------------------------ boot

async function init() {
  for (let n = 5; n <= 50; n += 5) frequencyEl.append(new Option(`${n} rounds`, String(n), n === 15, n === 15));
  render();
  renderBars();

  const join = /^#join=([A-Za-z0-9_-]{22})$/.exec(location.hash);
  const resume = /^#g=([A-Za-z0-9_-]{22})$/.exec(location.hash);
  const id = join?.[1] ?? resume?.[1];
  if (!id) {
    setStatus("Pick your settings and start a game.");
    openStart();
    return;
  }
  try {
    let current = await getGame(id, loadToken(id), 0);
    const openSeat = !!current.seats && !(current.seats.white && current.seats.black);
    if (join && !current.you && current.seats) {
      if (openSeat) {
        try {
          const res = await joinGame(id);
          applyGame(res, false);
          toast(`You joined as ${res.you}.`);
          return;
        } catch (err) {
          if (!(err instanceof ApiError) || err.status !== 409) throw err;
          current = await getGame(id, loadToken(id), 0); // someone took the seat a moment earlier
        }
      }
      toast("This game already has two players. You're watching it.");
    }
    applyGame(current, false);
  } catch (err) {
    await handleError(err);
  }
}

void init();
