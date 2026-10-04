"""Game flow: modes, variants, turns, clock, draws, resign, undo, lightning and game over.

Modes:     "ai"     one human against the computer (human picks a color)
           "local"  two humans on one device
           "online" two humans on different devices (seats are handled by the server)
Variants:  "classic"   normal chess
           "lightning" every few rounds lightning stuns a piece for 3 turns. A storm cloud marks
                       the target square one round ahead; the strike hits whatever stands there.
           "fog"       Fog of War: you only see squares your pieces can move to. There is no
                       check; you win by capturing the king (chess.com rules).

Game endings (same as chess.com, which applies the FIDE rules automatically):
    checkmate, resignation, timeout                                  -> a win
    stalemate, threefold repetition, 50-move rule,
    insufficient material, timeout vs insufficient material          -> a draw

Clients (Flask, pygame) only talk to Game. All rules, timing and randomness live here, so a
client can never decide where lightning strikes, what counts as legal, or how much time is left.
"""
import random
import time

from .board import (BISHOP, BLACK, CASTLE, EMPTY, EN_PASSANT, KING, KNIGHT, PAWN, PIECE_TO_CHAR, PROMO_PIECE,
                    PROMOTION, QUEEN, ROOK, SQUARES, WHITE, ZOBRIST_EP, Board, parse_square, square_name)
from . import fog_ai
from .search import Searcher, pick_level

MODES = ("ai", "local", "online")
VARIANTS = ("classic", "lightning", "fog")
COLORS = ("white", "black")
DIFFICULTIES = range(0, 7)          # 0 = dynamic, 1..6 = fixed
STRIKE_FREQUENCIES = range(5, 51)   # full rounds between lightning strikes
PROMOTION_PIECES = {"q": QUEEN, "r": ROOK, "b": BISHOP, "n": KNIGHT}
# chess.com-style presets: "minutes+increment seconds"
TIME_CONTROLS = {"1+0": (60, 0), "2+1": (120, 1), "3+0": (180, 0), "3+2": (180, 2), "5+0": (300, 0),
                 "10+0": (600, 0), "15+10": (900, 10), "30+0": (1800, 0)}

SIDE = {"white": WHITE, "black": BLACK}
COLOR = {WHITE: "white", BLACK: "black"}
OTHER = {"white": "black", "black": "white"}

WINS = {"checkmate", "resignation", "timeout", "king_captured"}
STATUSES = ("playing", "checkmate", "stalemate", "draw", "resigned", "timeout", "king_captured")
HIDDEN = "?"                        # a square the viewer can't see in Fog of War
# What a stun costs, used to steer the AI away from a forecast strike (about a third of a piece)
STUN_COST = {PAWN: 40, KNIGHT: 110, BISHOP: 110, ROOK: 170, QUEEN: 300, KING: 250}
DRAWS = {"stalemate", "repetition", "fifty_move", "insufficient_material", "timeout_vs_insufficient"}


class IllegalMove(ValueError):
    pass


