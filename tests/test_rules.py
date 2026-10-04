"""Draw rules, clock, resign, undo and takebacks."""

import pytest

from engine import Game, IllegalMove
from engine.board import Board, parse_square as sq


class PickSquare:
    """Stand-in for the lightning RNG: always forecasts the given square."""
    def __init__(self, name):
        self.square = sq(name)

    def choice(self, options):
        return self.square if self.square in options else options[0]


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def play(game, *moves, color=None):
    for m in moves:
        frm, to = m.split("-")
        game.move(frm, to, color=color)


# 10 half-moves that never repeat a position
PAWN_MOVES = ["e2-e3", "e7-e6", "d2-d3", "d7-d6", "c2-c3", "c7-c6", "b2-b3", "b7-b6", "a2-a3", "a7-a6"]


def local(fen=None, **kw):
    g = Game("local", "classic", **kw)
    if fen:
        g.board = Board(fen)
        g.positions = [g._position_key()]
        g.hashes = [g.board.hash]
    return g


# ---------------------------------------------------------------------- draws

def test_threefold_repetition():
    g = local()
    knights = ["g1-f3", "g8-f6", "f3-g1", "f6-g8"]
    play(g, *knights)              # start position seen twice
    assert g.status == "playing"
    play(g, *knights)              # three times
    assert (g.status, g.reason, g.winner) == ("draw", "repetition", None)


def test_repetition_needs_same_side_to_move():
    g = local()
    play(g, "g1-f3", "g8-f6", "f3-g1", "f6-g8", "g1-f3", "g8-f6")
    assert g.status == "playing"


def test_repetition_ignores_unusable_en_passant_square():
    # After 1.e4 the e3 square is set, but no black pawn can capture there, so the
    # position counts as the same one that appears later without an e3 square.
    g = local("4k3/8/8/8/8/8/4P3/4K1N1 w - - 0 1")
    play(g, "e2-e4", "e8-d8", "g1-f3", "d8-e8", "f3-g1", "e8-d8", "g1-f3", "d8-e8", "f3-g1")
    # The position right after e4 comes back after moves 5 and 9: the third time is a draw
    assert (g.status, g.reason) == ("draw", "repetition")


def test_stuns_make_positions_different():
    g = Game("local", "lightning")
    key_plain = g._position_key()
    g.board.stuns[85] = 2
    assert g._position_key() != key_plain


def test_fifty_move_counter_resets_on_pawn_move_and_capture():
    g = local()
    play(g, "g1-f3", "g8-f6")
    assert g.halfmove == 2
    play(g, "e2-e4")
    assert g.halfmove == 0
    play(g, "f6-e4")               # capture
    assert g.halfmove == 0


def test_fifty_move_exact_trigger():
    g = local("4k3/8/8/8/8/8/8/R3K3 w - - 0 1")
    g.halfmove = 99
    play(g, "a1-a2")
    assert (g.status, g.reason) == ("draw", "fifty_move")


def test_checkmate_beats_fifty_move_rule():
    g = local("k7/8/1K6/8/8/8/8/7R w - - 0 1")
    g.halfmove = 99
    play(g, "h1-h8")
    assert g.reason == "checkmate"


@pytest.mark.parametrize("fen,draw", [
    ("4k3/8/8/8/8/8/8/4K3 w - - 0 1", True),           # K v K
    ("4k3/8/8/8/8/8/8/2B1K3 w - - 0 1", True),         # K+B v K
    ("4k3/8/8/8/8/8/8/1N2K3 w - - 0 1", True),         # K+N v K
    ("3bk3/8/8/8/8/8/8/2B1K3 w - - 0 1", True),        # bishops on the same color (d8, c1)
    ("4k1b1/8/8/8/8/8/8/2B1K3 w - - 0 1", False),      # bishops on different colors (g8, c1)
    ("4k3/8/8/8/8/8/8/1NN1K3 w - - 0 1", False),       # two knights: mate is possible
    ("4k3/8/8/8/8/8/4P3/4K3 w - - 0 1", False),        # a pawn
    ("1n2k3/8/8/8/8/8/8/1N2K3 w - - 0 1", False),      # knight each: mate possible
])
def test_insufficient_material(fen, draw):
    assert local(fen)._insufficient_material() is draw


