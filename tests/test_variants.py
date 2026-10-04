"""Lightning forecasts and Fog of War."""
import json
import random

import pytest

from engine import Game
from engine.board import Board, SQUARES, WHITE, BLACK, parse_square as sq, square_name
from engine.game import HIDDEN
from server.app import create_app


class PickSquare:
    def __init__(self, name):
        self.square = sq(name)

    def choice(self, options):
        return self.square if self.square in options else options[0]


def play(game, *moves, color=None):
    for m in moves:
        frm, to = m.split("-")
        game.move(frm, to, color=color)


# ---------------------------------------------------------------------- forecast

FOUR_ROUNDS = ["e2-e4", "d7-d5", "a2-a3", "a7-a6", "b2-b3", "b7-b6", "c2-c3", "c7-c6"]


def test_strike_misses_when_the_piece_steps_away():
    g = Game("local", "lightning", strike_frequency=5, rng=PickSquare("g1"))
    play(g, *FOUR_ROUNDS)
    assert g.state()["forecast"] == "g1"
    play(g, "g1-f3", "h7-h6")                       # the knight leaves the cloud
    strike = [e for e in g.events if e["type"] == "lightning"][0]
    assert strike == {"type": "lightning", "square": "g1", "piece": None, "turns": 3, "missed": True}
    assert g.board.stuns == {}


def test_strike_hits_whoever_stands_there():
    # Forecast on white's e4 pawn; black captures onto e4 and gets stunned instead (a trap)
    g = Game("local", "lightning", strike_frequency=5, rng=PickSquare("e4"))
    play(g, *FOUR_ROUNDS)
    play(g, "h2-h3", "d5-e4")
    assert g.board.stuns == {sq("e4"): 3} and g.board.piece_char(sq("e4")) == "p"


def test_forecast_shown_in_review_and_part_of_repetition():
    g = Game("local", "lightning", strike_frequency=5, rng=PickSquare("h1"))
    key_before = g._position_key()
    play(g, *FOUR_ROUNDS)
    assert g.timeline()[-1]["forecast"] == "h1" and g.timeline()[-2]["forecast"] is None
    g.forecast = None
    plain = g._position_key()
    g.forecast = sq("h1")
    assert g._position_key() != plain and key_before != plain


def test_no_forecast_in_classic():
    g = Game("local", "classic", rng=PickSquare("h1"))
    play(g, *FOUR_ROUNDS, "a3-a4", "a6-a5")
    assert g.forecast is None and g.state()["forecast"] is None


def test_ai_steps_off_the_cloud():
    # The computer (black) has its queen under the cloud; the strike lands right after its move
    g = Game("ai", "lightning", difficulty=3, player_color="white", strike_frequency=5, rng=PickSquare("a1"))
    g.board = Board("3qk3/pppppppp/8/8/8/8/PPPPPPPP/4K2R w - - 0 1")
    g.positions = [g._position_key()]; g.hashes = [g.board.hash]
    g.rounds, g.forecast = 4, sq("d8")
    play(g, "h1-g1")
    g.ai_move()
    assert g.rounds == 5
    assert all(g.board.piece_char(s) != "q" for s in g.board.stuns), "AI left its queen under the cloud"


# ---------------------------------------------------------------------- fog of war: rules

def fog(mode="local", **kw):
    return Game(mode, "fog", **kw)


def test_fog_start_view():
    g = fog()
    view = g.state(viewer="white")
    assert view["board"][:4] == [HIDDEN * 8] * 4              # ranks 8-5 hidden
    assert view["board"][4] == " " * 8 and view["board"][7] == "RNBQKBNR"
    assert view["in_check"] is False and view["fog"] is True


def test_you_may_move_into_check_and_lose_the_king():
    g = fog()
    g.board = Board("4k3/8/8/8/8/8/3r4/4K3 w - - 0 1")
    play(g, "e1-e2")                                            # walks next to... nothing stops it
    play(g, "d2-e2")                                            # rook takes the king
    assert (g.status, g.reason, g.winner) == ("king_captured", "king_captured", "black")
    assert g.history[-1].endswith("#")


def test_no_stalemate_trap_in_fog():
    # Normally stalemate: black king has no safe move. In fog it must move into capture.
    g = fog()
    g.board = Board("k7/2Q5/1K6/8/8/8/8/8 b - - 0 1")
    assert g.status == "playing" and g.state(viewer="black")["legal_moves"]["a8"]


def test_castling_through_attack_allowed():
    g = fog()
    g.board = Board("4k3/8/8/8/8/5r2/8/R3K2R w KQ - 0 1")       # f1 is attacked
    assert "g1" in g.state()["legal_moves"]["e1"]


def test_ai_takes_the_king():
    g = fog("ai", difficulty=1, player_color="white")
    g.board = Board("4k3/8/8/8/8/8/3r4/4K3 w - - 0 1")
    play(g, "e1-e2")
    g.ai_move()
    assert g.reason == "king_captured" and g.winner == "black"


def test_fog_timeout_is_always_a_loss():
    class Clock:
        t = 1000.0

        def __call__(self):
            return self.t
    clock = Clock()
    g = fog(time_control="1+0", clock=clock)
    g.board = Board("4k3/8/8/8/8/8/8/2B1K3 w - - 0 1")
    g.positions = [g._position_key()]; g.hashes = [g.board.hash]
    play(g, "c1-d2")
    clock.t += 61
    g.check_time()
    assert (g.status, g.winner) == ("timeout", "white")


