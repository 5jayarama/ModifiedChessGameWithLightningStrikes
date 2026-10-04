import random

import pytest

from engine import Game, IllegalMove
from engine.board import Board, parse_square as sq


def play(game, *moves, color=None):
    for m in moves:
        frm, to = m.split("-")
        game.move(frm, to, color=color)


def test_classic_never_strikes():
    g = Game("local", "classic", rng=random.Random(1))
    for _ in range(30):
        st = g.state()
        frm = sorted(st["legal_moves"])[0]
        g.move(frm, st["legal_moves"][frm][0])
        if g.status != "playing":
            break
    assert g.board.stuns == {}
    assert not any(e["type"] == "lightning" for e in g.events)
    assert g.state()["next_strike_in"] is None


class PickSquare:
    """Stand-in for the lightning RNG: always forecasts the given square."""
    def __init__(self, name):
        self.square = sq(name)

    def choice(self, options):
        assert self.square in options
        return self.square


def test_lightning_strikes_on_schedule():
    g = Game("local", "lightning", strike_frequency=5, rng=PickSquare("h1"))
    play(g, "e2-e3", "e7-e6", "d2-d3", "d7-d6", "c2-c3", "c7-c6", "b2-b3", "b7-b6")
    assert g.rounds == 4 and g.forecast == sq("h1")          # forecast one round ahead
    assert g.events[-1] == {"type": "forecast", "square": "h1", "piece": "R"}
    assert g.state()["forecast"] == "h1" and g.board.stuns == {}
    play(g, "a2-a3", "a7-a6")
    assert g.rounds == 5 and g.board.stuns == {sq("h1"): 3} and g.forecast is None
    assert g.events[-1]["type"] == "lightning" and g.events[-1]["missed"] is False


def test_player_black_ai_moves_first():
    g = Game("ai", "classic", difficulty=1, player_color="black")
    assert g.is_ai_turn()
    assert g.state()["legal_moves"] == {}           # not the human's turn yet
    g.ai_move()
    assert g.turn == "black" and len(g.history) == 1
    assert g.state()["legal_moves"]                  # now it is
    with pytest.raises(IllegalMove):
        g.move("e2", "e4")                           # can't move the AI's pieces


def test_ai_mode_human_cannot_move_for_ai():
    g = Game("ai", "classic", difficulty=1, player_color="white")
    play(g, "e2-e4")
    with pytest.raises(IllegalMove, match="not your turn"):
        g.move("e7", "e5")


def test_local_mode_both_sides_move():
    g = Game("local", "classic")
    play(g, "e2-e4", "e7-e5", "g1-f3")
    assert g.history == ["e2-e4", "e7-e5", "Ng1-f3"]


def test_online_mode_checks_the_seat():
    g = Game("online", "classic")
    with pytest.raises(IllegalMove):
        g.move("e2", "e4", color="black")
    g.move("e2", "e4", color="white")
    with pytest.raises(IllegalMove):
        g.move("e7", "e5", color="white")
    g.move("e7", "e5", color="black")
    assert g.state(can_move=False)["legal_moves"] == {}


@pytest.mark.parametrize("choice,piece", [("q", "Q"), ("r", "R"), ("b", "B"), ("n", "N")])
def test_promotion_choice(choice, piece):
    g = Game("local", "classic")
    g.board = Board("8/P7/8/8/8/8/8/k6K w - - 0 1")
    g.move("a7", "a8", promotion=choice)
    assert g.board.piece_char(21) == piece
    assert g.history[-1].endswith("=" + piece) or g.history[-1].endswith("=" + piece + "+")


def test_promotion_listed_once_per_square():
    g = Game("local", "classic")
    g.board = Board("1n6/P7/8/8/8/8/8/k6K w - - 0 1")
    assert sorted(g.state()["legal_moves"]["a7"]) == ["a8", "b8"]


def test_bad_promotion_letter():
    g = Game("local", "classic")
    g.board = Board("8/P7/8/8/8/8/8/k6K w - - 0 1")
    with pytest.raises(IllegalMove):
        g.move("a7", "a8", promotion="k")


def test_ai_promotes():
    g = Game("ai", "classic", difficulty=2, player_color="black")
    g.board = Board("8/P7/8/8/8/8/7k/K7 w - - 0 1")
    g.ai_move()
    assert g.board.piece_char(21) == "Q"


def test_stunned_message_names_turns():
    g = Game("local", "lightning")
    g.board.stuns[sq("e2")] = 2
    with pytest.raises(IllegalMove, match="stunned for 2 more turns"):
        g.move("e2", "e4")


def test_save_round_trip_keeps_settings():
    g = Game("ai", "lightning", difficulty=3, strike_frequency=10, player_color="black")
    g.ai_move()
    h = Game.from_dict(g.to_dict())
    assert h.state() == g.state()


@pytest.mark.parametrize("kwargs", [
    {"mode": "solo"}, {"variant": "blitz"}, {"mode": "ai", "difficulty": 9},
    {"mode": "ai", "player_color": "red"}, {"variant": "lightning", "strike_frequency": 3},
])
def test_bad_settings(kwargs):
    with pytest.raises(ValueError):
        Game(**kwargs)
