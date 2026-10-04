"""Board state, move generation and evaluation for Chess with Lightning Strikes.

No pygame or Flask in here, so every client (pygame, web server, tests) shares one engine.

Representation: a 10x12 "mailbox". The 8x8 board sits inside a border of OFF squares, so
sliding pieces and knights stop at the edge without any bounds checks.

    index = 21 + row * 10 + col     row 0 = rank 8, col 0 = file a
    a8 = 21, h8 = 28, a1 = 91, h1 = 98

White pieces are positive ints, black pieces negative, empty squares 0.
White pawns move toward row 0 (index - 10).
"""
import random

from .tables import PIECE_VALUES, TABLES

EMPTY, PAWN, KNIGHT, BISHOP, ROOK, QUEEN, KING = 0, 1, 2, 3, 4, 5, 6
OFF = 99  # border square

WHITE, BLACK = 1, -1

# Move flags. A move is a tuple (from_square, to_square, flag).
# Every flag >= PROMOTION is a promotion; PROMOTION itself promotes to a queen.
NORMAL, DOUBLE_PUSH, EN_PASSANT, CASTLE = 0, 1, 2, 3
PROMOTION, PROMO_KNIGHT, PROMO_BISHOP, PROMO_ROOK = 4, 5, 6, 7

# Castling rights bits
WK, WQ, BK, BQ = 1, 2, 4, 8

KNIGHT_DIRS = (-21, -19, -12, -8, 8, 12, 19, 21)
BISHOP_DIRS = (-11, -9, 9, 11)
ROOK_DIRS = (-10, -1, 1, 10)
KING_DIRS = BISHOP_DIRS + ROOK_DIRS

CHAR_TO_PIECE = {"P": PAWN, "N": KNIGHT, "B": BISHOP, "R": ROOK, "Q": QUEEN, "K": KING}
PIECE_TO_CHAR = {v: k for k, v in CHAR_TO_PIECE.items()}

PROMO_PIECE = {PROMOTION: QUEEN, PROMO_KNIGHT: KNIGHT, PROMO_BISHOP: BISHOP, PROMO_ROOK: ROOK}
PROMO_FLAG = {v: k for k, v in PROMO_PIECE.items()}            # piece type -> flag
ALL_PROMOTIONS = (PROMOTION, PROMO_KNIGHT, PROMO_ROOK, PROMO_BISHOP)

SQUARES = tuple(21 + r * 10 + c for r in range(8) for c in range(8))  # a8 .. h1

# Moving from or onto these squares removes castling rights (king or rook moved or was captured)
CASTLE_KEEP = [WK | WQ | BK | BQ] * 120
CASTLE_KEEP[95] &= ~(WK | WQ)  # e1
CASTLE_KEEP[98] &= ~WK         # h1
CASTLE_KEEP[91] &= ~WQ         # a1
CASTLE_KEEP[25] &= ~(BK | BQ)  # e8
CASTLE_KEEP[28] &= ~BK         # h8
CASTLE_KEEP[21] &= ~BQ         # a8

# king from -> (king to, rook from, rook to, right bit)
CASTLES = {
    WHITE: ((95, 97, 98, 96, WK), (95, 93, 91, 94, WQ)),
    BLACK: ((25, 27, 28, 26, BK), (25, 23, 21, 24, BQ)),
}
ROOK_CASTLE_MOVES = {97: (98, 96), 93: (91, 94), 27: (28, 26), 23: (21, 24)}

MATERIAL = {PAWN: PIECE_VALUES["p"], KNIGHT: PIECE_VALUES["n"], BISHOP: PIECE_VALUES["b"],
            ROOK: PIECE_VALUES["r"], QUEEN: PIECE_VALUES["q"], KING: PIECE_VALUES["k"]}


def square_name(sq):
    r, c = divmod(sq - 21, 10)
    return "abcdefgh"[c] + str(8 - r)


def parse_square(name):
    """'e2' -> mailbox index. Raises ValueError on anything else."""
    if not isinstance(name, str) or len(name) != 2 or name[0] not in "abcdefgh" or name[1] not in "12345678":
        raise ValueError(f"bad square: {name!r}")
    return 21 + (8 - int(name[1])) * 10 + "abcdefgh".index(name[0])


