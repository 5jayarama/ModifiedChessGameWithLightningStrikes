"""Alpha-beta search with iterative deepening, a time budget, move ordering and quiescence.

Scores are negamax style: always from the point of view of the side to move.
"""
import random
import time

from .board import BLACK, EMPTY, EN_PASSANT, KING, MATERIAL, PROMOTION, QUEEN

MATE = 1_000_000
INF = 10_000_000

# difficulty -> (max depth in half-moves, seconds to think). 0 is "dynamic", see pick_level().
# Level N searches N+1 half-moves like the original game, plus quiescence, but now with a
# time cap so the slowest reply is bounded. Level 6 goes as deep as it can in its time.
LEVELS = {
    1: (2, 0.5),
    2: (3, 1.0),
    3: (4, 1.5),
    4: (5, 2.0),
    5: (6, 3.0),
    6: (64, 5.0),
}

# Victim/attacker values for capture ordering, indexed by abs(piece)
_VALUE = [0] + [MATERIAL[p] for p in range(1, 7)]


class TimeUp(Exception):
    pass


def pick_level(difficulty, board, ai_side=BLACK):
    """Dynamic difficulty (0): stronger when the human is ahead, weaker when behind.
    Same thresholds as the original get_dynamic_difficulty."""
    if difficulty != 0:
        return difficulty
    balance = board.evaluate() * -ai_side   # positive = the human is ahead
    if balance > 700:
        return 4
    if balance < -700:
        return 1
    return 2