def test_capture_into_insufficient_material_ends_game():
    g = local("4k3/8/8/8/8/8/3n4/4K3 w - - 0 1")
    play(g, "e1-d2")
    assert (g.status, g.reason) == ("draw", "insufficient_material")


# ---------------------------------------------------------------------- clock

def test_clock_starts_after_whites_first_move_and_adds_increment():
    clock = FakeClock()
    g = Game("local", "classic", time_control="3+2", clock=clock)
    clock.t += 30                         # white's first move is free
    play(g, "e2-e4")
    assert g.remaining("white") == 180_000
    clock.t += 10                         # black thinks 10 s
    assert g.clock_view()["black_ms"] == 170_000 and g.clock_view()["running"] == "black"
    play(g, "e7-e5")
    assert g.remaining("black") == 172_000   # 180 - 10 + 2 increment
    clock.t += 5
    assert g.remaining("white") == 175_000


def test_timeout_loses():
    clock = FakeClock()
    g = Game("local", "classic", time_control="1+0", clock=clock)
    play(g, "e2-e4")
    clock.t += 61
    with pytest.raises(IllegalMove, match="time ran out"):
        g.move("e7", "e5")
    assert (g.status, g.reason, g.winner) == ("timeout", "timeout", "white")
    assert g.clock_view()["black_ms"] == 0 and g.clock_view()["running"] is None


def test_check_time_without_a_move():
    clock = FakeClock()
    g = Game("local", "classic", time_control="1+0", clock=clock)
    play(g, "e2-e4")
    clock.t += 30
    assert g.check_time() is False
    clock.t += 31
    assert g.check_time() is True and g.winner == "white"


def test_timeout_vs_insufficient_material_is_a_draw():
    clock = FakeClock()
    g = Game("local", "classic", time_control="1+0", clock=clock)
    g.board = Board("4k3/8/8/8/8/8/4P3/2B1K3 b - - 0 1")     # white has a pawn: can mate
    g.history = ["x"]; g.snapshots = [None]; g.positions.append("p"); g.hashes.append(0)
    g.clock["running_since"] = clock.t
    clock.t += 61
    g.check_time()
    assert g.reason == "timeout"
    g2 = Game("local", "classic", time_control="1+0", clock=clock)
    g2.board = Board("4k3/8/8/8/8/8/8/2B1K3 b - - 0 1")      # white has only K+B
    g2.clock["running_since"] = clock.t
    clock.t += 61
    g2.check_time()
    assert (g2.status, g2.reason, g2.winner) == ("draw", "timeout_vs_insufficient", None)


def test_ai_respects_its_clock():
    clock = FakeClock()
    g = Game("ai", "classic", difficulty=6, player_color="white", time_control="1+0", clock=clock)
    play(g, "e2-e4")
    move, info = g.choose_ai_move()
    assert info["seconds"] < 60 / 30 + 0.5


# ---------------------------------------------------------------------- resign

def test_resign():
    g = local()
    g.resign("white")
    assert (g.status, g.reason, g.winner) == ("resigned", "resignation", "black")
    with pytest.raises(IllegalMove):
        g.resign("black")


# ---------------------------------------------------------------------- undo (vs computer only)

def ai_game(**kw):
    kw.setdefault("difficulty", 1)
    kw.setdefault("player_color", "white")
    return Game("ai", kw.pop("variant", "classic"), takebacks=kw.pop("takebacks", True), **kw)


def test_undo_vs_computer_takes_back_both_moves():
    g = ai_game()
    play(g, "e2-e4")
    g.ai_move()
    assert g.undo() == 2
    assert g.board.fen() == Board().fen() and g.history == [] and g.turn == "white"


def test_undo_needs_takebacks_switched_on():
    g = ai_game(takebacks=False)
    play(g, "e2-e4")
    g.ai_move()
    assert g.undo_plies() == 0 and g.state()["can_undo"] is False
    with pytest.raises(IllegalMove, match="takebacks are off"):
        g.undo()


