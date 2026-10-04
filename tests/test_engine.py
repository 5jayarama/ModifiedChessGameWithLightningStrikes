"""Rules tests: perft against published counts, a random-game comparison with python-chess,
and the lightning/stun rules that standard chess doesn't have."""
import random

import pytest

from engine.board import Board, parse_square as sq, PROMO_PIECE, PROMOTION, QUEEN, ZOBRIST, ZOBRIST_SIDE, ZOBRIST_CASTLE, ZOBRIST_EP, SQUARES, BLACK


def perft(b, depth):
    n = 0
    side = b.side
    for m in b.pseudo_moves():
        b.make(m)
        if not b.is_attacked(b.king[side], -side):
            n += 1 if depth == 1 else perft(b, depth - 1)
        b.unmake()
    return n


# Published perft counts (chessprogramming.org "Perft Results"). Positions 4 and 5 are full of
# promotions, including under-promotions.
@pytest.mark.parametrize("fen,depth,expected", [
    ("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1", 4, 197281),
    ("r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1", 3, 97862),
    ("8/2p5/3p4/KP5r/1R3p1k/8/4P1P1/8 w - - 0 1", 5, 674624),
    ("r3k2r/Pppp1ppp/1b3nbN/nP6/BBP1P3/q4N2/Pp1P2PP/R2Q1RK1 w kq - 0 1", 4, 422333),
    ("rnbq1k1r/pp1Pbppp/2p5/8/2B5/8/PPP1NnPP/RNBQK2R w KQ - 1 8", 3, 62379),
])
def test_perft(fen, depth, expected):
    b = Board(fen)
    assert perft(b, depth) == expected
    assert b.fen() == Board(fen).fen()  # make/unmake restored everything


def _full_hash(b):
    h = 0
    for s in SQUARES:
        if b.sq[s]:
            h ^= ZOBRIST[b.sq[s] + 6][s]
    if b.side == BLACK:
        h ^= ZOBRIST_SIDE
    return h ^ ZOBRIST_CASTLE[b.castling] ^ ZOBRIST_EP[b.ep]


def test_matches_python_chess_on_random_games():
    chess = pytest.importorskip("chess")

    def rc(s):
        return 21 + (7 - chess.square_rank(s)) * 10 + chess.square_file(s)

    def key(m):  # (from, to, promotion piece type or 0); python-chess and this engine share 2..5
        return rc(m.from_square), rc(m.to_square), m.promotion or 0

    fens = [chess.STARTING_FEN,
            "r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1",
            "r3k2r/Pppp1ppp/1b3nbN/nP6/BBP1P3/q4N2/Pp1P2PP/R2Q1RK1 w kq - 0 1",
            "rnbq1k1r/pp1Pbppp/2p5/8/2B5/8/PPP1NnPP/RNBQK2R w KQ - 1 8"]
    rng = random.Random(7)
    for game_no in range(300):
        fen = fens[game_no % len(fens)]
        ref, b = chess.Board(fen), Board(fen)
        for _ in range(120):
            expected = {key(m) for m in ref.legal_moves}
            got = {(m[0], m[1], PROMO_PIECE[m[2]] if m[2] >= PROMOTION else 0) for m in b.legal_moves()}
            assert got == expected, ref.fen()
            assert b.score == b.full_evaluate()   # incremental eval stays exact
            assert b.hash == _full_hash(b)        # incremental hash stays exact
            if not expected:
                break
            choice = rng.choice(list(ref.legal_moves))
            ref.push(choice)
            b.make(b.find_move(rc(choice.from_square), rc(choice.to_square), choice.promotion or QUEEN))
            # python-chess omits the en passant square when no capture is possible,
            # so compare placement, side and castling (en passant is covered by legal moves)
            assert b.fen().split()[:3] == ref.fen().split()[:3]


def test_stunned_piece_cannot_move_and_does_not_attack():
    b = Board("4k3/8/8/8/8/8/4r3/4K3 w - - 0 1")
    b.stuns[sq("e2")] = 2
    assert not b.in_check()                        # stunned rook gives no check
    b.side = BLACK
    assert all(m[0] != sq("e2") for m in b.legal_moves())


