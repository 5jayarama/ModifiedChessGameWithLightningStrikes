from ai import Heuristics
from boardModified import ChessBoard
import pygame
import sys
import math


class ChessGame:
    def __init__(self):
        self.sq_size = 63
        self.load_images()
        self.selected_piece = None
        self.possible_moves = []
        self.screen = pygame.display.set_mode((512, 512))
        pygame.display.set_caption("Chess Game")
        self.board = ChessBoard()
    
    def load_images(self):
        piece_map = {
            'r': 'bR', 'n': 'bN', 'b': 'bB', 'q': 'bQ', 'k': 'bK', 'p': 'bP',
            'R': 'wR', 'N': 'wN', 'B': 'wB', 'Q': 'wQ', 'K': 'wK', 'P': 'wP'
        }
        self.images = {}
        for piece, file_name in piece_map.items():
            image_path = f"images1/{file_name}.png"
            original_image = pygame.image.load(image_path)
            self.images[piece] = pygame.transform.scale(original_image, (self.sq_size, self.sq_size))

    def draw_board(self):
        """Draw the chess board and pieces."""
        for r in range(8):
            for c in range(8):
                color = pygame.Color("dark green") if (r + c) % 2 == 0 else pygame.Color("light gray")
                pygame.draw.rect(self.screen, color, pygame.Rect(c*self.sq_size, r*self.sq_size, self.sq_size, self.sq_size))
                piece = self.board.board[r][c].strip()
                if piece in self.images:
                    self.screen.blit(self.images[piece], (c*self.sq_size, r*self.sq_size))

    def convert_click_to_position(self, x, y):
        """Convert pixel coordinates to chessboard row/column."""
        col = x // self.sq_size
        row = y // self.sq_size
        return row, col
    
    def position_to_algebraic(self, row, col):
        """Convert row, col to algebraic notation (e.g., 6,0 -> 'a2')."""
        file = chr(col + ord('a'))
        rank = str(8 - row)
        return file + rank

    def show_start_screen(self):
        """
        Display a start screen where the player can set difficulty level and lightning strike frequency.
        Returns: (difficulty, strike_frequency) tuple with player's chosen settings
        """
        # Default settings
        difficulty = 2  # Default AI difficulty (1-6)
        strike_frequency = 15  # Default strikes frequency
        selection = 0  # 0 = difficulty, 1 = strike_frequency
        # Setup the screen
        self.screen.fill(pygame.Color("black"))
        font_large = pygame.font.Font(None, 48)
        font_medium = pygame.font.Font(None, 32)
        font_small = pygame.font.Font(None, 24)
        # Title
        title = font_large.render("Chess with Lightning Strikes", True, (255, 255, 0))
        title_rect = title.get_rect(center=(self.screen.get_width() // 2, 80))
        # Create confirm button
        confirm_button = pygame.Rect(self.screen.get_width() // 2 - 100, 400, 200, 50)
        # Main loop for the start screen
        running = True
        while running:
            # Clear screen
            self.screen.fill(pygame.Color("black"))
            # Display title and instructions
            self.screen.blit(title, title_rect)
            instruction_text = font_small.render("Use UP/DOWN to select, LEFT/RIGHT to change values", True, (255, 255, 255))
            self.screen.blit(instruction_text, (50, 150))
            # Display difficulty option (highlight if selected)
            difficulty_color = (255, 255, 0) if selection == 0 else (255, 255, 255)
            difficulty_text = font_medium.render(f"AI Difficulty: {difficulty}", True, difficulty_color)
            self.screen.blit(difficulty_text, (100, 200))
            # Display difficulty explanation
            if difficulty == 0:
                diff_explanation = "Dynamic - AI adjusts to your skill level"
            else:
                diff_explanation = f"Level {difficulty}: AI looks {difficulty} moves ahead"
            diff_expl_text = font_small.render(diff_explanation, True, (200, 200, 200))
            self.screen.blit(diff_expl_text, (100, 230))
            # Display strike frequency option (highlight if selected)
            frequency_color = (255, 255, 0) if selection == 1 else (255, 255, 255)
            frequency_text = font_medium.render(f"Lightning every: {strike_frequency} turns", True, frequency_color)
            self.screen.blit(frequency_text, (100, 280))
            # Display frequency explanation
            freq_explanation = "Lower number = more frequent lightning strikes"
            freq_expl_text = font_small.render(freq_explanation, True, (200, 200, 200))
            self.screen.blit(freq_expl_text, (100, 310))
            # Draw confirm button
            pygame.draw.rect(self.screen, (0, 128, 0), confirm_button)
            confirm_text = font_medium.render("START GAME", True, (255, 255, 255))
            confirm_rect = confirm_text.get_rect(center=confirm_button.center)
            self.screen.blit(confirm_text, confirm_rect)
            # Event handling
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    pygame.quit()
                    sys.exit()
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_UP:
                        selection = 0  # Select difficulty
                    elif event.key == pygame.K_DOWN:
                        selection = 1  # Select frequency
                    elif event.key == pygame.K_LEFT:
                        # Decrease the selected setting
                        if selection == 0:
                            difficulty = max(0, difficulty - 1)  # 0 is dynamic, 1 is easiest
                        elif selection == 1:
                            strike_frequency = max(5, strike_frequency - 5)
                    elif event.key == pygame.K_RIGHT:
                        # Increase the selected setting
                        if selection == 0:
                            difficulty = min(6, difficulty + 1)
                        elif selection == 1:
                            strike_frequency = min(50, strike_frequency + 5)
                    elif event.key == pygame.K_RETURN:
                        running = False  # Exit the start screen
                elif event.type == pygame.MOUSEBUTTONDOWN:
                    # Check if confirm button was clicked
                    if confirm_button.collidepoint(event.pos):
                        running = False  # Exit the start screen
            pygame.display.flip()
        return difficulty, strike_frequency

    def run(self, difficulty, strike_frequency):
        running = True
        move_start_pos = None  # Initialize a variable to store the start position
        possible_moves = []  # To store possible moves for the selected piece
        turn_counter = 0  # Counter to track the number of turns
        # No need to initialize stunned_pieces dictionary as it's now in board.py
        while running:

            # Check if it's black's turn (AI is black)
            if not self.board.white_to_move: 
                print("Black to move - AI is calculating...")
                # Check for game over conditions for black (AI)
                if self.board.get_all_legal_moves("black") == []:
                    if self.board.is_check():
                        self.show_endgame_screen("White wins by checkmate!\nThank you for playing!")
                    else:
                        self.show_endgame_screen("Stalemate! Thank you for playing!")
                    running = False  # End the game
                    break  # Exit the loop
                # AI makes its move if no game-ending conditions
                game.ai_move(self.board, difficulty)
                turn_counter += 1  # Increment turn counter after AI's move
                # After AI move, check if white has valid moves, and handle the game-ending
                if self.board.get_all_legal_moves("white") == []:
                    if self.board.is_check():
                        self.show_endgame_screen("Black wins by checkmate!\nThank you for playing!")
                    else:
                        self.show_endgame_screen("Stalemate!\nThank you for playing!")
                    running = False  # End the game
                    break  # Exit the loop
                # Lightning strike every strike_frequency turns (15 is default)
                if turn_counter % strike_frequency == 0:
                    self.lightning_strike()
                # Decrement stun duration for all stunned pieces
                stunned_pieces_copy = self.board.stunned_pieces.copy()
                for piece_pos, stun_duration in stunned_pieces_copy.items():
                    if stun_duration > 1:
                        self.board.stunned_pieces[piece_pos] = stun_duration - 1
                    else:
                        row, col = piece_pos
                        piece = self.board.board[row][col]
                        position = self.position_to_algebraic(row, col)
                        print(f"The {piece} at {position} is no longer stunned!")
                        del self.board.stunned_pieces[piece_pos]
                self.draw_board()  # Redraw the board to reflect the state
                self.highlight_possible_moves(possible_moves)
                self.highlight_stunned_pieces()  # Add this to highlight stunned pieces every frame
                pygame.display.flip()

            # Handle player's move (if game is not over)
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.MOUSEBUTTONDOWN:
                    x, y = pygame.mouse.get_pos()
                    row, col = self.convert_click_to_position(x, y)
                    if move_start_pos:
                        # Second click: Destination for the move
                        move_end_pos = (row, col)
                        start = self.position_to_algebraic(*move_start_pos)
                        end = self.position_to_algebraic(row, col)
                        # Create the move string and use existing logic to process the move
                        move_string = f"{start} to {end}"
                        print(f"Trying move: {move_string}")
                        # Check if the selected piece is stunned
                        if move_start_pos in self.board.stunned_pieces:
                            print(f"This piece is stunned for {self.board.stunned_pieces[move_start_pos]} more turns and cannot move!")
                        else:
                            # Try to parse the move with the current move logic
                            if self.board.parse_move(move_string):
                                turn_counter += 1  # Increment turn counter after player's move
                                # Lightning strike every strike_frequency turns (15 is default)
                                if turn_counter % strike_frequency == 0:
                                    self.lightning_strike()
                                # Decrement stun duration for all stunned pieces
                                stunned_pieces_copy = self.board.stunned_pieces.copy()
                                for piece_pos, stun_duration in stunned_pieces_copy.items():
                                    if stun_duration > 1:
                                        self.board.stunned_pieces[piece_pos] = stun_duration - 1
                                    else:
                                        row, col = piece_pos
                                        piece = self.board.board[row][col]
                                        position = self.position_to_algebraic(row, col)
                                        print(f"The {piece} at {position} is no longer stunned!")
                                        del self.board.stunned_pieces[piece_pos]
                        # Reset start position and possible moves for the next action
                        move_start_pos = None
                        possible_moves = []
                    else:
                        # First click: Select the piece if it's a piece of the current player
                        if self.board.board[row][col].strip() != " " and \
                        ((self.board.white_to_move and self.board.board[row][col].isupper()) or
                        (not self.board.white_to_move and self.board.board[row][col].islower())):
                            move_start_pos = (row, col)  # Store the start position
                            # Check if the piece is stunned
                            if move_start_pos in self.board.stunned_pieces:
                                print(f"This piece is stunned for {self.board.stunned_pieces[move_start_pos]} more turns!")
                                possible_moves = []  # No possible moves for stunned pieces
                            else:
                                possible_moves = self.board.get_piece_possible_moves(row, col)  # Get possible moves for the piece
                    # Redraw the board to reflect the state, with or without a successful move
                    self.draw_board()  # This will draw the board and pieces
                    self.highlight_possible_moves(possible_moves)  # This highlights possible moves
                    self.highlight_stunned_pieces()  # This highlights stunned pieces
                    pygame.display.flip()
        pygame.quit()
        sys.exit()

    # Handle a lightning strike event and display the message.
    def lightning_strike(self):
        struck_position = self.board.lightning_strike()
        if struck_position:
            row, col = struck_position
            piece = self.board.board[row][col]
            position = self.position_to_algebraic(row, col)
            # Redraw the board immediately to show the highlight
            self.draw_board()
            self.highlight_stunned_pieces()
            # Display message at the top of the screen
            font = pygame.font.Font(None, 28)
            message1 = f"Lightning strikes at {position}!"
            message2 = f"The piece will be stunned for 3 turns."
            text1 = font.render(message1, True, (255, 255, 0))
            text2 = font.render(message2, True, (255, 255, 0))
            text_rect1 = text1.get_rect(center=(self.screen.get_width() // 2, 20))
            text_rect2 = text2.get_rect(center=(self.screen.get_width() // 2, 50))
            self.screen.blit(text1, text_rect1)
            self.screen.blit(text2, text_rect2)
            pygame.display.flip()
            pygame.time.delay(3000)  # Show message for 3 seconds

    def show_endgame_screen(self, message):
        """ Display the endgame screen with the final board state and message. """
        self.draw_board()  # Draw the last state of the board
        pygame.display.flip()  # Update the display with the final board state
        # Display the endgame message at the top
        font = pygame.font.Font(None, 36)
        text = font.render(message, True, (255, 255, 255))  # White text color
        text_rect = text.get_rect(center=(self.screen.get_width() // 2, 50))  # Center the text at the top
        self.screen.blit(text, text_rect)
        # Wait for the user to quit or close the window
        pygame.display.flip()  # Update the display
        waiting = True
        while waiting:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    waiting = False

    def highlight_possible_moves(self, possible_moves):
        for move in possible_moves:
            start_row, start_col, end_row, end_col = move  # Correctly unpack all four coordinates
            # Convert the row and column into pixel positions for drawing
            center_x = end_col * self.sq_size + self.sq_size // 2
            center_y = end_row * self.sq_size + self.sq_size // 2
            pygame.draw.circle(self.screen, (255, 0, 0), (center_x, center_y), 15)

    # evaluate which side is at an advantage using piece values and advanced heuristics
    def evaluate(self, board):
        material = board.get_material_score()
        pawns = board.get_piece_position_score('p', Heuristics.PAWN_TABLE)
        knights = board.get_piece_position_score('n', Heuristics.KNIGHT_TABLE)
        bishops = board.get_piece_position_score('b', Heuristics.BISHOP_TABLE)
        rooks = board.get_piece_position_score('r', Heuristics.ROOK_TABLE)
        queens = board.get_piece_position_score('q', Heuristics.QUEEN_TABLE)
        return material + pawns + knights + bishops + rooks + queens

    # helper to make the best move possible by looking forward a set number of turns
    def alphabeta(self, board, depth, a, b, maximizing):
        if (depth == 0):
            return self.evaluate(board)

        if (maximizing):
            best_score = -10000000
            for move in board.get_all_legal_moves("white"):
                start, end = move
                copy = board.clone_board()
                copy.move(start, end)
                best_score = max(best_score, self.alphabeta(copy, depth-1, a, b, False))
                # undo all the moves that have occurred
                a = max(a, best_score)
                if (b <= a):
                    break
            return best_score
        else:
            best_score = 10000000
            for move in board.get_all_legal_moves("black"):
                start, end = move
                copy = board.clone_board()
                copy.move(start, end)
                best_score = min(best_score, self.alphabeta(copy, depth-1, a, b, True))
                b = min(b, best_score)
                if (b <= a):
                    break
            return best_score

    # make the best move possible
    def ai_move(self, board, difficulty):
        best_move = None
        best_score = 10000000
        legal_moves = board.get_all_legal_moves('black')
        print(f"Testing {len(legal_moves)} moves.")

        if difficulty == 0:  # Dynamic difficulty
            difficulty = self.get_dynamic_difficulty(board)
            print(f"Using dynamic difficulty: Level {difficulty}")

        for move in legal_moves:
            start, end = move
            print(f"Testing move from {start} to {end}")
            copy = board.clone_board()
            copy.move(start, end)
            
            score = self.alphabeta(copy, difficulty, -10000000, 10000000, True)
            if (score < best_score):
                best_score = score
                best_move = move

        if best_move:
            print(f"Best move: {best_move}, executing...")
            board.move(best_move[0], best_move[1])
        else:
            print("No valid moves found for AI.")

    def get_dynamic_difficulty(self, board):
        """ Dynamically adjust the AI difficulty based on the game state. """
        balance = self.evaluate(board)
        if balance > 700:
            return 4  # Highest difficulty when player is winning
        elif balance > 200:
            return 2
        elif balance > -200:
            return 2  # Medium difficulty for balanced game
        elif balance > -700:
            return 2
        else:
            return 1  # Lowest difficulty when player is losing

    def highlight_stunned_pieces(self):
        """Highlight all stunned pieces with a geometric lightning bolt made of two isosceles triangles."""
        for (row, col), stun_duration in self.board.stunned_pieces.items():
            center_x = col * self.sq_size + self.sq_size // 2
            center_y = row * self.sq_size + self.sq_size // 2
            lightning_color = (255, 255, 0)  # Bright yellow
            # Dimensions of one triangle
            tri_height = self.sq_size * 0.4
            tri_width = self.sq_size * 0.1
            # Top triangle (pointing down)
            top_tip = (center_x, center_y - tri_height)
            top_right = (center_x + tri_width, center_y)
            top_left = (center_x - tri_width, center_y)
            # Bottom triangle (pointing down, but offset)
            offset_x = self.sq_size * 0.15  # Horizontal offset for the bottom triangle
            bottom_tip = (center_x + offset_x, center_y + tri_height)
            bottom_right = (center_x + offset_x + tri_width, center_y)
            bottom_left = (center_x + offset_x - tri_width, center_y)
            # Combine both triangles into a single polygon
            original_points = [top_tip, top_right, top_left, bottom_tip, bottom_right, bottom_left]
            # Rotate the points 30 degrees counterclockwise
            angle = math.radians(-30)  # Negative angle for counterclockwise rotation
            rotated_points = []
            for point in original_points:
                # Translate point to origin
                x = point[0] - center_x
                y = point[1] - center_y
                # Rotate point
                x_rotated = x * math.cos(angle) - y * math.sin(angle)
                y_rotated = x * math.sin(angle) + y * math.cos(angle)
                # Translate point back
                rotated_points.append((x_rotated + center_x, y_rotated + center_y))
            # Draw the rotated polygon
            pygame.draw.polygon(self.screen, lightning_color, rotated_points)

if __name__ == "__main__":
    pygame.init()
    game = ChessGame()
    difficulty, strike_frequency = game.show_start_screen()
    game.run(difficulty, strike_frequency)
