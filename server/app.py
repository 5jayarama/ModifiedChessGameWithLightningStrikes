"""Flask API for Chess with Lightning Strikes.

All game state, move checking, AI and lightning RNG live on the server. The browser only
sends "move from X to Y" and draws whatever state comes back.

Online games: the creator and the player who joins each get a secret seat token (sent once,
stored hashed). Moves must carry the token of the side to move in the X-Player-Token header.
Clients learn about the opponent's move by long-polling GET /api/games/<id>?wait=1&since=<v>:
the request is held until the game changes, so a move arrives within about 0.15 s.

Long-polling holds a thread per waiting player, so run gunicorn with threads:
    gunicorn -k gthread -w 2 --threads 32 --timeout 60 -b 0.0.0.0:8000 "server.app:create_app()"

Run locally:   flask --app server.app run
Production:    gunicorn -k gthread -w 2 --threads 32 --timeout 60 -b 0.0.0.0:8000 "server.app:create_app()"
Configuration comes from environment variables, see CONFIG_DEFAULTS.
"""
import hashlib
import hmac
import logging
import os
import re
import secrets
import time
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_from_directory
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

from engine import Game, IllegalMove
from engine.game import COLORS, DIFFICULTIES, MODES, STRIKE_FREQUENCIES, TIME_CONTROLS, VARIANTS
from .store import GameStore, StoreFull

ROOT = Path(__file__).resolve().parents[1]
WEB_DIST = ROOT / "web" / "dist"
GAME_ID = re.compile(r"^[A-Za-z0-9_-]{22}$")  # secrets.token_urlsafe(16)
TOKEN = re.compile(r"^[A-Za-z0-9_-]{32}$")    # secrets.token_urlsafe(24), one per online seat
LONG_POLL_STEP = 0.15                          # seconds between version checks while long-polling


def _hash_token(token):
    """Seat tokens are stored hashed, so a leaked database can't be used to move pieces."""
    return hashlib.sha256(token.encode()).hexdigest()

# name -> (environment variable, default, type)
CONFIG_DEFAULTS = {
    "DB_PATH": ("CHESS_DB_PATH", str(ROOT / "data" / "chess.db"), str),
    "MAX_THINK_SECONDS": ("MAX_THINK_SECONDS", 4.0, float),       # hard cap on any AI move
    "LONG_POLL_SECONDS": ("LONG_POLL_SECONDS", 25.0, float),      # how long a waiting request is held
    "GAME_TTL_HOURS": ("GAME_TTL_HOURS", 24.0, float),            # idle games are deleted after this
    "MAX_ACTIVE_GAMES": ("MAX_ACTIVE_GAMES", 5000, int),          # stops the disk filling up
    "RATE_LIMIT_STORAGE_URI": ("RATE_LIMIT_STORAGE_URI", "memory://", str),  # e.g. redis://... with several hosts
    "TRUST_PROXY_HOPS": ("TRUST_PROXY_HOPS", 0, int),             # set to 1 behind one reverse proxy
    "LIMIT_NEW_GAME": ("LIMIT_NEW_GAME", "10 per minute;100 per day", str),
    "LIMIT_MOVE": ("LIMIT_MOVE", "60 per minute", str),
    "LIMIT_JOIN": ("LIMIT_JOIN", "20 per minute", str),
    "LIMIT_READ": ("LIMIT_READ", "120 per minute", str),
}

SECURITY_HEADERS = {
    "Content-Security-Policy": ("default-src 'self'; img-src 'self' data:; style-src 'self'; "
                                "script-src 'self'; connect-src 'self'; object-src 'none'; "
                                "base-uri 'none'; frame-ancestors 'none'; form-action 'none'"),
    "X-Content-Type-Options": "nosniff",
    # Browsers only honor this over HTTPS, so it's harmless on http://localhost
    "Strict-Transport-Security": "max-age=31536000",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}

log = logging.getLogger("chess")


def _load_config(overrides):
    config = {}
    for name, (env, default, cast) in CONFIG_DEFAULTS.items():
        raw = os.environ.get(env)
        config[name] = cast(raw) if raw is not None else default
    config.update(overrides or {})
    return config


