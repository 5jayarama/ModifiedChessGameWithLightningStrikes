import random
import time

import pytest

from engine.board import Board, parse_square as sq
from engine.search import LEVELS, MATE, Searcher


def best(fen, depth=4, seconds=5.0, **kw):
    return Searcher(depth, seconds, rng=random.Random(0), **kw).best_move(Board(fen))


def test_finds_mate_in_one():
    move, info = best("8/8/8/8/8/5kq1/8/7K b - - 0 1")
    b = Board("8/8/8/8/8/5kq1/8/7K b - - 0 1")
    b.make(move)
    assert b.in_check() and not b.legal_moves()
    assert info["score"] <= -(MATE - 10)          # white's perspective: black mates


def test_finds_mate_in_two():
    # Back-rank mate with doubled rooks: 1...Re1+ 2.Rxe1 Rxe1#
    fen = "4r1k1/4rppp/8/8/8/8/5PPP/3R2K1 b - - 0 1"
    move, info = best(fen, depth=4)
    assert move == (sq("e7"), sq("e1"), 0)
    assert info["score"] == -(MATE - 3)            # mate on the 3rd half-move


def test_stalemate_is_a_draw_not_a_loss():
    # Black to move has no legal moves and is not in check
    move, info = best("k7/2Q5/8/8/8/8/8/6K1 b - - 0 1")
    assert move is None


def test_avoids_stalemating_when_winning():
    # White queen + king vs king. Qc7?? stalemates; any decent search avoids it.
    fen = "k7/8/1K6/8/8/8/8/2Q5 w - - 0 1"
    move, info = best(fen, depth=3)
    b = Board(fen)
    b.make(move)
    assert b.legal_moves() or b.in_check()


def test_quiescence_avoids_hanging_the_queen():
    # Black queen on d5 attacked by the c4 pawn. At depth 1 without quiescence the search
    # can't see the recapture pattern; with it, the queen gets out of the way.
    fen = "rnb1kbnr/ppp1pppp/8/3q4/2P5/8/PP1P1PPP/RNBQKBNR b KQkq - 0 3"
    move, _ = best(fen, depth=1)
    assert move[0] == sq("d5")


def test_respects_time_limit():
    fen = "r1bq1rk1/pp2bppp/2n1pn2/3p4/2PP4/2N1PN2/PP3PPP/R2QKB1R b KQ - 0 8"
    start = time.perf_counter()
    move, info = Searcher(64, 0.5, rng=random.Random(0)).best_move(Board(fen))
    elapsed = time.perf_counter() - start
    assert move is not None and info["depth"] >= 2
    assert elapsed < 0.5 + 0.3                     # one node-check interval of slack


def test_search_does_not_change_the_board():
    b = Board("r1bq1rk1/pp2bppp/2n1pn2/3p4/2PP4/2N1PN2/PP3PPP/R2QKB1R b KQ - 0 8")
    b.stuns[sq("c6")] = 2
    before = (b.fen(), dict(b.stuns), b.score, b.hash)
    Searcher(3, 1.0).best_move(b)
    assert (b.fen(), b.stuns, b.score, b.hash) == before


def test_stunned_pieces_are_respected_in_search():
    # Black queen could take the undefended rook, but it is stunned
    b = Board("4k3/8/8/3q4/8/8/3R4/4K3 b - - 0 1")
    b.stuns[sq("d5")] = 3
    move, _ = Searcher(3, 1.0).best_move(b)
    assert move[0] != sq("d5")


@pytest.mark.parametrize("level", sorted(LEVELS))
def test_every_level_answers_within_its_budget(level):
    fen = "r1bq1rk1/pp2bppp/2n1pn2/3p4/2PP4/2N1PN2/PP3PPP/R2QKB1R b KQ - 0 8"
    depth, seconds = LEVELS[level]
    start = time.perf_counter()
    move, _ = Searcher.for_level(level, max_seconds=1.0).best_move(Board(fen))
    assert move is not None
    assert time.perf_counter() - start < min(seconds, 1.0) + 0.3
