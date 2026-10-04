"""Local pygame version of Chess with Lightning Strikes, running on the shared engine.

Run from the repo root:  python -m pygame_client.game
Pieces: the bundled "maestro" set in pygame_client/pieces/, or your own images in
pygame_client/images1/ (or ./images1/) if present.

1 player: you against the computer (pick your color). 2 players: hot-seat on this computer.
Online play is in the web version.

Keys: Left/Right/Home/End review moves, Esc cancels a premove or promotion, U undo (vs computer
with takebacks on), N new game once a game is over.
"""
import math
import random
import sys
import threading
from pathlib import Path

import pygame

from engine import Game, IllegalMove, square_name
from engine.board import Board
from engine.game import TIME_CONTROLS

SQ = 64                      # 8 * 64 = 512: the board is a 512 x 512 square on the left
BOARD = 8 * SQ
PANEL = 240                  # side panel: clocks, moves, buttons
WIDTH, HEIGHT = BOARD + PANEL, BOARD
BANNER_MS = 2500             # how long the lightning message stays up (the game keeps running)
CONFIRM_MS = 3000            # resign / new game need a second click within this time
LOW_TIME_MS = 20_000
HERE = Path(__file__).resolve().parent
# Your own images first (images1/, as in the original game), then the bundled maestro set
IMAGE_DIRS = [HERE / "images1", Path.cwd() / "images1", HERE / "pieces"]
PIECE_FILES = {'r': 'bR', 'n': 'bN', 'b': 'bB', 'q': 'bQ', 'k': 'bK', 'p': 'bP',
               'R': 'wR', 'N': 'wN', 'B': 'wB', 'Q': 'wQ', 'K': 'wK', 'P': 'wP'}
YELLOW, WHITE_TXT, GREY, DIM = (255, 255, 0), (255, 255, 255), (200, 200, 200), (120, 124, 130)
PANEL_BG, BOX_BG, RED = (27, 30, 35), (40, 44, 51), (230, 80, 70)

VARIANT_KEYS = {"Classic": "classic", "Lightning": "lightning", "Fog of War": "fog"}
FOG_SQUARE, CLOUD = (43, 47, 54), (214, 226, 255)
RESULTS = {
    "stalemate": "Draw by stalemate",
    "repetition": "Draw by threefold repetition",
    "fifty_move": "Draw by the 50-move rule",
    "insufficient_material": "Draw by insufficient material",
    "timeout_vs_insufficient": "Draw: time ran out, no mating material",
}


def load_images():
    folder = next((d for d in IMAGE_DIRS if (d / "wK.png").exists()), None)
    if folder is None:
        sys.exit("Piece images not found: pygame_client/pieces/ is missing from this copy of the project.")
    return {piece: pygame.transform.smoothscale(pygame.image.load(str(folder / f"{name}.png")), (SQ, SQ))
            for piece, name in PIECE_FILES.items()}


