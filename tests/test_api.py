import json

import pytest

from server.app import create_app


@pytest.fixture
def app(tmp_path):
    return create_app({"DB_PATH": str(tmp_path / "test.db"), "MAX_THINK_SECONDS": 0.3,
                       "RATELIMIT_ENABLED": False})


@pytest.fixture
def client(app):
    return app.test_client()


def new_game(client, **body):
    r = client.post("/api/games", json=body or {"difficulty": 1, "strike_frequency": 5})
    assert r.status_code == 201, r.json
    return r.json["id"], r.json["state"]


def test_create_and_read_game(client):
    gid, state = new_game(client)
    assert state["turn"] == "white" and state["status"] == "playing"
    assert sorted(state["legal_moves"]["e2"]) == ["e3", "e4"]
    r = client.get(f"/api/games/{gid}")
    assert r.status_code == 200 and r.json["state"]["board"] == state["board"]


def test_move_gets_ai_reply(client):
    gid, _ = new_game(client)
    r = client.post(f"/api/games/{gid}/moves", json={"from": "e2", "to": "e4"})
    assert r.status_code == 200
    state = r.json["state"]
    assert state["turn"] == "white" and len(state["history"]) == 2
    assert state["history"][0] == "e2-e4"
    assert state["ai"]["depth"] >= 1


def test_illegal_move_returns_422_with_state(client):
    gid, _ = new_game(client)
    r = client.post(f"/api/games/{gid}/moves", json={"from": "e2", "to": "e5"})
    assert r.status_code == 422
    assert r.json["error"] == "illegal move" and r.json["state"]["turn"] == "white"


def test_moving_opponent_piece_is_rejected(client):
    gid, _ = new_game(client)
    r = client.post(f"/api/games/{gid}/moves", json={"from": "e7", "to": "e5"})
    assert r.status_code == 422


@pytest.mark.parametrize("body", [
    {"from": "e2"},                                  # missing field
    {"from": "e2", "to": "e4", "extra": 1},          # unknown field
    {"from": 5, "to": "e4"},                         # wrong type
    {"from": "e2", "to": "e44"},
    {"from": "E2", "to": "e4"},
    {"from": "e2", "to": "i4"},
    ["e2", "e4"],
])
def test_bad_move_input(client, body):
    gid, _ = new_game(client)
    r = client.post(f"/api/games/{gid}/moves", json=body)
    assert r.status_code in (400, 422)
    assert "error" in r.json


@pytest.mark.parametrize("body", [
    {"difficulty": 7, "strike_frequency": 15},
    {"difficulty": -1, "strike_frequency": 15},
    {"difficulty": True, "strike_frequency": 15},
    {"difficulty": 2.0, "strike_frequency": 15},
    {"difficulty": "2", "strike_frequency": 15},
    {"difficulty": 2, "strike_frequency": 4},
    {"difficulty": 2, "strike_frequency": 51},
    {"difficulty": 2, "strike_frequency": 15, "seed": 1},
])
def test_bad_new_game_input(client, body):
    r = client.post("/api/games", json=body)
    assert r.status_code == 400 and "error" in r.json


def test_non_json_and_oversized_bodies(client):
    assert client.post("/api/games", data="difficulty=2").status_code == 400
    r = client.post("/api/games", data=json.dumps({"difficulty": 2, "pad": "x" * 5000}),
                    content_type="application/json")
    assert r.status_code == 413


@pytest.mark.parametrize("gid", ["nope", "../../etc/passwd", "A" * 22, "x" * 200])
def test_unknown_game_is_404(client, gid):
    assert client.get(f"/api/games/{gid}").status_code == 404
    # "../" paths get normalized away from /api/ entirely; any refusal is fine there
    r = client.post(f"/api/games/{gid}/moves", json={"from": "e2", "to": "e4"})
    assert r.status_code in ((404,) if "/" not in gid else (404, 405))


def test_state_cannot_be_injected_by_client(client):
    """The client never sends a board, so it can't place pieces or pick lightning targets."""
    gid, _ = new_game(client)
    r = client.post(f"/api/games/{gid}/moves",
                    json={"from": "e2", "to": "e4", "board": ["QQQQKQQQ"] * 8})
    assert r.status_code == 400


