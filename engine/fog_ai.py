"""The computer's Fog of War player. It only uses what a human in its seat could know.

What it knows:
  - the starting position, and every enemy piece it has seen since (the same views the
    human gets after every move),
  - which enemy pieces it captured, so how many of each type are left (a hidden promotion
    is not known: it still counts that pawn as a pawn).

What it does each move:
  1. Remembers where each hidden enemy piece was last seen.
  2. Builds several guesses of the full board: visible pieces as they are, hidden ones near
     where they were last seen (the longer ago, the more likely they moved). Hidden pieces never
     go on squares it can see, or on squares it attacks (a piece there would be visible as a
     capture).
  3. Scores every move it can play on each guess with the normal search and plays the move with
     the best average. A move that loses the king on some guesses scores badly on those, so it
     avoids risks that are likely and accepts ones that are not.

It never reads a hidden square of the real board.
"""
import time
from collections import Counter

from .board import (BISHOP, BISHOP_DIRS, BK, BQ, EMPTY, EN_PASSANT, KING, KING_DIRS, KNIGHT,
                    KNIGHT_DIRS, OFF, PAWN, QUEEN, ROOK, ROOK_DIRS, SQUARES, WHITE, WK, WQ)
from .search import LEVELS, MATE, Searcher

START_COUNTS = {PAWN: 8, KNIGHT: 2, BISHOP: 2, ROOK: 2, QUEEN: 1, KING: 1}
SAMPLES = 6          # guesses per move
CLAMP = 2500         # a lost king counts as about two queens, so one bad guess doesn't veto a move
STAY_ODDS = 0.8      # chance a remembered piece hasn't moved, per enemy move since it was seen
STEPS = {KNIGHT: KNIGHT_DIRS, BISHOP: BISHOP_DIRS, ROOK: ROOK_DIRS, QUEEN: KING_DIRS, KING: KING_DIRS}
SLIDERS = {BISHOP, ROOK, QUEEN}
# Home squares for castling rights: (king square, rook square, right) per side
CASTLE_HOMES = {WHITE: ((95, 98, WK), (95, 91, WQ)), -WHITE: ((25, 28, BK), (25, 21, BQ))}