def _build_eval_tables():
    """SCORE[piece + 6][sq]: material + piece-square value, signed (+ for white, - for black).

    The evaluation is exactly the original one, just looked up per square so it can be
    updated incrementally when a move is made instead of rescanning the board."""
    score = [[0] * 120 for _ in range(13)]
    for ptype, char in PIECE_TO_CHAR.items():
        table = TABLES.get(char.lower())
        for sq in SQUARES:
            r, c = divmod(sq - 21, 10)
            white_pst = table[7 - r][c] if table else 0
            black_pst = table[r][c] if table else 0
            score[ptype + 6][sq] = MATERIAL[ptype] + white_pst
            score[-ptype + 6][sq] = -(MATERIAL[ptype] + black_pst)
    return score


SCORE = _build_eval_tables()

# Zobrist keys, used by the search to remember the best move per position
_rng = random.Random(20240501)
ZOBRIST = [[_rng.getrandbits(64) for _ in range(120)] for _ in range(13)]
ZOBRIST_SIDE = _rng.getrandbits(64)
ZOBRIST_CASTLE = [_rng.getrandbits(64) for _ in range(16)]
ZOBRIST_EP = [_rng.getrandbits(64) for _ in range(120)]

START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


class Board:
    # How many full rounds (white move + black move) a struck piece stays stunned
    STUN_TURNS = 3
    # False: a stunned piece is inert. It can't move or capture, gives no check, guards nothing.
    # True: stunned pieces still attack, they just can't move.
    STUNNED_PIECES_ATTACK = False

    def __init__(self, fen=START_FEN, allow_missing_king=False):
        self.sq = [OFF] * 120
        self.side = WHITE
        self.castling = 0
        self.ep = 0             # square a pawn just skipped over, or 0
        self.stuns = {}         # square -> full rounds of stun remaining
        self.king = [0, 0, 0]   # king[WHITE] and king[BLACK] (index -1 is the last slot)
        self.score = 0          # material + position, from white's point of view
        self.hash = 0
        self.undo_stack = []
        self.load_fen(fen, allow_missing_king)

    # ----------------------------------------------------------------- setup / IO

    def load_fen(self, fen, allow_missing_king=False):
        """allow_missing_king: only for a finished Fog of War game, where a king was captured."""
        parts = fen.split()
        if len(parts) < 4:
            raise ValueError("FEN needs at least 4 fields")
        rows = parts[0].split("/")
        if len(rows) != 8:
            raise ValueError("FEN needs 8 ranks")
        self.sq = [OFF] * 120
        for sq in SQUARES:
            self.sq[sq] = EMPTY
        for r, row in enumerate(rows):
            c = 0
            for ch in row:
                if ch.isdigit():
                    c += int(ch)
                    continue
                if ch.upper() not in CHAR_TO_PIECE or c > 7:
                    raise ValueError(f"bad FEN rank: {row}")
                piece = CHAR_TO_PIECE[ch.upper()]
                self.sq[21 + r * 10 + c] = piece if ch.isupper() else -piece
                c += 1
            if c != 8:
                raise ValueError(f"bad FEN rank: {row}")
        if parts[1] not in ("w", "b"):
            raise ValueError("bad side to move")
        self.side = WHITE if parts[1] == "w" else BLACK
        self.castling = 0
        for ch, bit in (("K", WK), ("Q", WQ), ("k", BK), ("q", BQ)):
            if ch in parts[2]:
                self.castling |= bit
        self.ep = 0 if parts[3] == "-" else parse_square(parts[3])
        self.stuns = {}
        self.undo_stack = []
        self._recompute(allow_missing_king)

    def _recompute(self, allow_missing_king=False):
        """Rebuild king squares, score and hash from scratch."""
        self.king = [0, 0, 0]
        self.score = 0
        h = 0
        for sq in SQUARES:
            p = self.sq[sq]
            if p:
                self.score += SCORE[p + 6][sq]
                h ^= ZOBRIST[p + 6][sq]
                if p == KING:
                    self.king[WHITE] = sq
                elif p == -KING:
                    self.king[BLACK] = sq
        if (not self.king[WHITE] or not self.king[BLACK]) and not allow_missing_king:
            raise ValueError("each side needs exactly one king")
        if self.side == BLACK:
            h ^= ZOBRIST_SIDE
        h ^= ZOBRIST_CASTLE[self.castling] ^ ZOBRIST_EP[self.ep]
        self.hash = h

    def fen(self):
        rows = []
        for r in range(8):
            row, empty = "", 0
            for c in range(8):
                p = self.sq[21 + r * 10 + c]
                if p == EMPTY:
                    empty += 1
                    continue
                if empty:
                    row += str(empty)
                    empty = 0
                ch = PIECE_TO_CHAR[abs(p)]
                row += ch if p > 0 else ch.lower()
            rows.append(row + (str(empty) if empty else ""))
        castling = "".join(ch for ch, bit in (("K", WK), ("Q", WQ), ("k", BK), ("q", BQ)) if self.castling & bit) or "-"
        ep = square_name(self.ep) if self.ep else "-"
        return f"{'/'.join(rows)} {'w' if self.side == WHITE else 'b'} {castling} {ep} 0 1"

    def to_dict(self):
        return {"fen": self.fen(), "stuns": {square_name(s): n for s, n in self.stuns.items()}}

    @classmethod
    def from_dict(cls, data, allow_missing_king=False):
        board = cls(data["fen"], allow_missing_king)
        for name, turns in data.get("stuns", {}).items():
            sq = parse_square(name)
            if not isinstance(turns, int) or turns <= 0 or board.sq[sq] == EMPTY:
                raise ValueError("bad stun entry")
            board.stuns[sq] = turns
        return board

    def copy(self):
        b = Board.__new__(Board)
        b.sq = self.sq[:]
        b.side = self.side
        b.castling = self.castling
        b.ep = self.ep
        b.stuns = dict(self.stuns)
        b.king = self.king[:]
        b.score = self.score
        b.hash = self.hash
        b.undo_stack = []
        return b

    def piece_char(self, sq):
        """'P', 'k', ... or ' ' for an empty square (same letters as the original board)."""
        p = self.sq[sq]
        if p == EMPTY:
            return " "
        ch = PIECE_TO_CHAR[abs(p)]
        return ch if p > 0 else ch.lower()

    def rows(self):
        """8 strings, rank 8 first, one char per square."""
        return ["".join(self.piece_char(21 + r * 10 + c) for c in range(8)) for r in range(8)]

    # ----------------------------------------------------------------- attacks

    def is_attacked(self, target, by):
        """Is `target` attacked by side `by`? Stunned pieces don't attack unless STUNNED_PIECES_ATTACK."""
        s = self.sq
        skip = self.stuns if (self.stuns and not self.STUNNED_PIECES_ATTACK) else None
        # Pawns: a white pawn on p attacks p-9 and p-11, so look 9 and 11 squares "behind" the target
        pawn = PAWN * by
        for d in (9, 11):
            p = target + d * by
            if s[p] == pawn and not (skip and p in skip):
                return True
        knight = KNIGHT * by
        for d in KNIGHT_DIRS:
            p = target + d
            if s[p] == knight and not (skip and p in skip):
                return True
        king = KING * by
        for d in KING_DIRS:
            p = target + d
            if s[p] == king and not (skip and p in skip):
                return True
        rook, bishop, queen = ROOK * by, BISHOP * by, QUEEN * by
        for d in ROOK_DIRS:
            p = target + d
            while s[p] == EMPTY:
                p += d
            t = s[p]
            if (t == rook or t == queen) and not (skip and p in skip):
                return True
        for d in BISHOP_DIRS:
            p = target + d
            while s[p] == EMPTY:
                p += d
            t = s[p]
            if (t == bishop or t == queen) and not (skip and p in skip):
                return True
        return False

    def in_check(self, side=None):
        side = self.side if side is None else side
        return self.is_attacked(self.king[side], -side)

    # ----------------------------------------------------------------- move generation

    def pseudo_moves(self, captures_only=False, fog=False):
        """Moves that follow piece movement rules but may leave the own king in check.

        captures_only (for quiescence search) returns captures and queen promotions only.
        fog=True uses Fog of War castling: there is no check, so castling ignores attacks.
        Stunned pieces don't move."""
        moves = []
        add = moves.append
        s = self.sq
        side = self.side
        stuns = self.stuns
        fwd = -10 * side
        start_lo, start_hi = (81, 88) if side == WHITE else (31, 38)
        promo_lo, promo_hi = (21, 28) if side == WHITE else (91, 98)

        for frm in SQUARES:
            p = s[frm] * side
            if p <= 0:
                continue
            if stuns and frm in stuns:
                continue

            if p == PAWN:
                to = frm + fwd
                if s[to] == EMPTY:
                    if promo_lo <= to <= promo_hi:
                        for flag in ((PROMOTION,) if captures_only else ALL_PROMOTIONS):
                            add((frm, to, flag))
                    elif not captures_only:
                        add((frm, to, NORMAL))
                        if start_lo <= frm <= start_hi and s[to + fwd] == EMPTY:
                            add((frm, to + fwd, DOUBLE_PUSH))
                for to in (frm + fwd - 1, frm + fwd + 1):
                    t = s[to]
                    if t != OFF and t * side < 0:
                        if promo_lo <= to <= promo_hi:
                            for flag in ((PROMOTION,) if captures_only else ALL_PROMOTIONS):
                                add((frm, to, flag))
                        else:
                            add((frm, to, NORMAL))
                    elif to == self.ep and t == EMPTY:
                        add((frm, to, EN_PASSANT))

            elif p == KNIGHT or p == KING:
                for d in (KNIGHT_DIRS if p == KNIGHT else KING_DIRS):
                    to = frm + d
                    t = s[to]
                    if t == EMPTY:
                        if not captures_only:
                            add((frm, to, NORMAL))
                    elif t != OFF and t * side < 0:
                        add((frm, to, NORMAL))

            else:  # bishop, rook, queen
                dirs = BISHOP_DIRS if p == BISHOP else ROOK_DIRS if p == ROOK else KING_DIRS
                for d in dirs:
                    to = frm + d
                    t = s[to]
                    while t == EMPTY:
                        if not captures_only:
                            add((frm, to, NORMAL))
                        to += d
                        t = s[to]
                    if t != OFF and t * side < 0:
                        add((frm, to, NORMAL))

        if not captures_only and self.castling:
            self._castle_moves(add, fog)
        return moves

    def _castle_moves(self, add, fog=False):
        side = self.side
        s = self.sq
        for king_from, king_to, rook_from, rook_to, bit in CASTLES[side]:
            if not self.castling & bit:
                continue
            if s[king_from] != KING * side or s[rook_from] != ROOK * side:
                continue
            if king_from in self.stuns or rook_from in self.stuns:
                continue  # a stunned king or rook can't move
            step = 1 if rook_from > king_from else -1
            if any(s[x] != EMPTY for x in range(king_from + step, rook_from, step)):
                continue
            # Not out of check, not through an attacked square. The landing square is
            # covered by the normal "doesn't leave the king in check" test.
            if not fog and (self.is_attacked(king_from, -side) or self.is_attacked(king_from + step, -side)):
                continue
            add((king_from, king_to, CASTLE))

    def legal_moves(self):
        moves = []
        side = self.side
        for m in self.pseudo_moves():
            self.make(m)
            if not self.is_attacked(self.king[side], -side):
                moves.append(m)
            self.unmake()
        return moves

    # ----------------------------------------------------------------- make / unmake

    def make(self, move):
        """Play a move in place (no legality check). Undo with unmake()."""
        frm, to, flag = move
        s = self.sq
        side = self.side
        piece = s[frm]
        cap_sq = to + 10 * side if flag == EN_PASSANT else to
        captured = s[cap_sq]

        self.undo_stack.append((move, captured, cap_sq, self.castling, self.ep, self.score,
                                self.hash, self.stuns, self.king[side]))

        score = self.score
        h = self.hash ^ ZOBRIST_CASTLE[self.castling] ^ ZOBRIST_EP[self.ep] ^ ZOBRIST_SIDE

        # Remove the mover and any captured piece
        score -= SCORE[piece + 6][frm]
        h ^= ZOBRIST[piece + 6][frm]
        if captured:
            score -= SCORE[captured + 6][cap_sq]
            h ^= ZOBRIST[captured + 6][cap_sq]
            s[cap_sq] = EMPTY
        s[frm] = EMPTY

        placed = PROMO_PIECE[flag] * side if flag >= PROMOTION else piece
        s[to] = placed
        score += SCORE[placed + 6][to]
        h ^= ZOBRIST[placed + 6][to]

        if flag == CASTLE:
            rook_from, rook_to = ROOK_CASTLE_MOVES[to]
            rook = s[rook_from]
            s[rook_from] = EMPTY
            s[rook_to] = rook
            score += SCORE[rook + 6][rook_to] - SCORE[rook + 6][rook_from]
            h ^= ZOBRIST[rook + 6][rook_from] ^ ZOBRIST[rook + 6][rook_to]

        if piece == KING * side:
            self.king[side] = to

        self.castling &= CASTLE_KEEP[frm] & CASTLE_KEEP[to]
        self.ep = frm + (to - frm) // 2 if flag == DOUBLE_PUSH else 0
        h ^= ZOBRIST_CASTLE[self.castling] ^ ZOBRIST_EP[self.ep]

        # Stuns: a captured piece takes its stun with it, and stuns count down once
        # per full round (after black moves). The dict is replaced, never mutated,
        # so unmake can restore the old one.
        stuns = self.stuns
        if stuns and ((captured and cap_sq in stuns) or side == BLACK):
            new = {}
            for st, n in stuns.items():
                if st == cap_sq and captured:
                    continue
                if side == BLACK:
                    n -= 1
                if n > 0:
                    new[st] = n
            self.stuns = new

        self.score = score
        self.hash = h
        self.side = -side

    def unmake(self):
        move, captured, cap_sq, castling, ep, score, h, stuns, king_sq = self.undo_stack.pop()
        frm, to, flag = move
        s = self.sq
        side = -self.side  # side that made the move
        self.side = side
        piece = PAWN * side if flag >= PROMOTION else s[to]
        s[frm] = piece
        s[to] = EMPTY
        s[cap_sq] = captured  # also restores an en passant victim (to stays empty)
        if flag == CASTLE:
            rook_from, rook_to = ROOK_CASTLE_MOVES[to]
            s[rook_from] = s[rook_to]
            s[rook_to] = EMPTY
        self.king[side] = king_sq
        self.castling = castling
        self.ep = ep
        self.score = score
        self.hash = h
        self.stuns = stuns

    # ----------------------------------------------------------------- helpers for clients

    def find_move(self, frm, to, promotion=QUEEN):
        """The legal move from `frm` to `to`, or None. `promotion` (a piece type) only
        matters when a pawn reaches the last rank."""
        for m in self.legal_moves():
            if m[0] == frm and m[1] == to and (m[2] < PROMOTION or PROMO_PIECE[m[2]] == promotion):
                return m
        return None

    def evaluate(self):
        """Score from white's point of view (positive = white is better)."""
        return self.score

    def full_evaluate(self):
        """Same as evaluate() but recomputed from scratch. Used by tests."""
        return sum(SCORE[self.sq[sq] + 6][sq] for sq in SQUARES if self.sq[sq])

    def stun(self, sq):
        """Stun whatever stands on `sq` for STUN_TURNS rounds. Returns False if the square is empty."""
        if self.sq[sq] == EMPTY:
            return False
        self.stuns = dict(self.stuns)
        self.stuns[sq] = self.STUN_TURNS
        return True

    def visible_squares(self, side):
        """Fog of War: the squares `side` can see, which are its own pieces plus every square
        those pieces could move to right now (captures included, so adjacent enemies show)."""
        seen = {sq for sq in SQUARES if self.sq[sq] * side > 0}
        saved_side, saved_ep = self.side, self.ep
        self.side = side
        if side != saved_side:
            self.ep = 0   # en passant belongs to the side to move only
        try:
            seen.update(m[1] for m in self.pseudo_moves(fog=True))
        finally:
            self.side, self.ep = saved_side, saved_ep
        return seen

    def strike(self, rng=random):
        """Lightning: stun a random piece for STUN_TURNS rounds. Returns its square, or None."""
        occupied = [sq for sq in SQUARES if self.sq[sq] != EMPTY]
        if not occupied:
            return None
        target = rng.choice(occupied)
        self.stuns = dict(self.stuns)
        self.stuns[target] = self.STUN_TURNS
        return target

    def display(self):
        print("   a b c d e f g h")
        for i, row in enumerate(self.rows()):
            print(f"{8 - i}| {' '.join(row)} |")