class Game:
    def __init__(self, mode="ai", variant="lightning", difficulty=2, strike_frequency=15,
                 player_color="white", time_control=None, takebacks=False, rng=None, clock=time.time):
        if mode not in MODES:
            raise ValueError("mode must be ai, local or online")
        if variant not in VARIANTS:
            raise ValueError("variant must be classic, lightning or fog")
        if mode == "ai":
            if difficulty not in DIFFICULTIES:
                raise ValueError("difficulty must be 0-6")
            if player_color not in COLORS:
                raise ValueError("player_color must be white or black")
        if variant == "lightning" and strike_frequency not in STRIKE_FREQUENCIES:
            raise ValueError("strike_frequency must be 5-50")
        if time_control is not None and time_control not in TIME_CONTROLS:
            raise ValueError("unknown time control")
        self.board = Board()
        self.mode = mode
        self.variant = variant
        self.difficulty = difficulty if mode == "ai" else None
        self.player_color = player_color if mode == "ai" else None
        self.strike_frequency = strike_frequency if variant == "lightning" else None
        self.time_control = time_control
        self.takebacks = bool(takebacks) and mode == "ai"   # undo is a vs-computer option only
        self.rounds = 0              # full rounds played (one white move + one black move)
        self.status = "playing"      # playing | checkmate | stalemate | draw | resigned | timeout
        self.reason = None           # how it ended: one of WINS or DRAWS
        self.winner = None           # "white" | "black" | None
        self.history = []            # move notation strings
        self.last_move = None        # {"from": "e2", "to": "e4"}
        self.events = []             # what happened since the last human action
        self.ai_info = None
        self.halfmove = 0            # half-moves since the last capture or pawn move (50-move rule)
        self.snapshots = []          # state before each move: powers undo and move review
        self.forecast = None         # Lightning: the square the next strike will hit
        self.positions = [self._position_key()]   # repetition keys, one per position reached
        self.hashes = [self.board.hash]          # same positions as plain hashes, for the AI
        seconds = TIME_CONTROLS[time_control][0] if time_control else 0
        # Clock times in milliseconds. running_since is when the side to move started thinking.
        self.clock = {"white": seconds * 1000, "black": seconds * 1000, "running_since": None}
        self._now = clock
        # SystemRandom: lightning can't be predicted from earlier strikes
        self.rng = rng or random.SystemRandom()

    # ------------------------------------------------------------------ whose turn

    @property
    def turn(self):
        return COLOR[self.board.side]

    @property
    def ai_color(self):
        if self.mode != "ai":
            return None
        return OTHER[self.player_color]

    def is_ai_turn(self):
        return self.status == "playing" and self.turn == self.ai_color

    def human_can_move(self, color=None):
        """Can a human move right now? `color` is the side the client controls (online only)."""
        if self.status != "playing":
            return False
        if self.mode == "ai":
            return self.turn == self.player_color
        if self.mode == "local":
            return True
        return color == self.turn

    # ------------------------------------------------------------------ moves

    def move(self, from_name, to_name, promotion="q", color=None):
        """Play a human move. `color` is required online (the seat making the move).
        Raises IllegalMove. Does not trigger the AI."""
        self.check_time()
        if self.status != "playing":
            raise IllegalMove("time ran out" if self.reason in ("timeout", "timeout_vs_insufficient")
                              else "the game is over")
        if not self.human_can_move(color):
            raise IllegalMove("not your turn")
        if promotion not in PROMOTION_PIECES:
            raise IllegalMove("promotion must be q, r, b or n")
        try:
            frm, to = parse_square(from_name), parse_square(to_name)
        except ValueError as e:
            raise IllegalMove(str(e)) from None
        b = self.board
        if frm in b.stuns and b.sq[frm] * b.side > 0:
            n = b.stuns[frm]
            raise IllegalMove(f"that piece is stunned for {n} more turn{'s' if n != 1 else ''}")
        piece = PROMOTION_PIECES[promotion]
        move = next((m for m in self._moves() if m[0] == frm and m[1] == to
                     and (m[2] < PROMOTION or PROMO_PIECE[m[2]] == piece)), None)
        if move is None:
            raise IllegalMove("illegal move")
        self.events = []
        self._apply(move)

    def choose_ai_move(self, max_seconds=None, board=None):
        """Pick the AI's move without changing the game. Returns (move, info) or (None, None).

        For a background thread, pass board=game.board.copy() made on the main thread, so the
        search never reads a board that the UI is touching."""
        if not self.is_ai_turn():
            return None, None
        board = board or self.board
        ai_side = SIDE[self.ai_color]
        if self.variant == "fog":
            # No check in Fog of War: take the king whenever it's offered
            enemy_king = board.king[-ai_side]
            for m in board.pseudo_moves(fog=True):
                if m[1] == enemy_king:
                    return m, {"depth": 0, "score": 0, "nodes": 0, "seconds": 0.0, "level": self.difficulty}
        level = pick_level(self.difficulty, board, ai_side)
        if self.time_control:
            # Don't lose on time: spend at most 1/30 of the remaining clock plus most of the increment
            budget = self.remaining(self.ai_color) / 1000 / 30 + TIME_CONTROLS[self.time_control][1] * 0.8
            max_seconds = max(0.05, min(max_seconds or budget, budget))
        if self.variant == "fog":
            # Fair play: the AI only uses what it has seen, never the real hidden squares
            boards = [Board.from_dict(s["board"]) for s in self.snapshots] + [board]
            move, info = fog_ai.choose_move(boards, ai_side, level, max_seconds, self.rng)
            return move, dict(info, level=level)
        searcher = Searcher.for_level(level, max_seconds=max_seconds)
        move, info = searcher.best_move(board, history=set(self.hashes), root_bonus=self._forecast_bonus())
        return move, dict(info, level=level)

    def _forecast_bonus(self):
        """Steer the AI around a forecast strike: its own piece standing on the cloud costs about a
        third of the piece, an enemy piece there is a small gain (the enemy can still step away)."""
        target = self.forecast
        if target is None:
            return None

        def bonus(b):
            p = b.sq[target]
            if p == EMPTY:
                return 0
            cost = STUN_COST[abs(p)]
            mover = -b.side          # the AI just moved, so the side to move is the opponent
            return -cost if p * mover > 0 else cost // 2
        return bonus

    def apply_ai_move(self, move, info):
        self.check_time()
        if move is None or not self.is_ai_turn():
            return
        self.ai_info = info
        self._apply(move)

    def ai_move(self, max_seconds=None):
        """Let the AI move if it is its turn."""
        move, info = self.choose_ai_move(max_seconds)
        self.apply_ai_move(move, info)

    # ------------------------------------------------------------------ resign / undo

    def resign(self, color):
        if self.status != "playing":
            raise IllegalMove("the game is over")
        if color not in COLORS:
            raise IllegalMove("unknown color")
        self.events = []
        self._finish("resigned", "resignation", OTHER[color])

    def undo_plies(self):
        """How many half-moves Undo would take back (0 = not allowed).

        Only against the computer, and only if takebacks were switched on before the game:
        your last move and the computer's reply (or just your move if it ended the game)."""
        if not self.takebacks or self.reason in ("resignation", "timeout", "timeout_vs_insufficient"):
            return 0  # like chess.com: no takebacks after a resignation or a flag
        if self.is_ai_turn():
            return 0
        n = len(self.history)
        plies = 2 if self.turn == self.player_color else 1
        # Never undo the computer's opening move on its own (when the human plays black)
        keep = 1 if self.player_color == "black" else 0
        return plies if n - plies >= keep else 0

    def undo(self):
        plies = self.undo_plies()
        if not plies:
            raise IllegalMove("takebacks are off" if not self.takebacks else "nothing to take back")
        self._undo(plies)
        return plies

    def _undo(self, plies):
        for _ in range(plies):
            snap = self.snapshots.pop()
            self.board = Board.from_dict(snap["board"])
            self.rounds = snap["rounds"]
            self.last_move = snap["last_move"]
            self.halfmove = snap["halfmove"]
            self.forecast = snap["forecast"]
            self.history.pop()
            self.positions.pop()
            self.hashes.pop()
        self.status, self.reason, self.winner = "playing", None, None
        self.events = [{"type": "undo", "plies": plies}]
        # Times are not given back (chess.com doesn't either); the side to move's clock restarts
        self.clock["running_since"] = self._now() if self.time_control and self.history else None

    # ------------------------------------------------------------------ clock

    def remaining(self, color, now=None):
        """Milliseconds left for `color`, counting the time the side to move has used so far."""
        ms = self.clock[color]
        since = self.clock["running_since"]
        if since is not None and color == self.turn and self.status == "playing":
            ms -= ((now or self._now()) - since) * 1000
        return max(0, int(ms))

    def check_time(self, now=None):
        """End the game if the side to move has run out of time. Returns True if it just ended."""
        if not self.time_control or self.status != "playing" or self.clock["running_since"] is None:
            return False
        if self.remaining(self.turn, now) > 0:
            return False
        loser = self.turn
        self.clock[loser] = 0
        winner = OTHER[loser]
        if self.variant == "fog" or self._can_mate(winner):
            self._finish("timeout", "timeout", winner)
        else:
            self._finish("draw", "timeout_vs_insufficient", None)
        return True

    def _charge_clock(self, now):
        """Before a move: take the thinking time off the mover's clock and add the increment."""
        since = self.clock["running_since"]
        if not self.time_control or since is None:
            return
        mover = self.turn
        self.clock[mover] = max(0, self.clock[mover] - (now - since) * 1000)
        self.clock[mover] += TIME_CONTROLS[self.time_control][1] * 1000

    # ------------------------------------------------------------------ internals

    def _apply(self, move):
        now = self._now()
        self._charge_clock(now)
        self.clock["running_since"] = None   # restarted for the next player below
        b = self.board
        side = b.side
        frm, to, flag = move
        self.snapshots.append({"board": b.to_dict(), "rounds": self.rounds, "last_move": self.last_move,
                               "halfmove": self.halfmove, "forecast": self.forecast})
        stuns_before = b.stuns
        captured_sq = to + 10 * side if flag == EN_PASSANT else to
        resets_fifty = abs(b.sq[frm]) == PAWN or b.sq[to] != EMPTY or flag == EN_PASSANT
        self._play(move)
        self.halfmove = 0 if resets_fifty else self.halfmove + 1
        if side == BLACK:
            # Black's move ends a round, so every stun counted down by one
            self.rounds += 1
            for sq, turns in stuns_before.items():
                if turns == 1 and sq != captured_sq:
                    self.events.append({"type": "stun_expired", "square": square_name(sq),
                                        "piece": b.piece_char(sq)})
        if self.variant == "lightning" and side == BLACK and self._game_continues():
            self._weather()
        self.positions.append(self._position_key())
        self.hashes.append(b.hash)
        self._update_status()
        if self.time_control:
            self.clock["running_since"] = now if self.status == "playing" else None

    def _game_continues(self):
        return bool(self.board.legal_moves())

    def _weather(self):
        """Lightning, at the end of a round. The strike lands every strike_frequency rounds on the
        square forecast one round earlier, hitting whatever stands there (or nothing). Stuns already
        counted down this round, so a fresh stun lasts its full length."""
        b = self.board
        if self.forecast is not None and self.rounds % self.strike_frequency == 0:
            sq, self.forecast = self.forecast, None
            hit = b.stun(sq)
            self.events.append({"type": "lightning", "square": square_name(sq),
                                "piece": b.piece_char(sq) if hit else None,
                                "turns": Board.STUN_TURNS, "missed": not hit})
        if (self.rounds + 1) % self.strike_frequency == 0:
            occupied = [s for s in SQUARES if b.sq[s] != EMPTY]
            self.forecast = self.rng.choice(occupied)
            self.events.append({"type": "forecast", "square": square_name(self.forecast),
                                "piece": b.piece_char(self.forecast)})

    def _kings_alive(self):
        return KING in self.board.sq and -KING in self.board.sq

    def _moves(self):
        """Moves the side to move may play: normal legal moves, or in Fog of War every move that
        follows the piece rules (moving into check is allowed, there is no check)."""
        if self.variant == "fog":
            return self.board.pseudo_moves(fog=True) if self._kings_alive() else []
        return self.board.legal_moves()

    def _play(self, move):
        frm, to, flag = move
        b = self.board
        mover = COLOR[b.side]
        piece = b.sq[frm]
        capture = b.sq[to] != EMPTY or flag == EN_PASSANT
        b.make(move)
        b.undo_stack.clear()  # snapshots handle undo; don't let the board's stack grow
        if flag == CASTLE:
            text = "O-O" if to > frm else "O-O-O"
        else:
            letter = "" if abs(piece) == 1 else PIECE_TO_CHAR[abs(piece)]
            text = f"{letter}{square_name(frm)}{'x' if capture else '-'}{square_name(to)}"
            if flag >= PROMOTION:
                text += "=" + PIECE_TO_CHAR[PROMO_PIECE[flag]]
        if self.variant == "fog":
            if not self._kings_alive():
                text += "#"          # the king was captured
        elif b.in_check():
            text += "#" if not b.legal_moves() else "+"
        self.history.append(text)
        self.last_move = {"from": square_name(frm), "to": square_name(to)}
        self.events.append({"type": "move", "by": mover, "from": square_name(frm), "to": square_name(to),
                            "notation": text})

    def _update_status(self):
        b = self.board
        if self.variant == "fog":
            # No check, no checkmate, no stalemate trap: capture the king to win.
            if not self._kings_alive():
                self._finish("king_captured", "king_captured", OTHER[self.turn])
            elif not self._moves():
                self._finish("stalemate", "stalemate", None)   # only if nothing can move at all
            elif self.halfmove >= 100:
                self._finish("draw", "fifty_move", None)
            elif self.positions.count(self.positions[-1]) >= 3:
                self._finish("draw", "repetition", None)
            return
        if not b.legal_moves():
            if b.in_check():
                self._finish("checkmate", "checkmate", OTHER[self.turn])
            else:
                self._finish("stalemate", "stalemate", None)
        elif self._insufficient_material():
            self._finish("draw", "insufficient_material", None)
        elif self.halfmove >= 100:
            self._finish("draw", "fifty_move", None)
        elif self.positions.count(self.positions[-1]) >= 3:
            self._finish("draw", "repetition", None)

    def _finish(self, status, reason, winner):
        if self.time_control and self.clock["running_since"] is not None:
            # freeze the clock of the side that was thinking
            self.clock[self.turn] = self.remaining(self.turn)
        self.status, self.reason, self.winner = status, reason, winner
        self.clock["running_since"] = None
        self.events.append({"type": "game_over", "status": status, "reason": reason, "winner": winner})

    def _position_key(self):
        """Repetition key: pieces, side to move, castling rights, en passant (only when an
        en passant capture is actually possible, as FIDE says) and, in Lightning, the stuns and
        the forecast, because they change what can happen next."""
        b = self.board
        h = b.hash
        if b.ep and not any(m[2] == EN_PASSANT for m in self._moves()):
            h ^= ZOBRIST_EP[b.ep] ^ ZOBRIST_EP[0]
        stuns = ",".join(f"{s}:{n}" for s, n in sorted(b.stuns.items()))
        forecast = f"|F{self.forecast}" if self.forecast else ""   # a coming strike changes the position
        return f"{h:016x}|{stuns}{forecast}"

    def _pieces(self):
        """Non-king pieces as (color, type, square color) tuples."""
        out = []
        for sq in SQUARES:
            p = self.board.sq[sq]
            if p and abs(p) != KING:
                r, c = divmod(sq - 21, 10)
                out.append(("white" if p > 0 else "black", abs(p), (r + c) % 2))
        return out

    def _insufficient_material(self):
        """Neither side can ever checkmate: K v K, K+minor v K, or only bishops on one square color."""
        pieces = self._pieces()
        if any(t in (PAWN, ROOK, QUEEN) for _, t, _ in pieces):
            return False
        if len(pieces) <= 1:
            return True
        return all(t == BISHOP for _, t, _ in pieces) and len({sc for _, _, sc in pieces}) == 1

    def _can_mate(self, color):
        """Timeout rule: a side with only a king, or king plus one knight or bishop, can't win."""
        mine = [t for c, t, _ in self._pieces() if c == color]
        return any(t in (PAWN, ROOK, QUEEN) for t in mine) or len(mine) >= 2

    # ------------------------------------------------------------------ views / storage

    def legal_moves_by_square(self):
        """{"e2": ["e3", "e4"], ...} for the side to move. Promotions appear once per square."""
        out = {}
        for frm, to, _ in self._moves():
            targets = out.setdefault(square_name(frm), [])
            if square_name(to) not in targets:
                targets.append(square_name(to))
        return out

    def timeline(self, start=0, viewer=None):
        """Positions for move review: index 0 is the start, index i is the position after move i.
        Returns entries from `start` on, so clients can fetch only what they don't have yet.
        In Fog of War each entry shows only what `viewer` could see at that moment."""
        entries = [(Board.from_dict(s["board"]), s["last_move"], s["forecast"]) for s in self.snapshots[start:]]
        if start <= len(self.snapshots):
            entries.append((self.board, self.last_move, self.forecast))
        out = []
        for i, (b, last, forecast) in enumerate(entries, start):
            if self._fogged():
                mover = "white" if i % 2 == 1 else "black"     # move i was played by this side
                rows, last = self._fog_view(b, viewer, last, mover)
                out.append({"board": rows, "last_move": last, "stuns": {}, "check_square": None, "forecast": None})
                continue
            check = b.in_check()
            out.append({"board": b.rows(), "last_move": last,
                        "stuns": {square_name(sq): n for sq, n in b.stuns.items()},
                        "check_square": square_name(b.king[b.side]) if check else None,
                        "forecast": square_name(forecast) if forecast else None})
        return out

    # ------------------------------------------------------------------ Fog of War views

    def _fogged(self):
        """Fog applies while the game is on; at the end both players see everything."""
        return self.variant == "fog" and self.status == "playing"

    def _fog_view(self, b, viewer, last_move, mover):
        """Board rows with unseen squares replaced by HIDDEN, and the last move cut down to the
        squares the viewer can see (all of it if the viewer made it). viewer None sees nothing."""
        seen = b.visible_squares(SIDE[viewer]) if viewer else set()
        rows = []
        for r in range(8):
            rows.append("".join(b.piece_char(21 + r * 10 + c) if 21 + r * 10 + c in seen else HIDDEN
                                for c in range(8)))
        if last_move and mover != viewer:
            shown = {k: v for k, v in last_move.items() if parse_square(v) in seen}
            last_move = {"from": shown.get("from"), "to": shown.get("to")} if shown else None
        return rows, last_move

    def _fog_history(self, viewer):
        """The viewer's own moves, and "?" for every move the opponent made."""
        return [h if viewer == ("white" if i % 2 == 0 else "black") else "?" for i, h in enumerate(self.history)]

    def clock_view(self, now=None):
        if not self.time_control:
            return None
        now = now or self._now()
        running = self.turn if self.clock["running_since"] is not None and self.status == "playing" else None
        return {"time_control": self.time_control,
                "increment_ms": TIME_CONTROLS[self.time_control][1] * 1000,
                "white_ms": self.remaining("white", now), "black_ms": self.remaining("black", now),
                "running": running}

    def state(self, can_move=None, have=0, viewer=None):
        """Everything a client needs to draw the game. `can_move` decides whether legal moves
        are included (defaults to human_can_move()). `have` is how many timeline entries the
        client already holds; only the rest is sent (the last one is always resent).

        `viewer` is the color this client plays. It only matters in Fog of War, where the state
        is cut down to what that player can see. viewer None (a spectator) sees no pieces at all
        until the game ends, so nobody can watch the game and pass positions to a player."""
        b = self.board
        if can_move is None:
            can_move = self.human_can_move(viewer)
        fog = self._fogged()
        lightning = self.variant == "lightning"
        if self.variant == "fog" and not fog:
            have = 0   # the game just ended: resend the whole timeline, now unfogged
        start = max(0, min(int(have), len(self.history) + 1) - 1)
        in_check = not fog and self.variant != "fog" and (
            self.status == "playing" and b.in_check() or self.reason == "checkmate")
        state = {
            "board": b.rows(),
            "turn": self.turn,
            "status": self.status,
            "reason": self.reason,
            "winner": self.winner,
            "in_check": in_check,
            "check_square": square_name(b.king[b.side]) if in_check else None,
            "stuns": {square_name(sq): n for sq, n in b.stuns.items()},
            "forecast": square_name(self.forecast) if self.forecast else None,
            "legal_moves": self.legal_moves_by_square() if can_move and self.status == "playing" else {},
            "last_move": self.last_move,
            "history": self.history,
            "timeline_start": start,
            "timeline": self.timeline(start, viewer),
            "events": self.events,
            "rounds": self.rounds,
            "next_strike_in": self.strike_frequency - self.rounds % self.strike_frequency if lightning else None,
            "clock": self.clock_view(),
            "can_undo": self.undo_plies() > 0,
            "fifty_move_count": self.halfmove,
            "fog": fog,
            "settings": {"mode": self.mode, "variant": self.variant, "difficulty": self.difficulty,
                         "player_color": self.player_color, "strike_frequency": self.strike_frequency,
                         "time_control": self.time_control, "takebacks": self.takebacks},
            "ai": self.ai_info,
        }
        if fog:
            mover = OTHER[self.turn]          # who played the last move
            state["board"], state["last_move"] = self._fog_view(b, viewer, self.last_move, mover)
            state["history"] = self._fog_history(viewer)
            # Opponent moves are secret; only results and your own moves are reported
            state["events"] = [e for e in self.events if e["type"] == "game_over"
                               or (e["type"] == "move" and e["by"] == viewer)]
            state["fifty_move_count"] = None  # a reset would reveal a hidden capture or pawn move
            if self.ai_info:
                state["ai"] = {k: v for k, v in self.ai_info.items() if k != "score"}  # score = material
        return state

    def to_dict(self):
        return {
            "v": 5,
            "board": self.board.to_dict(),
            "mode": self.mode,
            "variant": self.variant,
            "difficulty": self.difficulty,
            "player_color": self.player_color,
            "strike_frequency": self.strike_frequency,
            "time_control": self.time_control,
            "takebacks": self.takebacks,
            "forecast": self.forecast,
            "clock": self.clock,
            "rounds": self.rounds,
            "status": self.status,
            "reason": self.reason,
            "winner": self.winner,
            "history": self.history,
            "last_move": self.last_move,
            "events": self.events,
            "ai_info": self.ai_info,
            "halfmove": self.halfmove,
            "snapshots": self.snapshots,
            "positions": self.positions,
            "hashes": self.hashes,
        }

    @classmethod
    def from_dict(cls, data, rng=None, clock=time.time):
        if data.get("v") != 5:
            raise ValueError("unknown save format")
        game = cls(data["mode"], data["variant"], data["difficulty"], data["strike_frequency"],
                   data["player_color"], data["time_control"], data["takebacks"], rng=rng, clock=clock)
        # A finished Fog of War game can be missing the king that was captured
        king_taken = data["variant"] == "fog" and data["status"] == "king_captured"
        game.board = Board.from_dict(data["board"], allow_missing_king=king_taken)
        for key in ("rounds", "halfmove"):
            setattr(game, key, int(data[key]))
        for key in ("status", "reason", "winner", "last_move", "ai_info", "forecast"):
            setattr(game, key, data[key])
        for key in ("history", "events", "snapshots", "positions", "hashes"):
            setattr(game, key, list(data[key]))
        game.clock = dict(data["clock"])
        if game.status not in STATUSES:
            raise ValueError("bad status")
        if not (len(game.snapshots) == len(game.history) == len(game.positions) - 1 == len(game.hashes) - 1):
            raise ValueError("inconsistent save")
        return game


__all__ = ["Game", "IllegalMove", "MODES", "VARIANTS", "COLORS", "DIFFICULTIES", "STRIKE_FREQUENCIES",
           "PROMOTION_PIECES", "TIME_CONTROLS"]