@pytest.mark.parametrize("mode", ["local", "online"])
def test_no_undo_in_multiplayer(mode):
    g = Game(mode, "classic", takebacks=True)
    assert g.takebacks is False
    play(g, "e2-e4", color="white" if mode == "online" else None)
    with pytest.raises(IllegalMove):
        g.undo()


def test_undo_vs_computer_as_black_keeps_its_opening():
    g = ai_game(player_color="black")
    g.ai_move()
    assert g.undo_plies() == 0
    first = g.history[0]
    legal = g.state()["legal_moves"]
    frm = sorted(legal)[0]
    g.move(frm, legal[frm][0])
    g.ai_move()
    assert g.undo() == 2 and g.history == [first]


def test_undo_after_checkmate_vs_computer():
    g = ai_game()
    g.board = Board("k7/8/1K6/8/8/8/8/7R w - - 0 1")
    g.positions = [g._position_key()]; g.hashes = [g.board.hash]
    play(g, "h1-h8")
    assert g.reason == "checkmate"
    assert g.undo() == 1 and g.status == "playing"


def test_undo_restores_lightning_and_counters():
    g = ai_game(variant="lightning", strike_frequency=5, rng=PickSquare("h1"))
    for _ in range(5):
        legal = g.state()["legal_moves"]
        frm = sorted(legal)[0]
        g.move(frm, legal[frm][0])
        g.ai_move()
    assert g.rounds == 5 and g.board.stuns == {sq("h1"): 3}   # the forecast strike landed on h1
    halfmove, positions = g.snapshots[-2]["halfmove"], len(g.positions)
    g.undo()
    assert g.board.stuns == {} and g.rounds == 4 and len(g.history) == 8
    assert g.forecast == sq("h1")                              # the cloud is back
    assert g.halfmove == halfmove and len(g.positions) == positions - 2


def test_no_undo_after_resigning():
    g = ai_game()
    play(g, "e2-e4")
    g.ai_move()
    g.resign("white")
    assert g.undo_plies() == 0


def test_timeline_matches_history():
    g = local()
    play(g, "e2-e4", "e7-e5")
    t = g.timeline()
    assert len(t) == 3 and t[0]["board"][6] == "PPPPPPPP" and t[0]["last_move"] is None
    assert t[1]["last_move"] == {"from": "e2", "to": "e4"} and t[1]["board"][4][4] == "P"
    assert t[2]["board"] == g.board.rows()


def test_timeline_slices():
    g = local()
    play(g, "e2-e4", "e7-e5", "g1-f3")
    full = g.timeline()
    st = g.state(have=3)
    assert st["timeline_start"] == 2 and st["timeline"] == full[2:]       # last known entry resent
    assert g.state(have=0)["timeline"] == full
    assert g.state(have=99)["timeline_start"] == 3                          # client ahead (after an undo)


# ---------------------------------------------------------------------- AI and repetition

def test_ai_avoids_repetition_when_winning():
    # Black (AI) is a queen up. The only way back to an earlier position should be avoided.
    g = ai_game(difficulty=3)
    g.board = Board("6k1/5ppp/8/8/8/8/q4PPP/6K1 w - - 0 1")
    g.positions = [g._position_key()]; g.hashes = [g.board.hash]
    for _ in range(6):
        if g.status != "playing":
            break
        legal = g.state()["legal_moves"]
        frm = "g1" if "g1" in legal else ("f1" if "f1" in legal else sorted(legal)[0])
        to = legal[frm][0]
        g.move(frm, to)
        g.ai_move()
    assert g.reason != "repetition"


def test_save_round_trip_with_clock_and_snapshots():
    clock = FakeClock()
    g = ai_game(variant="lightning", time_control="5+0", clock=clock)
    play(g, "e2-e4")
    g.ai_move()
    h = Game.from_dict(g.to_dict(), clock=clock)
    assert h.state() == g.state()
    assert h.undo() == 2 and h.history == []
