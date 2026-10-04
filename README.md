# Chess with Lightning Strikes

A chess game with a twist: every few rounds, lightning strikes the board and stuns a piece for 3 turns. A storm cloud warns you one round ahead. There is also a Fog of War mode where you only see what your pieces can reach. Play in the browser against the computer, with a friend on the same device, or online against someone on another computer. A desktop version built with pygame shares the same engine.

![A lightning strike stuns the black knight on f6](docs/screenshots/lightning-strike.png)

## Features

- **Three game modes:** Classic (standard chess), Lightning, and Fog of War.
- **Three ways to play:** vs the computer, two players on one device, or two players online with an invite link.
- **Computer opponent:** 6 difficulty levels plus a dynamic level that adapts to how you're doing. You can play either color.
- **Clocks:** no clock, or bullet, blitz and rapid presets from 1 | 0 to 30 | 0, with increments. The clock starts after white's first move.
- **Full chess rules:** castling, en passant, promotion to any piece, and every draw rule (stalemate, threefold repetition, 50-move rule, insufficient material, timeout vs insufficient material). The game ends automatically, like on chess.com. Fog of War follows chess.com's Fog of War rules.
- **Premoves:** queue your next move while your opponent thinks. It plays the moment your turn starts, or is dropped if it isn't legal by then.
- **Move history:** click any move, or use the arrow keys, to see that position.
- **Undo:** against the computer only, and only if takebacks are switched on before the game.
- **Resign:** with a confirmation click.
- **Works on phones:** the layout adapts down to a 390 px wide screen.

## Lightning rules

- Lightning strikes after every N full rounds (one white move plus one black move). You pick N, from 5 to 50, before the game.
- **Forecast:** one round before each strike, a storm cloud appears over a random occupied square. Each player then gets one move before it lands.
- The strike hits whatever stands on that square when it lands: the original piece, a piece that moved there, or nothing if the square is empty. Step away to dodge it, or lure an enemy piece onto it.
- A stunned piece can't move or capture, and it doesn't give check or guard squares. It shows a ⚡ badge with the turns left.
- A stun lasts 3 full rounds. Capturing a stunned piece removes the stun with it.
- A stunned rook or king can't castle.
- If a strike leaves a player with no legal moves and their king isn't in check, the game is a stalemate.
- For repetition, stuns and a pending storm cloud are part of the position, because they change what can happen next.
- The computer plays around the cloud: it moves its own pieces off and likes seeing yours stay on.

![A storm cloud over d2: lightning strikes there at the end of the round](docs/screenshots/forecast.png)

## Fog of War rules

- You see your own pieces and only the squares they can move to right now. Everything else is fog.
- There is no check or checkmate. You win by capturing the king, and you may move into danger without warning.
- There is no stalemate trap: if every move loses the king, you still have to make one.
- The 50-move rule and threefold repetition still apply. Running out of time always loses, because any piece might still capture a king that wanders into it.
- **Online:** each player sees only their own view. Spectators see nothing until the game ends, so nobody can watch and pass positions to a player.
- **Same device:** a "pass the device" screen covers the board between turns, and the board turns to face the player to move.
- **vs the computer:** the computer sees the whole board. Playing well with hidden information is a research problem of its own.
- When the game ends, everything is revealed, including the full move list and the review positions.

![Fog of War, white's view: only squares white's pieces can reach are visible](docs/screenshots/fog-of-war.png)

## Quick start

You need Python 3.10+ and Node.js 18+. Run everything from the project folder.

```bash
# 1. Python setup (about 1 minute)
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 2. Build the web frontend (about 30 seconds; repeat after changing files in web/)
cd web
npm install
npm run build
cd ..

# 3. Start the server
python -m flask --app server.app run
```

Open http://127.0.0.1:5000. To try online play on one computer, start an online game and open the invite link in a private window.

If port 5000 is taken (macOS uses it for AirPlay), add `--port 5001` to the last command.

### Desktop version

```bash
pip install pygame
python -m pygame_client.game
```

It uses the bundled maestro pieces. To use your own, put images in `pygame_client/images1/` (`wK.png`, `bQ.png`, and so on); they take priority. The window has a side panel with the clocks, the move list, and Undo / Resign / New game buttons. Arrow keys, Home and End review moves; U undoes; Esc or right-click cancels a premove; N starts a new game once one ends.