def format_clock(ms):
    ms = max(0, ms)
    minutes, seconds = divmod(int(ms // 1000), 60)
    if ms < LOW_TIME_MS:
        return f"{minutes}:{seconds:02d}.{int(ms % 1000) // 100}"
    return f"{minutes}:{seconds:02d}"


# ---------------------------------------------------------------------- start screen options

class Option:
    def __init__(self, label, values, index, shown=lambda opts: True, describe=str):
        self.label, self.values, self.index = label, values, index
        self.shown, self.describe = shown, describe

    @property
    def value(self):
        return self.values[self.index]

    def step(self, delta):
        self.index = (self.index + delta) % len(self.values)


def difficulty_text(level):
    if level == 0:
        return "Dynamic (adapts to you)"
    if level == 6:
        return "6: as deep as 5 s allow"
    return f"{level}: looks {level + 1} half-moves ahead"


def make_options():
    one_player = lambda opts: opts["players"].index == 0   # noqa: E731
    o = {}
    o["players"] = Option("Players", ["1 player vs computer", "2 players, this computer"], 0)
    o["variant"] = Option("Game mode", ["Classic", "Lightning", "Fog of War"], 1)
    o["time"] = Option("Time control", [None] + list(TIME_CONTROLS), 0,
                       describe=lambda tc: "No clock" if tc is None else tc.replace("+", " | ") + " (min | sec)")
    o["difficulty"] = Option("Difficulty", list(range(0, 7)), 2, shown=one_player, describe=difficulty_text)
    o["color"] = Option("You play as", ["White", "Black", "Random"], 0, shown=one_player)
    o["takebacks"] = Option("Takebacks", ["Off", "On (U to undo)"], 0, shown=one_player)
    o["frequency"] = Option("Lightning every", list(range(5, 51, 5)), 2,
                            shown=lambda opts: opts["variant"].value == "Lightning",
                            describe=lambda n: f"{n} rounds")
    return o


# ---------------------------------------------------------------------- the game window

class ChessGame:
    def __init__(self):
        self.screen = pygame.display.set_mode((WIDTH, HEIGHT))
        pygame.display.set_caption("Chess with Lightning Strikes")
        self.images = load_images()
        self.font = pygame.font.Font(None, 28)
        self.small = pygame.font.Font(None, 22)
        self.clock_font = pygame.font.Font(None, 40)
        self.options = make_options()
        self.reset_view()

    def reset_view(self):
        self.game = None
        self.selected = None
        self.flipped = False
        self.banner = []
        self.banner_until = 0
        self.promotion = None        # (from, to) while the promotion picker is open
        self.ai_thread = None
        self.ai_result = None
        self.premove = None          # (from, to) to play as soon as the computer has moved
        self.premove_from = None
        self.review = None           # timeline index being reviewed, None = live
        self.timeline_cache = (None, [])
        self.armed = None            # (button name, deadline) for two-click confirmations
        self.revealed_at = -1        # same-device Fog of War: move count the current player revealed
        self.buttons = {}
        self.move_rects = []

    # ------------------------------------------------------------------ coordinates

    def rect_for(self, name):
        col, row = "abcdefgh".index(name[0]), 8 - int(name[1])
        if self.flipped:
            row, col = 7 - row, 7 - col
        return pygame.Rect(col * SQ, row * SQ, SQ, SQ)

    def square_at(self, x, y):
        col, row = x // SQ, y // SQ
        if not (0 <= row < 8 and 0 <= col < 8):
            return None
        if self.flipped:
            row, col = 7 - row, 7 - col
        return square_name(21 + row * 10 + col)

    def my_color(self):
        return self.game.player_color if self.game.mode == "ai" else None

    def viewer(self):
        """Whose eyes the board is drawn for (matters in Fog of War): yours vs the computer,
        the player to move on a shared computer."""
        return self.game.player_color if self.game.mode == "ai" else self.game.turn

    def state(self):
        # have=huge: skip the move-review timeline, it is only built while reviewing
        return self.game.state(have=10**9, viewer=self.viewer())

    def curtained(self):
        """Same-device Fog of War: hide the board until the next player is ready."""
        g = self.game
        return (g.mode == "local" and g.variant == "fog" and g.status == "playing"
                and self.revealed_at != len(g.history))

    def timeline(self):
        """All positions so far, rebuilt only when the number of moves changes."""
        key = (id(self.game), len(self.game.history), self.game.history[-1:] if self.game.history else None,
               self.viewer(), self.game.status)
        if self.timeline_cache[0] != key:
            self.timeline_cache = (key, self.game.timeline(viewer=self.viewer()))
        return self.timeline_cache[1]

    # ------------------------------------------------------------------ drawing

    def draw(self):
        state = self.state()
        if self.game.mode == "local" and self.game.variant == "fog" and state["status"] == "playing":
            self.flipped = state["turn"] == "black"   # each player sees the board from their side
        live = self.review is None
        if live:
            board, last, stuns, check = state["board"], state["last_move"], state["stuns"], state["check_square"]
            forecast = state["forecast"]
        else:
            entry = self.timeline()[self.review]
            board, last, stuns, check = entry["board"], entry["last_move"], entry["stuns"], entry["check_square"]
            forecast = entry["forecast"]
        last = last or {}
        targets = set(state["legal_moves"].get(self.selected, [])) if live and self.selected else set()
        premove_squares = {self.premove_from} | set(self.premove or ())
        for r in range(8):
            for c in range(8):
                name = square_name(21 + r * 10 + c)
                rect = self.rect_for(name)
                color = pygame.Color("dark green") if (r + c) % 2 == 0 else pygame.Color("light gray")
                piece = board[r][c]
                if piece == "?":
                    color = FOG_SQUARE   # Fog of War: this square can't be seen
                pygame.draw.rect(self.screen, color, rect)
                if name == forecast:
                    self._draw_storm(rect)
                if name in (last.get("from"), last.get("to")):
                    self._tint(rect, (255, 210, 63, 90))
                if live and name == self.selected:
                    self._tint(rect, (255, 210, 63, 150))
                if live and name in premove_squares:
                    self._tint(rect, (214, 54, 54, 140))
                if name == check:
                    pygame.draw.rect(self.screen, (220, 30, 30), rect, 4)
                if piece not in " ?":
                    self.screen.blit(self.images[piece], rect.topleft)
                if name == forecast:
                    self._draw_cloud(rect)
                if name in targets:
                    pygame.draw.circle(self.screen, (255, 0, 0), rect.center, 15)
        for name, turns in stuns.items():
            self._draw_bolt(self.rect_for(name), turns)
        if not live:
            self._tint(pygame.Rect(0, 0, BOARD, BOARD), (0, 0, 0, 50))
        if self.curtained():
            pygame.draw.rect(self.screen, (28, 31, 37), (0, 0, BOARD, BOARD))
            self._draw_text([f"Pass the computer to {state['turn'].capitalize()}", "Click the board when ready"],
                            top=BOARD // 2 - 16, size=34)
        if pygame.time.get_ticks() < self.banner_until:
            self._draw_text(self.banner, top=20)
        if self.promotion:
            self._draw_promotion_picker()
        if state["status"] != "playing" and live:
            self._draw_text([self.result_text(state), "Press N for a new game"], top=BOARD // 2 - 20, size=34)
        self.draw_panel(state)
        pygame.display.set_caption(f"Chess with Lightning Strikes - {self.status_text(state)}")
        pygame.display.flip()

    def draw_panel(self, state):
        x0 = BOARD
        pygame.draw.rect(self.screen, PANEL_BG, (x0, 0, PANEL, HEIGHT))
        bottom = self.viewer() if self.game.variant == "fog" else (self.my_color() or "white")
        top = "black" if bottom == "white" else "white"
        self._draw_player_bar(top, 8, state)
        self._draw_player_bar(bottom, HEIGHT - 48, state)

        # move list
        if self.review is not None:
            label, color = "REVIEWING (End returns)", YELLOW
        else:
            label, color = "MOVES (arrows to review)", DIM
        self.screen.blit(self.small.render(label, True, color), (x0 + 12, 60))
        history = [] if self.curtained() else state["history"]   # keep the next player's moves covered
        current = self.review if self.review is not None else len(history)
        rows = (len(history) + 1) // 2
        visible = 11
        first_row = max(0, min(rows - visible, (max(current, 1) - 1) // 2 - visible + 2))
        self.move_rects = []
        for i in range(first_row, min(rows, first_row + visible)):
            y = 80 + (i - first_row) * 22
            self.screen.blit(self.small.render(f"{i + 1}.", True, DIM), (x0 + 12, y + 3))
            for k, ply in enumerate((2 * i, 2 * i + 1)):
                if ply >= len(history):
                    break
                rect = pygame.Rect(x0 + 44 + k * 94, y, 90, 21)
                if current == ply + 1:
                    pygame.draw.rect(self.screen, (70, 60, 20), rect, border_radius=4)
                self.screen.blit(self.small.render(history[ply], True, WHITE_TXT), (rect.x + 4, rect.y + 3))
                self.move_rects.append((rect, ply + 1))

        # status and buttons
        status = self.status_text(state)
        for i, line in enumerate(self._wrap(status, PANEL - 24)[:2]):
            self.screen.blit(self.small.render(line, True, YELLOW), (x0 + 12, 336 + i * 18))
        self.buttons = {}
        playing = state["status"] == "playing"
        row = []
        if self.game.mode == "ai" and self.game.takebacks:
            row.append(("undo", "Undo", state["can_undo"] and self.ai_thread is None))
        row.append(("resign", "Resign", playing))
        width = (PANEL - 24 - 8 * (len(row) - 1)) // len(row)
        for i, (key, label, enabled) in enumerate(row):
            self._button(key, label, pygame.Rect(x0 + 12 + i * (width + 8), 378, width, 32), enabled)
        self._button("new", "New game", pygame.Rect(x0 + 12, 418, PANEL - 24, 32), True)

    def _button(self, key, label, rect, enabled):
        armed = self.armed and self.armed[0] == key and pygame.time.get_ticks() < self.armed[1]
        if armed:
            label = "Click again to confirm"
        bg = RED if armed else BOX_BG
        pygame.draw.rect(self.screen, bg, rect, border_radius=6)
        text = self.small.render(label, True, WHITE_TXT if enabled else DIM)
        self.screen.blit(text, text.get_rect(center=rect.center))
        if enabled:
            self.buttons[key] = rect

    def _draw_player_bar(self, color, y, state):
        x0 = BOARD
        if self.game.mode == "ai":
            name = "You" if color == self.game.player_color else f"Computer ({difficulty_text(self.game.difficulty).split(':')[0]})"
        else:
            name = color.capitalize()
        self.screen.blit(self.small.render(name, True, GREY), (x0 + 12, y + 13))
        clock = state["clock"]
        if not clock:
            return
        ms = clock[f"{color}_ms"]
        running = clock["running"] == color
        rect = pygame.Rect(x0 + PANEL - 112, y, 100, 40)
        bg = ((255, 90, 78) if ms < LOW_TIME_MS else (236, 235, 230)) if running else BOX_BG
        fg = (20, 20, 20) if running else ((255, 143, 134) if ms < LOW_TIME_MS else GREY)
        pygame.draw.rect(self.screen, bg, rect, border_radius=6)
        text = self.clock_font.render(format_clock(ms), True, fg)
        self.screen.blit(text, text.get_rect(midright=(rect.right - 8, rect.centery)))

    def _wrap(self, text, width):
        words, lines, line = text.split(), [], ""
        for w in words:
            trial = f"{line} {w}".strip()
            if self.small.size(trial)[0] > width and line:
                lines.append(line)
                line = w
            else:
                line = trial
        return lines + [line]

    def result_text(self, state):
        reason, winner = state["reason"], state["winner"]
        if reason in RESULTS:
            return RESULTS[reason]
        if self.game.mode == "ai":
            who = "You win" if winner == self.game.player_color else "The computer wins"
        else:
            who = f"{winner.capitalize()} wins"
        return {"checkmate": f"{who} by checkmate!", "timeout": f"{who} on time!",
                "resignation": f"{who} by resignation", "king_captured": f"{who}: king captured!"}.get(reason, "Game over")

    def status_text(self, state):
        if state["status"] != "playing":
            return self.result_text(state)
        check = "Check! " if state["in_check"] else ""
        if self.game.mode == "ai":
            if state["turn"] == self.game.player_color:
                return f"{check}Your move"
            return "Computer is thinking" + (" (premove set)" if self.premove else "")
        return f"{check}{state['turn'].capitalize()} to move"

    def _draw_storm(self, rect):
        """Forecast square: diagonal storm stripes under the piece."""
        overlay = pygame.Surface(rect.size, pygame.SRCALPHA)
        for i in range(-SQ, SQ, 12):
            pygame.draw.line(overlay, (60, 80, 120, 150), (i, SQ), (i + SQ, 0), 6)
        self.screen.blit(overlay, rect.topleft)
        pygame.draw.rect(self.screen, (143, 167, 214), rect, 3)

    def _draw_cloud(self, rect):
        """A small cloud in the corner of the forecast square."""
        x, y = rect.x + 6, rect.y + 6
        for dx, dy, radius in ((8, 9, 7), (16, 6, 8), (24, 9, 7)):
            pygame.draw.circle(self.screen, CLOUD, (x + dx, y + dy), radius)
        pygame.draw.rect(self.screen, CLOUD, (x + 6, y + 9, 20, 7))
        pygame.draw.polygon(self.screen, YELLOW, [(x + 17, y + 14), (x + 12, y + 24), (x + 17, y + 22), (x + 14, y + 31),
                                                   (x + 23, y + 19), (x + 18, y + 20), (x + 21, y + 14)])

    def _tint(self, rect, rgba):
        overlay = pygame.Surface(rect.size, pygame.SRCALPHA)
        overlay.fill(rgba)
        self.screen.blit(overlay, rect.topleft)

    def _draw_text(self, lines, top, size=28):
        font = pygame.font.Font(None, size)
        for i, line in enumerate(lines):  # font.render ignores "\n", so one line at a time
            text = font.render(line, True, YELLOW)
            rect = text.get_rect(center=(BOARD // 2, top + i * (size + 4)))
            pygame.draw.rect(self.screen, (0, 0, 0), rect.inflate(16, 6))
            self.screen.blit(text, rect)

    def _draw_bolt(self, rect, turns):
        """Lightning bolt made of two triangles, rotated 30 degrees (the original design)."""
        cx, cy = rect.center
        h, w, off = SQ * 0.4, SQ * 0.1, SQ * 0.15
        pts = [(cx, cy - h), (cx + w, cy), (cx - w, cy), (cx + off, cy + h), (cx + off + w, cy), (cx + off - w, cy)]
        a = math.radians(-30)
        rotated = [((x - cx) * math.cos(a) - (y - cy) * math.sin(a) + cx,
                    (x - cx) * math.sin(a) + (y - cy) * math.cos(a) + cy) for x, y in pts]
        pygame.draw.polygon(self.screen, YELLOW, rotated)
        self.screen.blit(self.font.render(str(turns), True, YELLOW), (rect.right - 16, rect.top + 2))

    # ------------------------------------------------------------------ promotion picker

    def promotion_boxes(self):
        """Four boxes in the middle of the board: queen, rook, bishop, knight."""
        start = BOARD // 2 - 2 * SQ - 12
        return [(p, pygame.Rect(start + i * (SQ + 8), BOARD // 2 - SQ // 2, SQ, SQ)) for i, p in enumerate("qrbn")]

    def _draw_promotion_picker(self):
        self._tint(pygame.Rect(0, 0, BOARD, BOARD), (0, 0, 0, 150))
        self._draw_text(["Promote to (Esc to cancel)"], top=BOARD // 2 - SQ)
        white = self.game.turn == "white"
        for p, rect in self.promotion_boxes():
            pygame.draw.rect(self.screen, pygame.Color("light gray"), rect, border_radius=8)
            pygame.draw.rect(self.screen, YELLOW, rect, 2, border_radius=8)
            self.screen.blit(self.images[p.upper() if white else p], rect.topleft)

    # ------------------------------------------------------------------ start screen

    def show_start_screen(self):
        """Pick players, Classic or Lightning, clock, difficulty, color, takebacks, frequency."""
        big, medium = pygame.font.Font(None, 44), pygame.font.Font(None, 30)
        button = pygame.Rect(WIDTH // 2 - 100, 456, 200, 44)
        selection = 0
        clock = pygame.time.Clock()
        while True:
            visible = [o for o in self.options.values() if o.shown(self.options)]
            selection = min(selection, len(visible) - 1)
            self.screen.fill(pygame.Color("black"))
            title = big.render("Chess with Lightning Strikes", True, YELLOW)
            self.screen.blit(title, title.get_rect(center=(WIDTH // 2, 36)))
            hint = self.small.render("UP/DOWN to choose, LEFT/RIGHT (or click) to change, ENTER to start", True, GREY)
            self.screen.blit(hint, hint.get_rect(center=(WIDTH // 2, 72)))
            rows = []
            for i, opt in enumerate(visible):
                y = 92 + i * 48
                color = YELLOW if i == selection else WHITE_TXT
                self.screen.blit(self.small.render(opt.label.upper(), True, GREY), (120, y))
                self.screen.blit(medium.render(f"<  {opt.describe(opt.value)}  >", True, color), (120, y + 17))
                rows.append((pygame.Rect(100, y - 4, WIDTH - 200, 46), opt))
            notes = {"Lightning": "A storm cloud marks the strike square one round ahead",
                     "Fog of War": "No check: capture the king to win" + (
                         ". The computer sees through the fog" if self.options["players"].index == 0 else
                         ". The board is covered between turns")}
            note = notes.get(self.options["variant"].value)
            if note:
                text = self.small.render(note, True, GREY)
                self.screen.blit(text, text.get_rect(center=(WIDTH // 2, 438)))
            pygame.draw.rect(self.screen, (0, 128, 0), button, border_radius=6)
            label = medium.render("START GAME", True, WHITE_TXT)
            self.screen.blit(label, label.get_rect(center=button.center))

            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    pygame.quit()
                    sys.exit()
                if event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_UP:
                        selection = (selection - 1) % len(visible)
                    elif event.key == pygame.K_DOWN:
                        selection = (selection + 1) % len(visible)
                    elif event.key in (pygame.K_LEFT, pygame.K_RIGHT):
                        visible[selection].step(1 if event.key == pygame.K_RIGHT else -1)
                    elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                        return self.settings()
                if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    if button.collidepoint(event.pos):
                        return self.settings()
                    for i, (rect, opt) in enumerate(rows):
                        if rect.collidepoint(event.pos):
                            selection = i
                            opt.step(-1 if event.pos[0] < WIDTH // 2 else 1)  # left half goes back
            pygame.display.flip()
            clock.tick(30)

    def settings(self):
        o = self.options
        color = o["color"].value.lower()
        if color == "random":
            color = random.choice(["white", "black"])
        return {
            "mode": "ai" if o["players"].index == 0 else "local",
            "variant": VARIANT_KEYS[o["variant"].value],
            "difficulty": o["difficulty"].value,
            "player_color": color,
            "strike_frequency": o["frequency"].value,
            "time_control": o["time"].value,
            "takebacks": o["takebacks"].index == 1,
        }

    # ------------------------------------------------------------------ main loop

    def run(self):
        clock = pygame.time.Clock()
        while True:
            if self.game is None:
                self.reset_view()
                self.game = Game(**self.show_start_screen())
                self.flipped = self.game.mode == "ai" and self.game.player_color == "black"
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    pygame.quit()
                    sys.exit()
                if event.type == pygame.KEYDOWN:
                    self.on_key(event.key)
                if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    self.on_click(*event.pos)
                if event.type == pygame.MOUSEBUTTONDOWN and event.button == 3:
                    self.cancel_premove()   # right-click cancels a premove
                if self.game is None:
                    break
            if self.game is None:
                continue
            if self.game.check_time():          # the flag can fall while nobody clicks
                self.handle_events()
            self.update_ai()
            self.draw()
            clock.tick(30)  # don't spin the CPU while waiting for input

    def on_key(self, key):
        if key == pygame.K_ESCAPE:
            self.promotion = None
            self.cancel_premove()
        elif key == pygame.K_n and self.game.status != "playing":
            self.game = None
        elif key == pygame.K_u:
            self.undo()
        elif key == pygame.K_LEFT:
            self.set_review((self.review if self.review is not None else len(self.game.history)) - 1)
        elif key == pygame.K_RIGHT:
            self.set_review((self.review if self.review is not None else len(self.game.history)) + 1)
        elif key == pygame.K_HOME:
            self.set_review(0)
        elif key == pygame.K_END:
            self.set_review(None)

    def set_review(self, index):
        if self.curtained():
            return   # reviewing would show the next player's view early
        last = len(self.game.history)
        self.review = None if index is None or index >= last else max(0, index)
        self.selected = None

    def update_ai(self):
        """Run the AI on a background thread so the window keeps responding while it thinks."""
        if self.ai_thread is None and self.game.is_ai_turn():
            board = self.game.board.copy()   # the thread only ever touches its own copy
            self.ai_result = None

            def think():
                self.ai_result = self.game.choose_ai_move(board=board)

            self.ai_thread = threading.Thread(target=think, daemon=True)
            self.ai_thread.start()
        elif self.ai_thread is not None and not self.ai_thread.is_alive():
            self.ai_thread = None
            move, info = self.ai_result
            self.game.apply_ai_move(move, info)
            if move is not None and info:
                print(f"AI: {self.game.history[-1]} (level {info['level']}, depth {info['depth']}, {info['seconds']} s)")
            self.handle_events()
            self.try_premove()

    # ------------------------------------------------------------------ input

    def on_click(self, x, y):
        if x >= BOARD:
            return self.on_panel_click(x, y)
        if self.promotion:
            for piece, rect in self.promotion_boxes():
                if rect.collidepoint(x, y):
                    self.play(*self.promotion, piece)
                    break
            self.promotion = None
            return
        if self.curtained():
            self.revealed_at = len(self.game.history)   # the next player is ready
            return
        if self.review is not None:
            self.review = None   # clicking the board while reviewing returns to the game
            return
        state = self.state()
        name = self.square_at(x, y)
        if name is None:
            return
        if not state["legal_moves"]:
            if self.game.mode == "ai" and self.game.status == "playing":
                self.premove_click(state, name)   # the computer is thinking: premove
            return
        row, col = 8 - int(name[1]), "abcdefgh".index(name[0])
        piece = state["board"][row][col]
        own = piece not in " ?" and (piece.isupper() == (state["turn"] == "white"))
        if self.selected and name in state["legal_moves"].get(self.selected, []):
            mover = state["board"][8 - int(self.selected[1])]["abcdefgh".index(self.selected[0])]
            if mover in "Pp" and name[1] in "18":
                self.promotion = (self.selected, name)   # ask which piece first
            else:
                self.play(self.selected, name)
            self.selected = None
        elif own and name != self.selected:
            if name in state["stuns"]:
                print(f"This piece is stunned for {state['stuns'][name]} more turns!")
            self.selected = name  # stunned pieces get selected but show no moves
        else:
            self.selected = None

    def premove_click(self, state, name):
        row, col = 8 - int(name[1]), "abcdefgh".index(name[0])
        piece = state["board"][row][col]
        own = piece not in " ?" and (piece.isupper() == (self.game.player_color == "white"))
        if self.premove and not self.premove_from:
            self.cancel_premove()
            if not own:
                return
        if self.premove_from is None:
            if own:
                self.premove_from = name
        elif name == self.premove_from:
            self.premove_from = None
        elif own:
            self.premove_from = name
        else:
            self.premove = (self.premove_from, name)
            self.premove_from = None

    def cancel_premove(self):
        self.premove = None
        self.premove_from = None

    def try_premove(self):
        """Play the premove as soon as it's the player's turn, if it is legal by then."""
        if not self.premove:
            return
        frm, to = self.premove
        self.cancel_premove()
        if to in self.state()["legal_moves"].get(frm, []):
            self.play(frm, to)   # premoved promotions become queens

    def on_panel_click(self, x, y):
        for rect, ply in self.move_rects:
            if rect.collidepoint(x, y):
                return self.set_review(ply)
        for key, rect in self.buttons.items():
            if not rect.collidepoint(x, y):
                continue
            if key == "undo":
                return self.undo()
            # resign and new game (mid-game) need a second click within CONFIRM_MS
            needs_confirm = key == "resign" or (key == "new" and self.game.status == "playing")
            armed = self.armed and self.armed[0] == key and pygame.time.get_ticks() < self.armed[1]
            if needs_confirm and not armed:
                self.armed = (key, pygame.time.get_ticks() + CONFIRM_MS)
                return
            self.armed = None
            if key == "resign":
                color = self.game.player_color if self.game.mode == "ai" else self.game.turn
                self.game.resign(color)
                self.cancel_premove()
                self.handle_events()
            elif key == "new":
                self.game = None

    def undo(self):
        if self.game.mode != "ai" or self.ai_thread is not None:
            return
        try:
            self.game.undo()
        except IllegalMove as e:
            print(e)
            return
        self.cancel_premove()
        self.review = None
        self.selected = None

    def play(self, frm, to, promotion="q"):
        try:
            self.game.move(frm, to, promotion=promotion)
        except IllegalMove as e:
            print(e)
            return
        self.handle_events()

    def handle_events(self):
        for e in self.game.events:
            if e["type"] == "forecast":
                self.banner = [f"Storm cloud over {e['square']}!", "Lightning strikes there at the end of next round."]
                self.banner_until = pygame.time.get_ticks() + BANNER_MS
            elif e["type"] == "lightning" and e["missed"]:
                self.banner = [f"Lightning strikes {e['square']}", "and hits nothing."]
                self.banner_until = pygame.time.get_ticks() + BANNER_MS
            elif e["type"] == "lightning":
                self.banner = [f"Lightning strikes at {e['square']}!",
                               f"The piece will be stunned for {Board.STUN_TURNS} turns."]
                self.banner_until = pygame.time.get_ticks() + BANNER_MS
                print(f"Lightning strikes the {e['piece']} at {e['square']}!")
            elif e["type"] == "stun_expired":
                print(f"The {e['piece']} at {e['square']} is no longer stunned!")
        self.game.events = []   # each event is shown once


def main():
    pygame.init()
    ChessGame().run()


if __name__ == "__main__":
    main()