def test_concurrent_save_is_rejected(app, client):
    gid, _ = new_game(client)
    store = app.extensions["game_store"]
    game, version, seats = store.load(gid)
    assert store.save(gid, game, version, seats) is True
    assert store.save(gid, game, version, seats) is False   # stale version


def test_security_headers_and_no_store(client):
    gid, _ = new_game(client)
    r = client.get(f"/api/games/{gid}")
    assert "default-src 'self'" in r.headers["Content-Security-Policy"]
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["Cache-Control"] == "no-store"


def test_static_path_traversal_blocked(client):
    assert client.get("/../server/app.py").status_code == 404
    assert client.get("/%2e%2e/server/app.py").status_code == 404


def test_rate_limit(tmp_path):
    app = create_app({"DB_PATH": str(tmp_path / "rl.db"), "LIMIT_NEW_GAME": "3 per minute"})
    c = app.test_client()
    codes = [c.post("/api/games", json={"difficulty": 1, "strike_frequency": 5}).status_code for _ in range(5)]
    assert codes == [201, 201, 201, 429, 429]


def test_store_full(tmp_path):
    app = create_app({"DB_PATH": str(tmp_path / "full.db"), "MAX_ACTIVE_GAMES": 2, "RATELIMIT_ENABLED": False})
    c = app.test_client()
    codes = [c.post("/api/games", json={"difficulty": 1, "strike_frequency": 5}).status_code for _ in range(3)]
    assert codes == [201, 201, 503]


def test_ai_time_cap(tmp_path):
    import time
    app = create_app({"DB_PATH": str(tmp_path / "cap.db"), "MAX_THINK_SECONDS": 0.2, "RATELIMIT_ENABLED": False})
    c = app.test_client()
    gid = c.post("/api/games", json={"difficulty": 6, "strike_frequency": 50}).json["id"]
    start = time.perf_counter()
    r = c.post(f"/api/games/{gid}/moves", json={"from": "e2", "to": "e4"})
    assert r.status_code == 200
    assert time.perf_counter() - start < 1.0          # level 6 alone would think for 5 s


def test_full_game_until_it_ends(client):
    """Play legal moves through the API (first legal move each turn) and check it stays consistent."""
    gid, state = new_game(client, difficulty=1, strike_frequency=5)
    for _ in range(150):
        if state["status"] != "playing":
            break
        frm = sorted(state["legal_moves"])[0]
        r = client.post(f"/api/games/{gid}/moves", json={"from": frm, "to": state["legal_moves"][frm][0]})
        assert r.status_code == 200, r.json
        state = r.json["state"]
        for sq_name in state["stuns"]:
            col, row = "abcdefgh".index(sq_name[0]), 8 - int(sq_name[1])
            assert state["board"][row][col] != " "   # stuns always sit on a piece
    assert state["rounds"] > 5


# ---------------------------------------------------------------------- modes

def test_ai_mode_player_black_gets_ai_opening(client):
    gid, state = new_game(client, mode="ai", variant="classic", difficulty=1, color="black")
    assert len(state["history"]) == 1 and state["turn"] == "black"
    assert state["legal_moves"] and state["settings"]["player_color"] == "black"


def test_random_color(client):
    colors = {new_game(client, mode="ai", difficulty=1, color="random")[1]["settings"]["player_color"]
              for _ in range(30)}
    assert colors == {"white", "black"}


def test_classic_variant_has_no_lightning(client):
    gid, state = new_game(client, mode="local", variant="classic")
    assert state["next_strike_in"] is None and state["settings"]["strike_frequency"] is None


def test_local_mode_both_sides(client):
    gid, _ = new_game(client, mode="local", variant="classic")
    for frm, to in [("e2", "e4"), ("e7", "e5"), ("g1", "f3")]:
        r = client.post(f"/api/games/{gid}/moves", json={"from": frm, "to": to})
        assert r.status_code == 200, r.json
    assert r.json["state"]["history"] == ["e2-e4", "e7-e5", "Ng1-f3"]
    assert r.json["state"]["ai"] is None