def create_app(test_config=None):
    cfg = _load_config(test_config)
    app = Flask(__name__, static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = 1024     # request bodies are tiny; reject anything bigger
    app.config["JSON_SORT_KEYS"] = False
    if cfg["TRUST_PROXY_HOPS"]:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=cfg["TRUST_PROXY_HOPS"], x_proto=cfg["TRUST_PROXY_HOPS"])

    Path(cfg["DB_PATH"]).parent.mkdir(parents=True, exist_ok=True)
    store = GameStore(cfg["DB_PATH"], ttl_seconds=cfg["GAME_TTL_HOURS"] * 3600, max_games=cfg["MAX_ACTIVE_GAMES"])
    limiter = Limiter(get_remote_address, app=app, storage_uri=cfg["RATE_LIMIT_STORAGE_URI"],
                      headers_enabled=True, enabled=cfg.get("RATELIMIT_ENABLED", True))
    app.extensions["game_store"] = store
    app.extensions["rate_limiter"] = limiter  # Flask-Limiter only keeps a weak reference itself

    # ------------------------------------------------------------------ helpers

    def json_body(allowed, required=()):
        data = request.get_json(silent=True)    # None unless Content-Type is application/json
        if not isinstance(data, dict) or set(data) - set(allowed) or set(required) - set(data):
            abort(400, description=f"expected a JSON object with: {', '.join(allowed)}")
        return data

    def load_or_404(game_id):
        if not GAME_ID.match(game_id):
            abort(404, description="game not found")
        found = store.load(game_id)
        if found is None:
            abort(404, description="game not found")
        return found

    def seat_of(seats):
        """Which color the X-Player-Token header belongs to, or None (spectator)."""
        token = request.headers.get("X-Player-Token", "")
        if not seats or not TOKEN.match(token):
            return None
        digest = _hash_token(token)
        for color, stored in seats.items():
            if stored and hmac.compare_digest(stored, digest):
                return color
        return None

    def number_arg(name, default, limit):
        raw = request.args.get(name)
        if raw is None:
            return default
        if not raw.isdigit() or int(raw) > limit:
            abort(400, description=f"{name} must be a whole number")
        return int(raw)

    def payload(game_id, game, version, seats, you=None, token=None):
        both_seated = not seats or all(seats.values())
        if game.mode == "online":
            can_move = both_seated and game.human_can_move(you)
        else:
            can_move = game.human_can_move()
        # ?have=N: the client already holds N review positions, send only the rest
        have = number_arg("have", 0, 100_000)
        # Whose eyes the state is built for (Fog of War hides everything else): your seat online,
        # your color vs the computer, the player to move on a shared device. Spectators: nobody.
        viewer = {"online": you, "ai": game.player_color, "local": game.turn}[game.mode]
        body = {"id": game_id, "version": version,
                "state": game.state(can_move=can_move, have=have, viewer=viewer),
                "you": you, "seats": {c: bool(v) for c, v in seats.items()} if seats else None}
        if token:
            body["token"] = token   # only ever sent once, to the player who owns the seat
        return body

    def pick(data, key, allowed, default):
        value = data.get(key, default)
        if type(value) is not type(default) or value not in allowed:
            abort(400, description=f"{key} must be one of: {', '.join(map(str, allowed))}")
        return value

    def save_or_409(game_id, game, version, seats):
        if not store.save(game_id, game, version, seats):
            abort(409, description="this game was updated by another request; reload it")
        return version + 1

    def flag_check(game_id, game, version, seats):
        """End the game if the side to move ran out of time. Returns the (maybe new) version."""
        if game.check_time() and store.save(game_id, game, version, seats):
            return version + 1
        return version

    # ------------------------------------------------------------------ API

    @app.post("/api/games")
    @limiter.limit(cfg["LIMIT_NEW_GAME"])
    def new_game():
        data = json_body(("mode", "variant", "difficulty", "color", "strike_frequency", "time_control", "takebacks"))
        mode = pick(data, "mode", MODES, "ai")
        variant = pick(data, "variant", VARIANTS, "lightning")
        # type() checks on purpose: bool is a subclass of int, and 2.0 is not a level
        difficulty = pick(data, "difficulty", DIFFICULTIES, 2)
        frequency = pick(data, "strike_frequency", STRIKE_FREQUENCIES, 15)
        color = pick(data, "color", ("white", "black", "random"), "white")
        takebacks = pick(data, "takebacks", (True, False), False)
        time_control = data.get("time_control")
        if time_control is not None and (type(time_control) is not str or time_control not in TIME_CONTROLS):
            abort(400, description=f"time_control must be null or one of: {', '.join(TIME_CONTROLS)}")
        if color == "random":
            color = secrets.choice(COLORS)

        game = Game(mode, variant, difficulty, frequency, player_color=color, time_control=time_control,
                    takebacks=takebacks)
        if mode == "ai":
            game.ai_move(max_seconds=cfg["MAX_THINK_SECONDS"])   # AI opens when the human is black
        seats, token, you = None, None, None
        if mode == "online":
            token = secrets.token_urlsafe(24)
            seats = {"white": None, "black": None}
            seats[color] = _hash_token(token)
            you = color
        try:
            game_id = store.create(game, seats)
        except StoreFull:
            abort(503, description="too many active games, try again later")
        return jsonify(payload(game_id, game, 0, seats, you, token)), 201

    @app.post("/api/games/<game_id>/join")
    @limiter.limit(cfg["LIMIT_JOIN"])
    def join_game(game_id):
        json_body(())
        game, version, seats = load_or_404(game_id)
        if game.mode != "online":
            abort(400, description="only online games can be joined")
        open_seats = [c for c, v in seats.items() if v is None]
        if not open_seats:
            abort(409, description="this game already has two players")
        token = secrets.token_urlsafe(24)
        seats[open_seats[0]] = _hash_token(token)
        if not store.save(game_id, game, version, seats):
            abort(409, description="someone else joined first")
        return jsonify(payload(game_id, game, version + 1, seats, open_seats[0], token))

    @app.get("/api/games/<game_id>")
    @limiter.limit(cfg["LIMIT_READ"])
    def get_game(game_id):
        """Plain read, or a long-poll with ?wait=1&since=<version>: the request is held until
        the game changes (a move, a join, a flag) or LONG_POLL_SECONDS pass (then 204)."""
        since = number_arg("since", None, 10**9)
        wait = request.args.get("wait") == "1" and since is not None
        game, version, seats = load_or_404(game_id)
        version = flag_check(game_id, game, version, seats)
        if wait and version == since:
            deadline = time.monotonic() + cfg["LONG_POLL_SECONDS"]
            if game.clock_view() and game.clock_view()["running"]:
                # wake up when the side to move's flag falls, to end the game on time
                flag_in = game.remaining(game.turn) / 1000 + 0.05
                deadline = min(deadline, time.monotonic() + flag_in)
            while time.monotonic() < deadline and store.version(game_id) == version:
                time.sleep(LONG_POLL_STEP)
            game, version, seats = load_or_404(game_id)
            version = flag_check(game_id, game, version, seats)
            if version == since:
                return "", 204
        return jsonify(payload(game_id, game, version, seats, seat_of(seats)))

    @app.post("/api/games/<game_id>/moves")
    @limiter.limit(cfg["LIMIT_MOVE"])
    def make_move(game_id):
        data = json_body(("from", "to", "promotion"), required=("from", "to"))
        game, version, seats = load_or_404(game_id)
        you = seat_of(seats)
        was_playing = game.status == "playing"

        def reject(message):
            # 422 with the current state, so the client can resync instead of guessing.
            # If the mover's flag fell while checking the move, that result is saved too.
            nonlocal version
            if was_playing and game.status != "playing" and store.save(game_id, game, version, seats):
                version += 1
            return jsonify(dict(payload(game_id, game, version, seats, you), error=message)), 422

        if game.mode == "online":
            if you is None:
                return reject("you are watching this game, not playing it")
            if not all(seats.values()):
                return reject("waiting for your opponent to join")
        promotion = data.get("promotion", "q")
        if not isinstance(promotion, str):
            return reject("promotion must be q, r, b or n")
        try:
            game.move(data["from"], data["to"], promotion=promotion, color=you)
        except IllegalMove as e:
            return reject(str(e))
        game.ai_move(max_seconds=cfg["MAX_THINK_SECONDS"])   # no-op unless it's the AI's turn
        version = save_or_409(game_id, game, version, seats)
        return jsonify(payload(game_id, game, version, seats, you))

    @app.post("/api/games/<game_id>/resign")
    @limiter.limit(cfg["LIMIT_MOVE"])
    def resign(game_id):
        json_body(())
        game, version, seats = load_or_404(game_id)
        you = seat_of(seats)
        if game.mode == "online":
            if you is None:
                abort(403, description="you are watching this game, not playing it")
            color = you
        elif game.mode == "ai":
            color = game.player_color
        else:
            color = game.turn          # same device: the player whose turn it is resigns
        game.check_time()
        try:
            game.resign(color)
        except IllegalMove as e:
            abort(422, description=str(e))
        version = save_or_409(game_id, game, version, seats)
        return jsonify(payload(game_id, game, version, seats, you))

    @app.post("/api/games/<game_id>/undo")
    @limiter.limit(cfg["LIMIT_MOVE"])
    def undo(game_id):
        """vs computer only, and only when takebacks were switched on for the game."""
        json_body(())
        game, version, seats = load_or_404(game_id)
        if game.mode != "ai":
            abort(400, description="undo is only available against the computer")
        game.check_time()
        try:
            game.undo()
        except IllegalMove as e:
            abort(422, description=str(e))
        version = save_or_409(game_id, game, version, seats)
        return jsonify(payload(game_id, game, version, seats))

    @app.get("/healthz")
    @limiter.exempt
    def health():
        return {"ok": True}

    # ------------------------------------------------------------------ frontend

    @app.get("/")
    def index():
        if not (WEB_DIST / "index.html").exists():
            return ("Frontend not built. Run: cd web && npm install && npm run build", 503,
                    {"Content-Type": "text/plain"})
        response = send_from_directory(WEB_DIST, "index.html")
        response.headers["Cache-Control"] = "no-cache"
        return response

    @app.get("/<path:filename>")
    def static_files(filename):
        if filename.startswith("api/"):
            abort(404)
        response = send_from_directory(WEB_DIST, filename)  # refuses paths outside WEB_DIST
        if filename.startswith("assets/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"  # hashed names
        return response

    # ------------------------------------------------------------------ errors and headers

    @app.errorhandler(HTTPException)
    def http_error(e):
        if request.path.startswith("/api/"):
            return jsonify({"error": e.description if e.code != 429 else "too many requests, slow down"}), e.code
        return e

    @app.errorhandler(Exception)
    def server_error(e):
        log.exception("unhandled error")   # full detail in the server log only
        return jsonify({"error": "internal server error"}), 500

    @app.after_request
    def security_headers(response):
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    return app