def test_stunned_pieces_attack_flag():
    b = Board("4k3/8/8/8/8/8/4r3/4K3 w - - 0 1")
    b.stuns[sq("e2")] = 2
    Board.STUNNED_PIECES_ATTACK = True
    try:
        assert b.in_check()
    finally:
        Board.STUNNED_PIECES_ATTACK = False


def test_stun_lasts_three_full_rounds():
    b = Board()
    b.stuns[sq("a2")] = Board.STUN_TURNS
    for white, black in [("e2", "e4"), ("e7", "e5")], [("d2", "d4"), ("d7", "d6")], [("g1", "f3"), ("g8", "f6")]:
        assert b.find_move(sq("a2"), sq("a3")) is None
        b.make(b.find_move(sq(white[0]), sq(white[1])))
        b.make(b.find_move(sq(black[0]), sq(black[1])))
    assert sq("a2") not in b.stuns
    assert b.find_move(sq("a2"), sq("a3")) is not None


def test_capturing_a_stunned_piece_removes_the_stun():
    b = Board("4k3/8/8/8/8/5p2/8/4K1N1 w - - 0 1")
    b.stuns[sq("f3")] = 3
    b.make(b.find_move(sq("g1"), sq("f3")))
    assert sq("f3") not in b.stuns
    b.unmake()
    assert b.stuns == {sq("f3"): 3}               # unmake restores it


def test_en_passant_capture_of_stunned_pawn_removes_the_stun():
    b = Board("4k3/3p4/8/4P3/8/8/8/4K3 b - - 0 1")
    b.make(b.find_move(sq("d7"), sq("d5")))
    b.stuns[sq("d5")] = 2
    b.make(b.find_move(sq("e5"), sq("d6")))
    assert sq("d5") not in b.stuns and b.sq[sq("d5")] == 0


def test_castling_rules():
    b = Board("4k3/8/8/8/8/5r2/8/R3K2R w KQ - 0 1")
    assert b.find_move(sq("e1"), sq("g1")) is None      # through an attacked square
    assert b.find_move(sq("e1"), sq("c1")) is not None
    b = Board("4k3/8/8/8/8/4r3/8/R3K2R w KQ - 0 1")
    assert b.find_move(sq("e1"), sq("c1")) is None      # out of check
    b = Board("r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1")
    b.stuns[sq("h1")] = 2
    assert b.find_move(sq("e1"), sq("g1")) is None      # stunned rook
    b = Board("r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1")
    for a, c in [("e1", "e2"), ("a8", "a7"), ("e2", "e1"), ("a7", "a8")]:
        b.make(b.find_move(sq(a), sq(c)))
    assert b.find_move(sq("e1"), sq("g1")) is None      # king moved and came back
    b.make(b.find_move(sq("h1"), sq("h2")))
    assert b.find_move(sq("e8"), sq("c8")) is None      # rook moved and came back
    assert b.find_move(sq("e8"), sq("g8")) is not None  # the other side is unaffected


def test_strike_always_hits_a_piece():
    b = Board("8/8/8/8/8/8/8/K6k w - - 0 1")
    hits = {b.strike(random.Random(i)) for i in range(50)}
    assert hits == {sq("a1"), sq("h1")}


def test_save_and_load_round_trip():
    b = Board()
    b.make(b.find_move(sq("e2"), sq("e4")))
    b.stuns[sq("d7")] = 2
    c = Board.from_dict(b.to_dict())
    assert c.fen() == b.fen() and c.stuns == b.stuns and c.score == b.score and c.hash == b.hash


@pytest.mark.parametrize("bad", [
    {"fen": "not a fen"},
    {"fen": "8/8/8/8/8/8/8/8 w - - 0 1"},                                     # no kings
    {"fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1", "stuns": {"e4": 2}},  # empty square
    {"fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1", "stuns": {"z9": 2}},
])
def test_from_dict_rejects_bad_data(bad):
    with pytest.raises(ValueError):
        Board.from_dict(bad)