def test_promotion_over_api(app, client):
    gid, _ = new_game(client, mode="local", variant="classic")
    store = app.extensions["game_store"]
    game, version, seats = store.load(gid)
    from engine.board import Board
    game.board = Board("8/P7/8/8/8/8/8/k6K w - - 0 1")
    store.save(gid, game, version, seats)
    r = client.post(f"/api/games/{gid}/moves", json={"from": "a7", "to": "a8", "promotion": "n"})
    assert r.status_code == 200 and r.json["state"]["board"][0][0] == "N"


@pytest.mark.parametrize("promotion", ["k", "queen", 1, None, ""])
def test_bad_promotion_value(client, promotion):
    gid, _ = new_game(client, mode="local", variant="classic")
    r = client.post(f"/api/games/{gid}/moves", json={"from": "e2", "to": "e4", "promotion": promotion})
    assert r.status_code == 422


# ---------------------------------------------------------------------- online

def online_pair(client, color="white"):
    r = client.post("/api/games", json={"mode": "online", "variant": "classic", "color": color})
    assert r.status_code == 201
    host = r.json
    r = client.post(f"/api/games/{host['id']}/join", json={})
    assert r.status_code == 200, r.json
    return host, r.json


def move(client, gid, frm, to, token=None):
    headers = {"X-Player-Token": token} if token else {}
    return client.post(f"/api/games/{gid}/moves", json={"from": frm, "to": to}, headers=headers)


def test_online_waits_for_opponent(client):
    r = client.post("/api/games", json={"mode": "online", "variant": "classic", "color": "white"})
    host = r.json
    assert host["you"] == "white" and host["seats"] == {"white": True, "black": False}
    assert host["state"]["legal_moves"] == {}             # can't move until someone joins
    r = move(client, host["id"], "e2", "e4", host["token"])
    assert r.status_code == 422 and "waiting" in r.json["error"]


def test_online_full_flow(client):
    host, guest = online_pair(client, "white")
    gid = host["id"]
    assert guest["you"] == "black" and guest["token"] != host["token"]
    assert move(client, gid, "e7", "e5", guest["token"]).status_code == 422   # not black's turn
    assert move(client, gid, "e2", "e4", guest["token"]).status_code == 422   # black can't move white
    r = move(client, gid, "e2", "e4", host["token"])
    assert r.status_code == 200 and r.json["state"]["legal_moves"] == {}    # now waiting on black
    # black polls and sees the move, with legal moves for black
    r = client.get(f"/api/games/{gid}", headers={"X-Player-Token": guest["token"]})
    assert r.json["state"]["history"] == ["e2-e4"] and r.json["state"]["legal_moves"]
    assert r.json["you"] == "black" and "token" not in r.json
    assert move(client, gid, "e7", "e5", guest["token"]).status_code == 200


def test_online_host_can_pick_black(client):
    host, guest = online_pair(client, "black")
    assert host["you"] == "black" and guest["you"] == "white"
    assert move(client, host["id"], "e2", "e4", guest["token"]).status_code == 200


def test_online_third_person_cannot_join_or_move(client):
    host, guest = online_pair(client)
    gid = host["id"]
    r = client.post(f"/api/games/{gid}/join", json={})
    assert r.status_code == 409
    assert move(client, gid, "e2", "e4").status_code == 422                     # no token
    assert move(client, gid, "e2", "e4", "x" * 32).status_code == 422           # wrong token
    assert move(client, gid, "e2", "e4", "bad token!").status_code == 422       # malformed
    r = client.get(f"/api/games/{gid}")                                          # spectators can watch
    assert r.status_code == 200 and r.json["you"] is None and r.json["state"]["legal_moves"] == {}


def test_tokens_are_stored_hashed(app, client):
    host, guest = online_pair(client)
    _, _, seats = app.extensions["game_store"].load(host["id"])
    assert host["token"] not in seats.values() and guest["token"] not in seats.values()
    assert all(len(v) == 64 for v in seats.values())


