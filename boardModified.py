import random
class ChessBoard:
    # Initialize a chessboard with an 8x8 grid
    def __init__(self):
        self.board = [
            ["r", "n", "b", "q", "k", "b", "n", "r"],
            ["p", "p", "p", "p", "p", "p", "p", "p"],
            [" ", " ", " ", " ", " ", " ", " ", " "],
            [" ", " ", " ", " ", " ", " ", " ", " "],
            [" ", " ", " ", " ", " ", " ", " ", " "],
            [" ", " ", " ", " ", " ", " ", " ", " "],
            ["P", "P", "P", "P", "P", "P", "P", "P"],
            ["R", "N", "B", "Q", "K", "B", "N", "R"],
        ]
        self.white_to_move = True # track turns
        self.stunned_pieces = {}

    # Create a new ChessBoard with the exact same board state
    def clone_board(self):
        new_board = ChessBoard() # new ChessBoard
        new_board.board = [row[:] for row in self.board]  # Deep copy the board
        new_board.white_to_move = self.white_to_move  # Copy the turn state
        return new_board


    # Display the board in terminal(Now only for debugging)
    def display(self):
        print("   a b c d e f g h")
        print(" +-----------------+")
        for i, row in enumerate(self.board):
            print(f"{8-i}| {' '.join(row)} |")
        print(" +-----------------+\n")

    # Convert a position from algebraic notation (e.g., 'e2') to board indices.
    # Helper method for parse_move
    def parse_position(self, pos):
        pos = pos.strip()  # Remove any leading or trailing spaces
        if len(pos) != 2 or not pos[0].isalpha() or not pos[1].isdigit():
            print(f"Invalid position format: '{pos}'. Please use column-row format like 'e2'.")
            return None
        column = ord(pos[0].lower()) - ord('a')  # Convert column letter to index (0-7)
        row = 8 - int(pos[1])  # Convert row number to index (0-7, top to bottom)
        if 0 <= column < 8 and 0 <= row < 8:
            return (row, column)
        else: # Out of chessboard bounds
            print("Invalid position input. Please ensure positions are within 'a1' to 'h8'.")
            return None

    # Parse a move given in standard notation (e.g., 'e2 to e4') and execute it if valid.
    def parse_move(self, move):
        parts = move.split(" to ")  # Split the move into start and end parts
        if len(parts) != 2:
            print("Invalid move format. Please use 'a1 to a2' format.")
            return
        start_pos = self.parse_position(parts[0])  # Parse the start position
        end_pos = self.parse_position(parts[1])    # Parse the end position
        if start_pos is None or end_pos is None:
            return
        self.move(start_pos, end_pos)  # Attempt to make the move

    # Move a piece from the start position to the end position if the move is valid.
    # Includes handling for castling and pawn promotion.
    def move(self, start, end):
        moving_piece = self.board[start[0]][start[1]]
        sx, sy = start
        ex, ey = end
        if not self.is_valid_move(start, end):
            print("Invalid move, type 'help' for game rules")
            return False
        # Castling move
        if moving_piece in ('K', 'k') and abs(sy - ey) == 2:
            # Move the king
            self.board[ex][ey] = moving_piece
            self.board[sx][sy] = " "
            # Move the corresponding rook
            if ey > sy:  # King-side castling
                self.board[ex][ey - 1] = self.board[ex][7]  # Move rook next to king
                self.board[ex][7] = " "
            else:  # Queen-side castling
                self.board[ex][ey + 1] = self.board[ex][0]  # Move rook next to king
                self.board[ex][0] = " "
        else:
            # Regular move or pawn promotion
            self.board[end[0]][end[1]] = moving_piece
            self.board[start[0]][start[1]] = " "
            # Pawn promotion
            if moving_piece in ('P', 'p') and (ex == 0 or ex == 7):
                # Promote pawn to queen when it reaches the other side of the board
                self.board[end[0]][end[1]] = 'Q' if moving_piece == 'P' else 'q'
        # Switch turns
        self.white_to_move = not self.white_to_move
        return True
    
    # check if the move is valid and doesn't leave the king in check
    def is_valid_move(self, start, end):
        sx, sy = start
        ex, ey = end
        if not self.is_valid_move_help(sx, sy, ex, ey):
            return False
        moving_piece = self.board[sx][sy]
        target_piece = self.board[ex][ey]
        # Move the piece
        self.board[ex][ey] = moving_piece
        self.board[sx][sy] = " "
        # Check if this move leaves the king in check
        in_check = self.is_check()
        # Undo the move
        self.board[sx][sy] = moving_piece
        self.board[ex][ey] = target_piece
        # If the move is possible and does not leave the king in check, it's valid
        return not in_check
    

    def is_valid_move_help(self, sx, sy, ex, ey):
        """
        Check if a move is valid according to the rules of chess and the current board state..
        """
        moving_piece = self.board[sx][sy]
        target_piece = self.board[ex][ey]

        # Generic move rules
        if not (0 <= sx < 8 and 0 <= sy < 8 and 0 <= ex < 8 and 0 <= ey < 8):
            return False  # Move must be within the bounds of the board
        if moving_piece == " ":  # No piece to move
            return False
        if self.white_to_move and moving_piece.islower():  # White can't move black piece
            return False
        if not self.white_to_move and moving_piece.isupper():  # Black can't move white piece
            return False
        if (moving_piece.isupper() == target_piece.isupper() and target_piece != " "):
            return False  # Can't capture own piece (White)
        if (moving_piece.islower() == target_piece.islower() and target_piece != " "):
            return False  # Can't capture own piece (Black)
        if (sx, sy) in self.stunned_pieces:
            return False  # NEW: Can't move stunned pieces
        
        # Determine piece type and check specific move legality
        piece_type = moving_piece.lower()
        delta_row = abs(ex - sx) # change in row
        delta_col = abs(ey - sy) # change in col

        if piece_type == 'p':  # Pawn
            if self.white_to_move: # White pawn
                if sy == ey and ((sx - ex == 1) or (sx == 6 and sx - ex == 2 and self.board[sx-1][sy] == " ")):
                    # Forward move: one row up, or two rows from starting position(row 6). 
                    # Target space must be empty. If 2 rows forward, must have no piece blocking path.
                    return target_piece == " "
                elif abs(sy - ey) == 1 and sx - ex == 1 and target_piece.islower():
                    # Capture move: one row up, one column left or right. Target space must contain black piece.
                    return True
            else: # Black pawn
                if sy == ey and ((ex - sx == 1) or (sx == 1 and ex - sx == 2 and self.board[sx+1][sy] == " ")):
                    # Forward move: one row up, or two rows from starting position(row 6).
                    # Target space must be empty. If 2 rows forward, must have no piece blocking path.
                    return target_piece == " "
                elif abs(sy - ey) == 1 and ex - sx == 1 and target_piece.isupper():
                    # Capture move: one row up, one column left or right. Target space must contain white piece.
                    return True

        elif piece_type == 'r':  # Rook
            # Movement type: rook can move either vertical or horizontal. 
            # No collision: The loop checks each square along the path, to make sure it is empty
            if delta_row != 0 and delta_col == 0 or delta_row == 0 and delta_col != 0:
                step_row = (ex - sx) // delta_row if delta_row != 0 else 0
                step_col = (ey - sy) // delta_col if delta_col != 0 else 0
                for step in range(1, max(delta_row, delta_col)):
                    if self.board[sx + step * step_row][sy + step * step_col] != " ":
                        return False
                return True

        elif piece_type == 'n':  # Knight
            # Movement type: moves in L shape
            return (delta_row == 2 and delta_col == 1) or (delta_row == 1 and delta_col == 2)

        elif piece_type == 'b':  # Bishop
            if delta_row == delta_col:
                # Bishop moves diagonally, so the number of rows and columns it moves should be the same.
                dir_row = (ex - sx) // delta_row  # Determine the direction of row movement (positive for down, negative for up)
                dir_col = (ey - sy) // delta_col  # Determine the direction of column movement (positive for right, negative for left)
                for step in range(1, delta_row):
                    # Check each square along the diagonal to ensure it's empty.
                    if self.board[sx + step * dir_row][sy + step * dir_col] != " ":
                        return False
                return True  # Path is clear, move is valid.
            return False  # If not moving diagonally, move is invalid.

        elif piece_type == 'q':  # Queen
            if delta_row == 0 or delta_col == 0 or delta_row == delta_col:
                # Queen moves like both a rook and a bishop.
                step_row = (ex - sx) // delta_row if delta_row != 0 else 0  # Horizontal or vertical move
                step_col = (ey - sy) // delta_col if delta_col != 0 else 0  # Diagonal move
                max_step = max(delta_row, delta_col)
                for step in range(1, max_step):
                    # Check each square along the path (horizontal, vertical, or diagonal) to ensure it's empty.
                    if self.board[sx + step * step_row][sy + step * step_col] != " ":
                        return False
                return True  # Path is clear, move is valid.
            return False  # If not moving along any queen-compatible path, move is invalid.
        
        elif piece_type == 'k':  # King
            if delta_row == 0 and delta_col == 0: # king can't stay still
                return False
            # The king can move exactly one square in any direction.
            elif delta_row <= 1 and delta_col <= 1:
                return True
            # Castling
            elif (delta_row == 0 and delta_col == 2):  # Check if it's a castling move
                if self.white_to_move and self.board[7][4] == 'K':
                    if (ey - sy) == 2:  # King-side castling
                        if (self.board[sx][sy + 1] == ' ' and self.board[sx][sy + 2] == ' '
                            and self.board[7][7] == 'R'):
                            return True
                    elif (ey - sy) == -2:  # Queen-side castling
                        if (self.board[sx][sy - 1] == ' ' and self.board[sx][sy - 2] == ' ' and
                            self.board[sx][sy - 3] == ' ' and self.board[7][0] == 'R'):
                            return True
                elif not self.white_to_move and self.board[0][4] == 'k':
                    if (ey - sy) == 2:  # King-side castling
                        if (self.board[sx][sy + 1] == ' ' and self.board[sx][sy + 2] == ' '
                            and self.board[0][7] == 'r'):
                            return True
                    elif (ey - sy) == -2:  # Queen-side castling
                        if (self.board[sx][sy - 1] == ' ' and self.board[sx][sy - 2] == ' ' and
                            self.board[sx][sy - 3] == ' ' and self.board[0][0] == 'r'):
                            return True
            return False

        else:
            print("Error: Invalid piece type")
            return False
        

    # returns true if king is in check, otherwise false.
    def is_check(self):
        if self.white_to_move:  # if white, check if white is in check
            king_symbol = "K"
        else:  # else check if black is in check
            king_symbol = "k"
        king_position = None
        # Scan the board to find the king and break immediately once found
        for r in range(8):
            for c in range(8):
                if self.board[r][c] == king_symbol:
                    king_position = (r, c)
                    break
            if king_position is not None:
                break
        # Error handling if King is not found
        if not king_position:
            print("Programming Error: King not found")
            return False
        # Use the is_square_attacked method to determine if the king's position is under threat
        return self.is_square_attacked(king_position)

    def is_square_attacked(self, position):
        row, col = position
        # Determine the opponent's pieces based on who's turn it is
        opponent_pieces = 'PNBRQK' if not self.white_to_move else 'pnbrqk'
        # Scan the board for opponent pieces and check if they can move to the given position
        for r in range(8):
            for c in range(8):
                piece = self.board[r][c]
                if piece in opponent_pieces:
                    self.white_to_move = not self.white_to_move
                    # Adjusting the call to is_valid_move_help with appropriate parameter unpacking
                    if self.is_valid_move_help(r, c, row, col):
                        self.white_to_move = not self.white_to_move
                        return True
                    self.white_to_move = not self.white_to_move
        return False

    def get_all_legal_moves(self, color):
        # Find all pieces for the current player
        moves = []
        # for each piece
        for r in range(8):
            for c in range(8):
                piece = self.board[r][c]
                if (color == 'white' and piece.isupper()) or (color == 'black' and piece.islower()):
                    start = (r, c)
                    # Try to move into each square
                    for end_row in range(8):
                        for end_col in range(8):
                            end = (end_row, end_col)
                            if self.is_valid_move(start, end):
                                moves.append((start, end))
        return moves
    

    def get_piece_possible_moves(self, start_row, start_col):
        # Get all possible legal moves for a piece at a given start position.
        moves = []
        piece = self.board[start_row][start_col]
        if piece == " ":
            # No piece at the given location
            return moves
        # Try to move into each square
        for end_row in range(8):
            for end_col in range(8):
                end = (end_row, end_col)
                if self.is_valid_move((start_row, start_col), end):
                    moves.append((start_row, start_col, end_row, end_col))
        return moves

    # gather the heuristical score for the given piece type on this board
    def get_piece_position_score(self, piece_type, table):
        white = 0
        black = 0
        for r in range(8):
            for c in range(8):
                piece = self.board[r][c]
                if piece == piece_type.upper():  # Upper case for white pieces
                    white += table[r][c]  # Direct indexing for white
                elif piece == piece_type.lower():  # Lower case for black pieces
                    black += table[7 - r][c]  # Inverted indexing for black to flip the table perspective
        return white - black

    # Tally up the material score across the board. Positive = white winning. Negative = black winning.
    def get_material_score(self):
        white = 0
        black = 0
        for r in range(8):
            for c in range(8):
                # first check what piece it is
                piece = self.board[r][c]
                if piece.lower() == 'p':
                    piece_value = 100
                elif piece.lower() == 'n':
                    piece_value = 320
                elif piece.lower() == 'b':
                    piece_value = 330
                elif piece.lower() == 'r':
                    piece_value = 500
                elif piece.lower() == 'q':
                    piece_value = 900
                elif piece.lower() == 'k':
                    piece_value = 20000
                else:
                    piece_value = 0
                # then check what color it is
                if piece.isupper():  # Upper case for white pieces
                    white += piece_value
                elif piece.islower():  # Lower case for black pieces
                    black += piece_value
        return white - black
    
    # get piece at the given coordinates
    def get_piece_at(self, position):
        x, y = position
        if 0 <= x < 8 and 0 <= y < 8:
            return self.board[x][y]
        return None  # If the position is out of bounds, return None for error checking.

    # remove piece at the given coordinates
    def remove_piece_at(self, position):
        x, y = position
        if 0 <= x < 8 and 0 <= y < 8:
            self.board[x][y] = ' '
        else:
            print("Error: Position out of bounds.")

    # Strikes a random piece on the board with lightning, stunning it for 3 turns.
    def lightning_strike(self):
        # Try up to 64 times to find a piece (worst case would be an almost empty board)
        for _ in range(64):
            x = random.randint(0, 7)
            y = random.randint(0, 7)
            if self.board[x][y] != " ":
                piece = self.board[x][y]
                position = (x, y)
                self.stunned_pieces[position] = 3
                print(f"Lightning strikes the {piece} at position ({x}, {y})! It is stunned for 3 turns.")
                return position
        # If we somehow couldn't find a piece after 64 attempts
        print("ERROR: Could not find a piece to strike with lightning!")
        return None

    def toggle_turn(self):
        """Toggle the current player's turn."""
        self.white_to_move = not self.white_to_move