class Searcher:
    def __init__(self, max_depth=4, time_limit=2.0, rng=None, quiescence=True):
        self.max_depth = max_depth
        self.time_limit = time_limit
        self.game_history = set()
        self.path = []
        self.root_bonus = None
        self.rng = rng or random.Random()
        self.quiescence = quiescence
        self.tt = {}            # position hash -> best move found there (used for ordering only)
        self.killers = []
        self.history = {}
        self.nodes = 0
        self.deadline = 0.0
        self.can_stop = False

    @classmethod
    def for_level(cls, level, max_seconds=None, rng=None):
        depth, seconds = LEVELS[level]
        if max_seconds is not None:
            seconds = min(seconds, max_seconds)
        return cls(depth, seconds, rng=rng)

    # ------------------------------------------------------------------ public

    def best_move(self, board, history=(), root_bonus=None):
        """Search a copy of `board`. Returns (move, info) or (None, info) if there are no legal moves.

        `history` holds the hashes of positions already seen in the game. Reaching one of them
        again scores as a draw, so the AI avoids repetition when ahead and seeks it when behind.
        `root_bonus(board)` (optional) adds a score, from the mover's point of view, to the position
        after each root move. The game uses it for things the search can't see, like a forecast strike."""
        b = board.copy()
        self.game_history = set(history)
        self.root_bonus = root_bonus
        self.path = []
        start = time.perf_counter()
        self.deadline = start + self.time_limit
        self.nodes = 0
        self.can_stop = False
        self.killers = [[None, None] for _ in range(128)]
        self.history = {}

        root_moves = b.legal_moves()
        info = {"depth": 0, "score": 0, "nodes": 0, "seconds": 0.0}
        if not root_moves:
            return None, info
        self.rng.shuffle(root_moves)  # variety between games: ties go to a random move

        best = root_moves[0]
        best_score = -INF
        for depth in range(1, self.max_depth + 1):
            try:
                score, move = self._root(b, depth, root_moves, best if depth > 1 else None)
            except TimeUp:
                break
            best, best_score = move, score
            info.update(depth=depth, score=best_score)
            self.can_stop = True  # depth 1 always finishes, so there is always a move to play
            if abs(best_score) >= MATE - 1000:
                break  # forced mate found; deeper search won't change the move
            if time.perf_counter() - start > self.time_limit * 0.5:
                break  # the next depth takes several times longer; don't start what can't finish
        info["nodes"] = self.nodes
        info["seconds"] = round(time.perf_counter() - start, 3)
        # report white's perspective, like Board.evaluate()
        info["score"] = best_score if board.side != BLACK else -best_score
        return best, info

    def score_moves(self, board, moves, margin=100):
        """Score every move in `moves` (mover's point of view) instead of just finding the best one.
        Each move gets a full search window, so the scores can be compared and averaged across
        boards (the Fog of War AI does this over its guesses). Scores within `margin` of the best are
        exact; a worse move's score is an upper bound that is still at least `margin` below the best.
        A move that leaves the mover's king attacked scores -MATE: in Fog of War that king gets captured.

        Returns ({move: score}, depth) from the deepest depth that finished. Depth 1 always finishes."""
        b = board.copy()
        self.game_history = set()
        self.root_bonus = None
        self.path = []
        start = time.perf_counter()
        self.deadline = start + self.time_limit
        self.can_stop = False
        self.killers = [[None, None] for _ in range(128)]
        self.history = {}
        side = b.side
        order = list(moves)
        self.rng.shuffle(order)
        scores, done = {}, 0
        for depth in range(1, self.max_depth + 1):
            current = {}
            try:
                best = -INF
                for m in order:
                    if abs(b.sq[m[1]]) == KING:
                        current[m] = best = MATE   # taking the king wins on the spot
                        continue
                    b.make(m)
                    if b.is_attacked(b.king[side], -side):
                        s = -MATE
                    else:
                        # Exact within `margin` of the best move so far; anything worse only needs
                        # to be known as "at least margin worse", which is much cheaper to prove
                        alpha = best - margin if best > -INF else -INF
                        s = -self._negamax(b, depth - 1, -INF, -alpha, 1)
                    b.unmake()
                    current[m] = s
                    best = max(best, s)
            except TimeUp:
                break   # the board copy is mid-search now; it is not used again
            scores, done = current, depth
            self.can_stop = True
            order.sort(key=lambda m: -current[m])   # best first helps the next depth
            if time.perf_counter() - start > self.time_limit * 0.4:
                break
        return scores, done

    # ------------------------------------------------------------------ search

    def _root(self, b, depth, moves, previous_best):
        if previous_best is not None:
            moves.sort(key=lambda m: m != previous_best)  # stable: keeps the shuffle for the rest
        alpha, beta = -INF, INF
        best_move = moves[0]
        for m in moves:
            b.make(m)
            score = -self._negamax(b, depth - 1, -beta, -alpha, 1)
            if self.root_bonus:
                score += self.root_bonus(b)
            b.unmake()
            if score > alpha:
                alpha, best_move = score, m
        return alpha, best_move

    def _tick(self):
        self.nodes += 1
        if self.can_stop and (self.nodes & 1023) == 0 and time.perf_counter() > self.deadline:
            raise TimeUp

    def _negamax(self, b, depth, alpha, beta, ply):
        self._tick()
        if b.hash in self.game_history or b.hash in self.path:
            return 0   # repeated position: treat as a draw
        if depth <= 0:
            return self._quiesce(b, alpha, beta, ply) if self.quiescence else b.score * b.side

        side = b.side
        tt_move = self.tt.get(b.hash)
        moves = b.pseudo_moves()
        moves = self._order(b, moves, tt_move, ply)

        best_score = -INF
        best_move = None
        legal = 0
        self.path.append(b.hash)
        for m in moves:
            b.make(m)
            if b.is_attacked(b.king[side], -side):
                b.unmake()
                continue
            legal += 1
            score = -self._negamax(b, depth - 1, -beta, -alpha, ply + 1)
            b.unmake()
            if score > best_score:
                best_score, best_move = score, m
                if score > alpha:
                    alpha = score
                    if alpha >= beta:
                        if b.sq[m[1]] == EMPTY and m[2] < PROMOTION and m[2] != EN_PASSANT:  # quiet move caused the cutoff
                            k = self.killers[ply]
                            if k[0] != m:
                                k[1], k[0] = k[0], m
                            key = (m[0], m[1])
                            self.history[key] = self.history.get(key, 0) + depth * depth
                        break

        self.path.pop()
        if legal == 0:
            # Checkmate (prefer faster mates via ply) or stalemate (a draw, not a loss)
            return -(MATE - ply) if b.in_check() else 0
        if best_move is not None:
            self.tt[b.hash] = best_move
            if len(self.tt) > 500_000:
                self.tt.clear()
        return best_score

    def _quiesce(self, b, alpha, beta, ply):
        """Only captures and promotions, until the position is quiet. Stops horizon blunders."""
        self._tick()
        stand_pat = b.score * b.side
        if stand_pat >= beta:
            return stand_pat
        if stand_pat > alpha:
            alpha = stand_pat
        side = b.side
        moves = b.pseudo_moves(captures_only=True)
        if not moves:
            return alpha
        s = b.sq
        moves.sort(key=lambda m: -(_VALUE[abs(s[m[1]])] * 10 - _VALUE[abs(s[m[0]])]
                                   + (_VALUE[QUEEN] if m[2] == PROMOTION else 0)))
        for m in moves:
            b.make(m)
            if b.is_attacked(b.king[side], -side):
                b.unmake()
                continue
            score = -self._quiesce(b, -beta, -alpha, ply + 1)
            b.unmake()
            if score >= beta:
                return score
            if score > alpha:
                alpha = score
        return alpha

    def _order(self, b, moves, tt_move, ply):
        """Best guesses first so alpha-beta cuts more: previous best, captures (most valuable
        victim, least valuable attacker), promotions, killer moves, then history."""
        s = b.sq
        killers = self.killers[ply] if ply < len(self.killers) else (None, None)
        history = self.history

        def key(m):
            if m == tt_move:
                return -10_000_000
            victim = s[m[1]]
            if m[2] == PROMOTION:
                return -1_100_000 - _VALUE[abs(victim)] * 10        # queening, best first
            if m[2] > PROMOTION:
                return 0                                             # under-promotions: almost never best
            if victim or m[2] == EN_PASSANT:
                return -1_000_000 - _VALUE[abs(victim) or 1] * 10 + _VALUE[abs(s[m[0]])]
            if m == killers[0]:
                return -800_000
            if m == killers[1]:
                return -799_999
            return -history.get((m[0], m[1]), 0)

        moves.sort(key=key)
        return moves