def test_join_rejected_for_non_online_games(client):
    gid, _ = new_game(client, mode="local", variant="classic")
    assert client.post(f"/api/games/{gid}/join", json={}).status_code == 400


# ---------------------------------------------------------------------- clock, resign, undo, long-poll

def test_time_control_validation(client):
    for bad in ["4+0", 5, True, "5+0 "]:
        r = client.post("/api/games", json={"mode": "local", "time_control": bad})
        assert r.status_code == 400
    r = client.post("/api/games", json={"mode": "local", "time_control": "3+2"})
    clock = r.json["state"]["clock"]
    assert clock["white_ms"] == clock["black_ms"] == 180_000 and clock["running"] is None


def test_clock_runs_after_first_move(client):
    gid = client.post("/api/games", json={"mode": "local", "time_control": "5+0"}).json["id"]
    r = client.post(f"/api/games/{gid}/moves", json={"from": "e2", "to": "e4"})
    assert r.json["state"]["clock"]["running"] == "black"


def flag_game(app, gid, seconds_ago):
    """Pretend the side to move started thinking `seconds_ago` seconds ago."""
    store = app.extensions["game_store"]
    game, version, seats = store.load(gid)
    game.clock["running_since"] -= seconds_ago
    store.save(gid, game, version, seats)


def test_flag_falls_on_read(app, client):
    gid = client.post("/api/games", json={"mode": "local", "variant": "classic", "time_control": "1+0"}).json["id"]
    client.post(f"/api/games/{gid}/moves", json={"from": "e2", "to": "e4"})
    flag_game(app, gid, 61)
    st = client.get(f"/api/games/{gid}").json["state"]
    assert (st["status"], st["reason"], st["winner"]) == ("timeout", "timeout", "white")
    r = client.post(f"/api/games/{gid}/moves", json={"from": "e7", "to": "e5"})
    assert r.status_code == 422


def test_flag_falls_when_moving_late(app, client):
    gid = client.post("/api/games", json={"mode": "local", "variant": "classic", "time_control": "1+0"}).json["id"]
    client.post(f"/api/games/{gid}/moves", json={"from": "e2", "to": "e4"})
    flag_game(app, gid, 61)
    r = client.post(f"/api/games/{gid}/moves", json={"from": "e7", "to": "e5"})
    assert r.status_code == 422 and r.json["error"] == "time ran out"
    assert client.get(f"/api/games/{gid}").json["state"]["status"] == "timeout"   # saved


def test_resign_modes(client):
    gid, _ = new_game(client, mode="ai", difficulty=1, color="white")
    r = client.post(f"/api/games/{gid}/resign", json={})
    assert r.json["state"]["winner"] == "black" and r.json["state"]["reason"] == "resignation"
    assert client.post(f"/api/games/{gid}/resign", json={}).status_code == 422   # already over

    gid, _ = new_game(client, mode="local", variant="classic")
    client.post(f"/api/games/{gid}/moves", json={"from": "e2", "to": "e4"})
    r = client.post(f"/api/games/{gid}/resign", json={})                           # black to move resigns
    assert r.json["state"]["winner"] == "white"


def test_online_resign_needs_a_seat(client):
    host, guest = online_pair(client)
    assert client.post(f"/api/games/{host['id']}/resign", json={}).status_code == 403
    r = client.post(f"/api/games/{host['id']}/resign", json={}, headers={"X-Player-Token": guest["token"]})
    assert r.json["state"]["winner"] == "white"     # guest is black, resigned out of turn


def test_undo_only_vs_computer_with_takebacks(client):
    gid, _ = new_game(client, mode="ai", difficulty=1, takebacks=True)
    r = client.post(f"/api/games/{gid}/moves", json={"from": "e2", "to": "e4"})
    assert r.json["state"]["can_undo"] is True
    r = client.post(f"/api/games/{gid}/undo", json={})
    assert r.status_code == 200 and r.json["state"]["history"] == []

    gid, _ = new_game(client, mode="ai", difficulty=1)                          # takebacks default off
    client.post(f"/api/games/{gid}/moves", json={"from": "e2", "to": "e4"})
    r = client.post(f"/api/games/{gid}/undo", json={})
    assert r.status_code == 422 and "off" in r.json["error"]

    gid, _ = new_game(client, mode="local", variant="classic", takebacks=True)
    client.post(f"/api/games/{gid}/moves", json={"from": "e2", "to": "e4"})
    assert client.post(f"/api/games/{gid}/undo", json={}).status_code == 400