def _distance(a, b):
    return max(abs(a // 10 - b // 10), abs(a % 10 - b % 10))


class Memory:
    """Where the AI last saw each enemy piece, and how many of each type are still alive."""

    def __init__(self, side, start):
        self.side = side
        enemy = -side
        # square -> (piece type, ply it was last seen there)
        self.seen = {sq: (abs(start.sq[sq]), 0) for sq in SQUARES if start.sq[sq] * enemy > 0}
        self.alive = Counter(START_COUNTS)

    def update(self, board, ply, previous=None):
        """Look at `board` (the AI's view after a move). `previous` is the board before that move,
        used to count the AI's own captures."""
        enemy = -self.side
        if previous is not None and previous.side == self.side:
            before = Counter(abs(p) for p in previous.sq if p != OFF and p * enemy > 0)
            after = Counter(abs(p) for p in board.sq if p != OFF and p * enemy > 0)
            self.alive -= before - after       # what the AI just took

        visible = board.visible_squares(self.side)
        now = {sq: abs(board.sq[sq]) for sq in visible if board.sq[sq] * enemy > 0}
        seen = self.seen
        # Remembered pieces whose square is visible but no longer holds them have moved (or were taken)
        gone = {sq for sq, (t, _) in seen.items() if sq in visible and now.get(sq) != t}
        for sq, t in now.items():
            if seen.get(sq, (None,))[0] == t:
                continue
            # A piece appeared here: it is most likely the closest remembered piece of that type
            pool = [s for s, (tt, _) in seen.items()
                    if tt == t and s != sq and (s in gone or s not in visible)
                    and (t != PAWN or abs(s % 10 - sq % 10) <= 1)]
            if pool:
                match = min(pool, key=lambda s: (s not in gone, _distance(s, sq), -seen[s][1]))
                del seen[match]
                gone.discard(match)
        for sq in gone:
            if board.sq[sq] * self.side > 0:
                del seen[sq]                   # captured by the AI
            # otherwise the piece walked into the fog: keep it, the guesses move it on
        for sq, t in now.items():
            seen[sq] = (t, ply)
        return visible


def build_memory(boards, side):
    """Replay the game from the AI's seat. `boards` is every position from the start to now."""
    memory = Memory(side, boards[0])
    visible = memory.update(boards[0], 0)
    for ply in range(1, len(boards)):
        visible = memory.update(boards[ply], ply, boards[ply - 1])
    return memory, visible


def _targets(board, sq, kind, enemy, free):
    """Squares a hidden piece of `kind` on `sq` could reach in one move, among `free` squares."""
    if kind == PAWN:
        fwd = -10 * enemy
        out = [sq + fwd, sq + fwd - 1, sq + fwd + 1]
        start_row = 8 if enemy == WHITE else 3
        if sq // 10 == start_row:
            out.append(sq + 2 * fwd)
        return [t for t in out if t in free]
    out = []
    for d in STEPS[kind]:
        t = sq + d
        while board.sq[t] != OFF:
            if t in free:
                out.append(t)
            if kind not in SLIDERS or board.sq[t] != EMPTY:
                break
            t += d
    return out


def guess_board(real, side, memory, visible, ply, rng):
    """One plausible full board, built only from what the AI knows. None if no guess fits."""
    b = real.copy()
    enemy = -side
    for sq in SQUARES:
        if sq not in visible and b.sq[sq] * enemy > 0:
            b.sq[sq] = EMPTY                   # forget the real hidden pieces
    shown = Counter(abs(b.sq[sq]) for sq in SQUARES if b.sq[sq] * enemy > 0)
    hidden = {t: max(0, memory.alive[t] - shown[t]) for t in START_COUNTS}
    promoted = sum(max(0, shown[t] - memory.alive[t]) for t in (KNIGHT, BISHOP, ROOK, QUEEN))
    hidden[PAWN] = max(0, hidden[PAWN] - promoted)   # a visible extra queen was a pawn

    # Free squares: not visible, empty, not attacked by the AI. Pawns never stand on the end ranks.
    free = {sq for sq in SQUARES if sq not in visible and b.sq[sq] == EMPTY and not b.is_attacked(sq, side)}
    if hidden[KING] and not free:
        return None

    for kind in (KING, QUEEN, ROOK, BISHOP, KNIGHT, PAWN):
        n = hidden[kind]
        if not n:
            continue
        spots = free if kind != PAWN else {sq for sq in free if 31 <= sq <= 88}
        remembered = sorted(((p, sq) for sq, (t, p) in memory.seen.items() if t == kind), reverse=True)[:n]
        for seen_at, sq in remembered:
            moves_since = (ply - seen_at) // 2
            here = sq
            if sq not in spots or rng.random() > STAY_ODDS ** moves_since:
                # It moved: one step (sometimes two) from where it was seen
                for _ in range(1 if moves_since < 4 or rng.random() < 0.5 else 2):
                    options = _targets(b, here, kind, enemy, spots)
                    if not options:
                        break
                    here = rng.choice(options)
            if here not in spots:
                if not spots:
                    break
                near = min(_distance(s, sq) for s in spots)
                here = rng.choice([s for s in spots if _distance(s, sq) == near])
            b.sq[here] = kind * enemy
            free.discard(here)
            spots.discard(here)
            n -= 1
        while n > 0 and spots:                 # never seen at all (rare): anywhere it could be
            here = rng.choice(sorted(spots))
            b.sq[here] = kind * enemy
            free.discard(here)
            spots.discard(here)
            n -= 1
        if kind == KING and n:
            return None

    # Castling rights: own rights are known; the enemy's only where king and rook stand at home
    b.castling &= ~sum(bit for _, _, bit in CASTLE_HOMES[enemy])
    for king_sq, rook_sq, bit in CASTLE_HOMES[enemy]:
        if b.sq[king_sq] == KING * enemy and b.sq[rook_sq] == ROOK * enemy and real.castling & bit:
            b.castling |= bit
    # En passant is the AI's own option; it only matters if the AI can actually take
    if b.ep and not any(m[2] == EN_PASSANT for m in real.pseudo_moves(fog=True)):
        b.ep = 0
    b._recompute()
    return b


def choose_move(boards, side, level, max_seconds, rng):
    """Pick a Fog of War move for `side` from the positions so far (boards[-1] is now)."""
    real = boards[-1]
    moves = real.pseudo_moves(fog=True)
    info = {"depth": 0, "score": 0, "nodes": 0, "seconds": 0.0, "guesses": 0}
    if not moves:
        return None, info
    if len(moves) == 1:
        return moves[0], info
    memory, visible = build_memory(boards, side)
    ply = len(boards) - 1
    depth, seconds = LEVELS[level]
    if max_seconds is not None:
        seconds = min(seconds, max_seconds)

    start = time.perf_counter()
    totals = Counter()
    guesses = 0
    depths = []
    nodes = 0
    for _ in range(SAMPLES * 3):
        if guesses == SAMPLES:
            break
        guess = guess_board(real, side, memory, visible, ply, rng)
        if guess is None:
            continue
        searcher = Searcher(depth, seconds / SAMPLES, rng=rng)
        scores, done = searcher.score_moves(guess, moves)
        for m in moves:
            totals[m] += max(-CLAMP, min(CLAMP, scores.get(m, -MATE)))
        guesses += 1
        depths.append(done)
        nodes += searcher.nodes
    if not guesses:
        return rng.choice(moves), info
    best = max(moves, key=lambda m: (totals[m], rng.random()))
    score = totals[best] // guesses
    info.update(depth=min(depths), nodes=nodes, guesses=guesses,
                seconds=round(time.perf_counter() - start, 3),
                score=score if side == WHITE else -score)
    return best, info