The web board also uses maestro. To swap in your own set, put PNGs with the same names in `web/src/pieces/` and rebuild; PNGs take priority over the bundled SVGs.

### Frontend development

```bash
python -m flask --app server.app run      # terminal 1: API on :5000
cd web && npm run dev                     # terminal 2: http://localhost:5173 with hot reload
```

## Deploying

The `Dockerfile` builds the frontend and runs the server with gunicorn as an unprivileged user. Any host that runs Docker images works. Two options:

### Render (free, simplest)

1. Push the project to a GitHub repository.
2. On [render.com](https://render.com), choose **New > Web Service**, connect the repository, and pick the **Free** instance type. Render finds the Dockerfile on its own.
3. Under **Advanced**, set the health check path to `/healthz`. No environment variables are required.
4. Create the service. The first build takes a few minutes, then the game is live at `https://<name>.onrender.com`.

On the free plan the service sleeps after 15 minutes without visitors, and the next visitor waits about a minute while it wakes. Free services have no persistent disk, so games in progress are lost when it sleeps or redeploys. For an always-on site that keeps games, use a paid instance and add a persistent disk mounted at `/data`.

### Fly.io (always on, a few dollars a month)

```bash
fly auth login
# set a unique app name in fly.toml first
fly launch --copy-config --no-deploy
fly volumes create chess_data --size 1 --region ewr
fly deploy
```

`fly.toml` keeps one machine running (no cold starts), mounts the volume at `/data` so games survive restarts and deploys, and forces HTTPS.

### Notes for any host

- Run **one** instance. Games live in a SQLite file, so they can't be shared between machines. Within that machine, gunicorn's workers share the file safely.
- Each waiting online player holds one thread. The defaults (2 workers × 32 threads) handle 64 at once; set `WEB_CONCURRENCY` and `GUNICORN_THREADS` to change that. Use about one worker per CPU core, because the AI's thinking is CPU-bound.
- Rate limits are counted per worker unless `RATE_LIMIT_STORAGE_URI` points at Redis, so with 2 workers a client can get up to twice the configured limit.
- The image sets `TRUST_PROXY_HOPS=1`, which is right for hosts that put one proxy in front of the app (Render and Fly both do), so rate limits see real client addresses.

Without Docker, the equivalent is:

```bash
cd web && npm ci && npm run build && cd ..
gunicorn -k gthread -w 2 --threads 32 --timeout 60 -b 0.0.0.0:8000 "server.app:create_app()"
```

gunicorn doesn't run on Windows; locally, the flask command in Quick start is all you need.

## Project layout

```
engine/          rules, AI and game flow, no UI code
  board.py       board, move generation, make/unmake, incremental evaluation
  search.py      the AI: alpha-beta search with a time budget
  game.py        modes, variants, clocks, draw rules, resign, undo, lightning, fog views, save/load
  tables.py      piece-square tables for the evaluation
server/          Flask API and SQLite game storage
web/             TypeScript frontend (Vite), maestro SVGs in src/pieces/
pygame_client/   desktop version (bundled maestro PNGs in pieces/)
tests/           167 tests: rules, AI, game flow, Fog of War views, API
docs/            screenshots
Dockerfile       production image (frontend build + gunicorn, non-root)
fly.toml         Fly.io configuration
```

## How it works

### The server is the referee

The browser never decides anything. It sends "move from e2 to e4" and draws whatever state comes back. The server checks legality, runs the AI, rolls the lightning (with `random.SystemRandom`, so strikes can't be predicted), and keeps the clocks. A modified client can't place pieces, pick a lightning target, or stop its own clock.

### The engine

The board is a 10×12 array with a border of off-board squares, so pieces stop at the edge without bounds checks. Moves are generated from each piece's movement pattern, then played and taken back on a single board (make/unmake) instead of copying the board at every step. The evaluation (material plus piece-square tables) is updated move by move instead of rescanning the board.

The rules are checked with perft, which counts every legal move sequence to a fixed depth and compares the total with published numbers. The engine matches on five standard positions, including two built to exercise promotions and en passant. It also matches the python-chess library move for move across 300 random games.

### The AI

The AI uses alpha-beta search with iterative deepening: it searches 1 move ahead, then 2, then 3, and stops when its time budget runs out, always keeping the best move from the last finished depth. That gives each difficulty level a guaranteed maximum thinking time. Other parts of the search:

- **Move ordering:** captures and promising moves are searched first, so alpha-beta can skip more of the tree.
- **Quiescence search:** captures are followed to the end, so the AI doesn't miss a recapture just past its search depth.
- **Repetition:** positions already seen in the game count as draws, so the AI avoids repeating when it's ahead and seeks a repetition when it's behind.
- **Clock:** with a clock running, it spends at most a thirtieth of its remaining time on a move, plus most of the increment.

| Position | Search depth | First version | Now |
|---|---|---|---|
| After 1.e4 | 4 half-moves | 7.9 s | 0.085 s |
| Middlegame | 4 half-moves | 21.2 s | 0.063 s |

The speedup comes with identical results: at equal depth the new search returns the same scores as the original in every tested position.

### Real-time updates

Online games use long-polling. Each player's browser keeps one request open (`GET /api/games/<id>?wait=1&since=<version>`). The server holds it and checks the game's version number every 0.15 s, answering as soon as anything changes: a move, a join, a resignation, or a flag falling. After 25 quiet seconds it returns an empty 204 and the browser asks again. A move reaches the opponent in about 0.15 s, so the clock charges players for their own thinking, not for network polling.

I chose long-polling over WebSockets because it works with plain Flask and gunicorn, and the version check goes through the shared SQLite file, so several worker processes stay in sync with no extra services. WebSockets would need an async server plus a message broker such as Redis. They'd be the next step for something like chat.

### Fog of War: the server only sends what you can see

Hiding the opponent in the browser with CSS would be trivial to defeat: open the developer tools and read the data. So the server builds a separate view for each player and replaces every square they can't see with `?` before anything leaves it. The same filter covers every field that could leak information:

- **Move list:** the opponent's moves show as `?`.
- **Last-move highlight:** only the squares you can see.
- **Events:** the opponent's move events are dropped.
- **Move review:** past positions are fogged from your point of view at that moment.
- **Other fields:** the 50-move counter would reveal a hidden capture or pawn move, and the computer's evaluation score would reveal the material balance, so both are withheld.

The tests play random Fog of War games and check every view, square by square, for both players and a spectator. They also check a real online game's API responses.

### Concurrency

Every saved game has a version number. A save only succeeds if the version hasn't changed since the game was loaded (optimistic locking), so two requests racing on the same game can't both apply a move. The second one gets a 409 and the client reloads.

### Online seats

Whoever creates an online game gets a secret seat token. The first person to open the invite link gets the other one, and anyone after that can only watch. The server stores tokens as SHA-256 hashes, compares them in constant time, and checks them on every move, so knowing the game's link isn't enough to move pieces. The browser keeps its token in local storage, so a reload keeps your seat.

### Move history without the bandwidth

Reviewing past moves needs every earlier position. Instead of sending all of them on every update, the client tells the server how many it already has (`?have=n`) and receives only the new ones. A late-game update stays around 1 to 2 KB instead of 30 KB.

## API

| Method | Path | Body | Result |
|---|---|---|---|
| POST | `/api/games` | `{"mode", "variant", "difficulty", "color", "strike_frequency", "time_control", "takebacks"}` | 201, game id and state, plus a seat token for online games |
| POST | `/api/games/<id>/join` | `{}` | online only: takes the open seat and returns its token |
| GET | `/api/games/<id>` | | game state. Add `?wait=1&since=<version>` to long-poll |
| POST | `/api/games/<id>/moves` | `{"from": "e7", "to": "e8", "promotion": "n"}` | state after the move, and the AI's reply in vs-computer games |
| POST | `/api/games/<id>/resign` | `{}` | vs computer: you; same device: the side to move; online: your seat |
| POST | `/api/games/<id>/undo` | `{}` | vs computer with takebacks on only |

- `mode`: `ai` (default), `local` or `online`. `variant`: `classic`, `lightning` (default) or `fog`.
- `difficulty` (0 to 6) and `takebacks` (true/false) apply to vs-computer games. `color` (`white`, `black` or `random`) is the creator's side in vs-computer and online games. `strike_frequency` (5 to 50) applies to Lightning. `time_control` is null or one of `1+0`, `2+1`, `3+0`, `3+2`, `5+0`, `10+0`, `15+10`, `30+0`.
- `promotion`: `q` (default), `r`, `b` or `n`.
- Online requests send the seat token in the `X-Player-Token` header.
- Any request can add `?have=<n>` to receive only the review positions it doesn't have yet.
- Errors are JSON `{"error": "..."}`. A rejected move returns 422 with the current state, so the client can resync.

## Configuration

All settings are environment variables.

| Variable | Default | Purpose |
|---|---|---|
| `CHESS_DB_PATH` | `data/chess.db` | SQLite file holding active games |
| `MAX_THINK_SECONDS` | `4` | Hard cap on any AI move |
| `LONG_POLL_SECONDS` | `25` | How long a waiting online request is held |
| `GAME_TTL_HOURS` | `24` | Idle games are deleted after this |
| `MAX_ACTIVE_GAMES` | `5000` | New games are refused (503) above this |
| `RATE_LIMIT_STORAGE_URI` | `memory://` | Use `redis://...` when running on more than one host |
| `TRUST_PROXY_HOPS` | `0` | Set to `1` behind one reverse proxy |
| `LIMIT_NEW_GAME` | `10 per minute;100 per day` | Per IP |
| `LIMIT_MOVE` | `60 per minute` | Per IP |
| `LIMIT_JOIN` | `20 per minute` | Per IP |
| `LIMIT_READ` | `120 per minute` | Per IP |

## Security

- **Server authority:** all state, rules, randomness and timing live on the server (see [The server is the referee](#the-server-is-the-referee)).
- **Information hiding:** in Fog of War each player receives only their own view, and spectators receive nothing until the end (see [Fog of War: the server only sends what you can see](#fog-of-war-the-server-only-sends-what-you-can-see)).
- **Strict input checks:** exact JSON keys, exact types (`true` is not a difficulty level), square names matching `[a-h][1-8]`, ids and tokens matching their generated format. Bodies over 1 KB are rejected.
- **Unguessable ids:** game ids are 128-bit random values. For vs-computer and same-device games, the id is what lets a reload resume the game. Online games also require a seat token.
- **Limits:** AI thinking time is capped, rate limits apply per IP, and the number of stored games is bounded.
- **Browser hardening:** a strict Content-Security-Policy (`script-src 'self'`, no inline scripts, no `eval`), plus HSTS, `nosniff`, `no-referrer` and `frame-ancestors 'none'`. API responses are `no-store`. The Docker image runs the server as an unprivileged user.
- **Error handling:** stack traces go to the server log only. Clients see a generic error.

## Tests

```bash
pip install pytest chess==1.10.0
pytest                                    # about 30 seconds
```

- **Rules:** perft on five standard positions, plus 300 random games compared with python-chess, checking the incremental evaluation at every position.
- **Variant and modes:** stuns, forecasts (dodged strikes, trapped pieces, the AI stepping off the cloud), castling, en passant, promotions, draw rules, clocks and increments, timeouts, resign, undo.
- **Fog of War:** king capture, no stalemate trap, castling through attacks, the AI taking the king, and random games checked for leaks in every view.
- **AI:** finds mates, scores stalemate as a draw, respects time limits and stuns, avoids repetition when winning.
- **API:** validation, rate limits, concurrent saves, online seats and tokens, long-poll wake-ups on moves and on flags, security headers, path traversal.

## Known limits

- No draw offers, and no undo in multiplayer (by design).
- In Fog of War, the computer sees the whole board.
- Online games without a clock that get abandoned simply expire after `GAME_TTL_HOURS`.
- The AI runs inside the move request, so each worker process handles one AI move at a time.

## Screenshots

| Start screen | Online game with a premove queued |
|---|---|
| ![Start screen](docs/screenshots/start-screen.png) | ![Online game, castling premoved](docs/screenshots/online-premove.png) |

| Reviewing an earlier move | Phone |
|---|---|
| ![Move review](docs/screenshots/move-review.png) | ![Phone layout](docs/screenshots/mobile.png) |

## Credits

Piece set: [maestro](https://github.com/lichess-org/lila/tree/master/public/piece/maestro) by sadsnake1, licensed [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/). That license allows non-commercial use only, with attribution, and changes must be shared under the same license. The app shows the credit under the New game button. If you ever charge money or run ads, replace the pieces with a set that allows commercial use (for example lichess's cburnett, CC BY-SA 3.0).