def test_timeline_slices_over_api(client):
    gid, state = new_game(client, mode="local", variant="classic")
    assert state["timeline_start"] == 0 and len(state["timeline"]) == 1
    r = client.post(f"/api/games/{gid}/moves?have=1", json={"from": "e2", "to": "e4"})
    assert r.json["state"]["timeline_start"] == 0 and len(r.json["state"]["timeline"]) == 2
    r = client.get(f"/api/games/{gid}?have=2")
    assert r.json["state"]["timeline_start"] == 1 and len(r.json["state"]["timeline"]) == 1
    assert client.get(f"/api/games/{gid}?have=abc").status_code == 400


@pytest.fixture
def lp_app(tmp_path):
    return create_app({"DB_PATH": str(tmp_path / "lp.db"), "MAX_THINK_SECONDS": 0.3,
                       "RATELIMIT_ENABLED": False, "LONG_POLL_SECONDS": 1.0})


def test_long_poll_returns_204_when_nothing_happens(lp_app):
    import time
    c = lp_app.test_client()
    r = c.post("/api/games", json={"mode": "online", "variant": "classic"})
    gid, version = r.json["id"], r.json["version"]
    start = time.perf_counter()
    r = c.get(f"/api/games/{gid}?wait=1&since={version}")
    assert r.status_code == 204 and 0.9 < time.perf_counter() - start < 1.6


def test_long_poll_wakes_on_opponent_move(lp_app):
    import threading
    import time
    c = lp_app.test_client()
    host, guest = online_pair(c)
    gid = host["id"]
    version = c.get(f"/api/games/{gid}", headers={"X-Player-Token": guest["token"]}).json["version"]
    result = {}

    def wait_for_move():
        t = time.perf_counter()
        r = lp_app.test_client().get(f"/api/games/{gid}?wait=1&since={version}",
                                      headers={"X-Player-Token": guest["token"]})
        result.update(code=r.status_code, after=time.perf_counter() - t, body=r.json)

    th = threading.Thread(target=wait_for_move)
    th.start()
    time.sleep(0.4)
    move(c, gid, "e2", "e4", host["token"])
    th.join(3)
    assert result["code"] == 200 and result["body"]["state"]["history"] == ["e2-e4"]
    assert 0.35 < result["after"] < 0.8              # answered right after the move, not at the timeout
    assert result["body"]["state"]["legal_moves"]     # black may move now


def test_long_poll_wakes_when_flag_falls(lp_app):
    c = lp_app.test_client()
    r = c.post("/api/games", json={"mode": "local", "variant": "classic", "time_control": "1+0"})
    gid = r.json["id"]
    r = c.post(f"/api/games/{gid}/moves", json={"from": "e2", "to": "e4"})
    flag_game(lp_app, gid, 59.7)                     # black has 0.3 s left
    r = c.get(f"/api/games/{gid}?wait=1&since={r.json['version'] + 1}")
    assert r.status_code == 200 and r.json["state"]["reason"] == "timeout"


def test_many_workers_initialising_a_new_database_at_once(tmp_path):
    """Regression: gunicorn workers start together, and switching a new database to WAL mode
    used to fail with 'database is locked' for all but one of them."""
    import threading
    from server.store import GameStore
    errors = []
    for run in range(15):              # the race is intermittent, so try several fresh databases
        path = str(tmp_path / f"race{run}.db")
        barrier = threading.Barrier(8)

        def boot():
            barrier.wait()
            try:
                GameStore(path)
            except Exception as e:      # noqa: BLE001 - the test reports any failure
                errors.append(e)

        threads = [threading.Thread(target=boot) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    assert errors == []
