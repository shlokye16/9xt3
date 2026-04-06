EMPTY = None
X = "X"
O = "O"

WIN_LINES = [
    (0,1,2),(3,4,5),(6,7,8),
    (0,3,6),(1,4,7),(2,5,8),
    (0,4,8),(2,4,6)
    ]
    
class Board:
    def __init__(self, state=None):
        if state:
            self.boards = state["boards"]
            self.big_board = state["big_board"]
            self.forced_board = state["forced_board"]
            self.current_player = state["current_player"]
            self.winner = state["winner"]
            self.last_move = state["last_move"]
        else:
            self.boards = [[EMPTY]*9 for _ in range(9)]
            self.big_board = [EMPTY]*9
            self.forced_board = None
            self.current_player = X
            self.winner = None
            self.last_move = None

    def serialize(self):
        return {
            "boards": self.boards,
            "big_board": self.big_board,
            "forced_board": self.forced_board,
            "current_player": self.current_player,
            "winner": self.winner,
            "last_move": self.last_move
        }

    def check_small_win(self, board):
        for a, b, c in WIN_LINES:
            if board[a] and board[a] == board[b] == board[c]:
                return True
        return False

    @staticmethod
    def is_small_board_full(board):
        return all(cell is not EMPTY for cell in board)
        
    def upd_lm(self, board_idx: int, cell_idx: int):
        self.last_move = [board_idx, cell_idx]

    def make_move(self, board_idx: int, cell_idx: int):
        if self.big_board[board_idx] is not None:
            raise ValueError("Board already won")
        if self.forced_board is not None and board_idx != self.forced_board:
            raise ValueError(f"You must play in board {self.forced_board}")
        if self.boards[board_idx][cell_idx] is not None:
            raise ValueError("Cell already occupied")

        self.boards[board_idx][cell_idx] = self.current_player
        if self.check_small_win(self.boards[board_idx]):
            self.big_board[board_idx] = self.current_player

        self.forced_board = cell_idx
        if self.big_board[self.forced_board] is not None:
            self.forced_board = None
            
        for a, b, c in WIN_LINES:
            if self.big_board[a] and self.big_board[a] == self.big_board[b] == self.big_board[c]:
                self.winner = self.big_board[a]
                break

        if self.winner is None and all(cell is not None for cell in self.big_board):
            self.winner = "TIE"

        self.current_player = "O" if self.current_player == "X" else "X"