# ---------------------------------------------------------------------- fog of war: no leaks

def assert_view_is_clean(g, view, viewer):
    """Every visible square must really be visible, every other square hidden, and nothing
    else in the state may carry the opponent's secrets."""
    seen = g.board.visible_squares(WHITE if viewer == "white" else BLACK) if viewer else set()
    for s in SQUARES:
        r, c = divmod(s - 21, 10)
        shown = view["board"][r][c]
        if s in seen:
            assert shown == g.board.piece_char(s)
        else:
            assert shown == HIDDEN, f"{square_name(s)} leaked to {viewer}"
    for i, h in enumerate(view["history"]):
        mine = viewer == ("white" if i % 2 == 0 else "black")
        assert h == (g.history[i] if mine else "?")
    assert all(e["type"] == "game_over" or e.get("by") == viewer for e in view["events"])
    assert view["fifty_move_count"] is None and view["check_square"] is None
    if view["last_move"]:
        for v in view["last_move"].values():
            assert v is None or sq(v) in seen or viewer == g.events[0].get("by")
    for entry in view["timeline"]:
        assert set("".join(entry["board"])) <= set("?PNBRQKpnbrqk ")
    text = json.dumps(view)
    assert "positions" not in text and "snapshots" not in text


@pytest.mark.parametrize("seed", range(5))
def test_random_fog_games_never_leak(seed):
    rng = random.Random(seed)
    g = fog()
    for _ in range(80):
        if g.status != "playing":
            break
        for viewer in ("white", "black", None):
            assert_view_is_clean(g, g.state(viewer=viewer, can_move=(viewer == g.turn)), viewer)
        legal = g.state(viewer=g.turn)["legal_moves"]
        frm = rng.choice(sorted(legal))
        g.move(frm, rng.choice(legal[frm]))


def test_spectators_see_nothing_until_the_end():
    g = fog()
    play(g, "e2-e4")
    view = g.state(viewer=None)
    assert set("".join(view["board"])) == {HIDDEN} and view["history"] == ["?"]
    g.resign("black")
    view = g.state(viewer=None, have=5)
    assert view["board"] == g.board.rows() and view["history"] == ["e2-e4"]
    assert view["timeline_start"] == 0 and view["timeline"][1]["board"] == g.timeline()[1]["board"]


# ---------------------------------------------------------------------- fog over the API

@pytest.fixture
def client(tmp_path):
    return create_app({"DB_PATH": str(tmp_path / "fog.db"), "RATELIMIT_ENABLED": False,
                       "MAX_THINK_SECONDS": 0.3}).test_client()


def test_online_fog_each_player_gets_their_own_view(client):
    host = client.post("/api/games", json={"mode": "online", "variant": "fog", "color": "white"}).json
    guest = client.post(f"/api/games/{host['id']}/join", json={}).json
    gid = host["id"]
    assert host["state"]["board"][0] == HIDDEN * 8          # white can't see black's back rank
    assert guest["state"]["board"][7] == HIDDEN * 8          # black can't see white's
    r = client.post(f"/api/games/{gid}/moves", json={"from": "g1", "to": "f3"},
                    headers={"X-Player-Token": host["token"]})
    assert r.status_code == 200
    seen_by_black = client.get(f"/api/games/{gid}", headers={"X-Player-Token": guest["token"]}).json["state"]
    assert seen_by_black["history"] == ["?"] and seen_by_black["last_move"] is None
    assert seen_by_black["board"][5][5] == HIDDEN            # the knight on f3 is invisible to black
    spectator = client.get(f"/api/games/{gid}").json["state"]
    assert set("".join(spectator["board"])) == {HIDDEN}


def test_fog_vs_computer_over_api(client):
    r = client.post("/api/games", json={"mode": "ai", "variant": "fog", "difficulty": 1, "color": "black"})
    st = r.json["state"]
    assert st["history"] == ["?"] and st["board"][7] == HIDDEN * 8 and st["legal_moves"]


def test_finished_fog_game_survives_save_and_reload(client):
    """Regression: after a king capture the saved board has one king; loading it must still work."""
    host = client.post("/api/games", json={"mode": "online", "variant": "fog", "color": "white"}).json
    guest = client.post(f"/api/games/{host['id']}/join", json={}).json
    gid, ht, gt = host["id"], {"X-Player-Token": host["token"]}, {"X-Player-Token": guest["token"]}
    for (hf, hto), (gf, gto) in [(("f2", "f3"), ("e7", "e5")), (("g2", "g4"), ("d8", "h4")), (("a2", "a3"), ("h4", "e1"))]:
        assert client.post(f"/api/games/{gid}/moves", json={"from": hf, "to": hto}, headers=ht).status_code == 200
        assert client.post(f"/api/games/{gid}/moves", json={"from": gf, "to": gto}, headers=gt).status_code == 200
    r = client.get(f"/api/games/{gid}", headers=ht)
    assert r.status_code == 200
    st = r.json["state"]
    assert (st["status"], st["winner"]) == ("king_captured", "black")
    assert "K" not in "".join(st["board"]) and st["history"][-1] == "Qh4xe1#"   # everything revealed
    assert HIDDEN not in "".join(st["board"])


def test_strict_loading_still_rejects_missing_kings():
    with pytest.raises(ValueError):
        Board("8/8/8/8/8/8/8/4k3 w - - 0 1")
