# chess.py — IRC chess plugin for qtpyrc
#
# Play chess in IRC channels. Games are stored in memory.
#
# Commands (type in channel or query):
#   !chess new <opponent> [white|black]  — start a new game
#   !chess move <from> <to>              — make a move (e.g. !chess move e2 e4)
#   !chess board [#id]                   — show the board
#   !chess list                          — list active games
#   !chess resign                        — resign the current game
#   !chess colors                        — show available color schemes
#   !chess set <scheme#>                 — change color scheme for current game
#
# Aliases: !cn, !cm, !cb, !cl, !cr

import json
import os
import plugin
import state

# ---------------------------------------------------------------------------
# Chess color schemes: [wscolor, bscolor, wpwscolor, wpbscolor, bpwscolor, bpbscolor]
# ---------------------------------------------------------------------------

COLOR_SCHEMES = [
  [0,1,10,10,4,4], [0,1,3,3,13,13], [0,15,12,12,3,3],
  [0,15,12,12,6,6], [0,15,3,3,6,6], [0,15,12,12,4,4],
  [0,15,12,12,6,6], [0,15,12,12,1,1], [0,15,10,10,4,4],
  [0,15,6,6,1,1], [0,15,3,3,1,1], [0,14,4,4,1,1],
  [2,3,11,11,13,13], [2,3,0,0,13,13],
  [0,8,3,3,1,1], [0,8,4,4,1,1], [10,3,0,0,1,1],
  [10,3,8,8,1,1], [4,1,0,0,12,12], [15,14,4,4,1,1],
  [15,14,12,12,1,1], [15,14,12,12,4,4], [0,14,12,12,1,1],
  [0,14,14,0,1,1], [10,12,0,0,4,4],
]

DEFAULT_SCHEME = [15, 14, 12, 12, 4, 4]

# Piece display characters
PIECE_CHARS = {
  'R': '#', 'N': chr(167), 'B': 'x', 'Q': '*', 'K': chr(177), 'P': 'i', ' ': ' '
}

INITIAL_BOARD = (
  "rnbqkbnr"
  "pppppppp"
  "        "
  "        "
  "        "
  "        "
  "PPPPPPPP"
  "RNBQKBNR"
)


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _clr(piece):
  """Return True for black (lowercase), False for white (uppercase), None for empty."""
  if piece == ' ':
    return None
  return piece == piece.lower()


def _pc(piece):
  """Return (uppercase_piece, is_black)."""
  return piece.upper(), _clr(piece)


def _cplace(x1, y1, x2=None, y2=None):
  """Convert coordinates to algebraic notation."""
  a = chr(ord('a') + x1) + str(8 - y1)
  if x2 is not None:
    a += chr(ord('a') + x2) + str(8 - y2)
  return a


def _checkline(p1x, p1y, p2x, p2y, board):
  """Check if the path between two squares is clear."""
  dx = (p2x > p1x) * 2 - 1
  dy = (p2y > p1y) * 2 - 1
  if p1x == p2x:
    return not ''.join([board[y * 8 + p1x] for y in range(p1y + dy, p2y, dy)]).strip()
  elif p1y == p2y:
    return not ''.join([board[p1y * 8 + x] for x in range(p1x + dx, p2x, dx)]).strip()
  else:
    return not ''.join([board[(p1y + n * dy + dy) * 8 + p1x + n * dx + dx]
                        for n in range(abs(p2y - p1y) - 1)]).strip()


def _parse_square(s):
  """Parse algebraic notation like 'e2' to (x, y) coords. Returns None on error."""
  if len(s) != 2:
    return None
  x = ord(s[0].lower()) - ord('a')
  try:
    y = 8 - int(s[1])
  except ValueError:
    return None
  if 0 <= x <= 7 and 0 <= y <= 7:
    return x, y
  return None


# ---------------------------------------------------------------------------
# Game state
# ---------------------------------------------------------------------------

def _clean_ident(ident):
  """Strip leading ~ from ident for consistent identity."""
  return ident.lstrip('~') if ident else ''


def _get_ident(user):
  """Extract clean ident from a nick!ident@host string."""
  if '!' in user:
    ih = user.split('!', 1)[1]
    ident = ih.split('@', 1)[0]
    return _clean_ident(ident)
  return ''


class ChessGame:
  _next_id = 1

  def __init__(self, white_ident, white_nick, black_ident, black_nick, scheme=None):
    self.id = ChessGame._next_id
    ChessGame._next_id += 1
    self.white_ident = white_ident    # stable identity
    self.black_ident = black_ident
    self.white_nick = white_nick      # display name (updated on each move)
    self.black_nick = black_nick
    self.password = None               # optional claim password
    self.board = INITIAL_BOARD
    self.turn = 0  # 0=white, 1=black
    self.moves = ''
    self.won = None
    self.stalemate = False
    self.draw = False
    self.forfeit = None
    self.wcheck = False
    self.bcheck = False
    self.scheme = scheme or list(DEFAULT_SCHEME)
    self._pending_promotion = None

  def to_dict(self):
    d = {
      'id': self.id,
      'white_ident': self.white_ident, 'black_ident': self.black_ident,
      'white_nick': self.white_nick, 'black_nick': self.black_nick,
      'password': self.password,
      'board': self.board, 'turn': self.turn, 'moves': self.moves,
      'won': self.won, 'stalemate': self.stalemate, 'draw': self.draw,
      'forfeit': self.forfeit, 'wcheck': self.wcheck, 'bcheck': self.bcheck,
      'scheme': self.scheme,
    }
    if self._pending_promotion:
      d['pending_promotion'] = list(self._pending_promotion)
    return d

  @classmethod
  def from_dict(cls, d):
    g = cls.__new__(cls)
    g.id = d['id']
    g.white_ident = d.get('white_ident', '')
    g.black_ident = d.get('black_ident', '')
    g.white_nick = d.get('white_nick', d.get('white', ''))
    g.black_nick = d.get('black_nick', d.get('black', ''))
    g.password = d.get('password')
    g.board = d['board']
    g.turn = d['turn']
    g.moves = d['moves']
    g.won = d.get('won')
    g.stalemate = d.get('stalemate', False)
    g.draw = d.get('draw', False)
    g.forfeit = d.get('forfeit')
    g.wcheck = d.get('wcheck', False)
    g.bcheck = d.get('bcheck', False)
    g.scheme = d.get('scheme', list(DEFAULT_SCHEME))
    pp = d.get('pending_promotion')
    g._pending_promotion = tuple(pp) if pp else None
    return g

  @property
  def finished(self):
    return self.won is not None or self.stalemate or self.draw or self.forfeit is not None

  def player_color_by_ident(self, ident):
    """Return 0 for white, 1 for black, None if not a player."""
    if ident and ident == self.white_ident:
      return 0
    if ident and ident == self.black_ident:
      return 1
    return None

  def player_color(self, nick):
    """Fallback: match by nick (case-insensitive)."""
    nl = nick.lower()
    if nl == self.white_nick.lower():
      return 0
    if nl == self.black_nick.lower():
      return 1
    return None

  def get_color(self, ident, nick):
    """Match by ident first, then nick."""
    c = self.player_color_by_ident(ident)
    if c is not None:
      return c
    return self.player_color(nick)

  def update_nick(self, ident, nick):
    """Update display nick when a player makes a move."""
    if ident == self.white_ident:
      self.white_nick = nick
    elif ident == self.black_ident:
      self.black_nick = nick

  def current_player_nick(self):
    return self.white_nick if self.turn == 0 else self.black_nick

  def render_board(self):
    """Return list of IRC-formatted lines for the board."""
    sc = self.scheme[0], self.scheme[1]
    pc = self.scheme[2], self.scheme[3], self.scheme[4], self.scheme[5]
    lines = []
    for row in range(8):
      m = str(8 - row) + ' '
      for col in range(8):
        piece = self.board[row * 8 + col]
        is_black = piece.lower() == piece and piece != ' '
        piece_upper = piece.upper()
        color_idx = (is_black * 2) + ((row + col) % 2)
        sq_color = sc[(row + col) % 2]
        pc_color = pc[color_idx]
        m += '\x03%d,%d %s ' % (pc_color, sq_color, PIECE_CHARS.get(piece_upper, '?'))
      # Side info
      if row == 3:
        m += '\x0f Game #%d: %s (white) vs %s (black)' % (self.id, self.white_nick, self.black_nick)
      if row == 4:
        m += '\x0f '
        if self.won is not None:
          m += ['White', 'Black'][self.won] + ' has won'
        elif self.draw:
          m += 'Draw'
        elif self.forfeit is not None:
          m += ['White', 'Black'][self.forfeit] + ' forfeited'
        elif self.stalemate:
          m += 'Stalemate'
        else:
          m += ['White', 'Black'][self.turn] + "'s turn"
      if row == 5:
        if self.wcheck:
          m += '\x0f White is in check'
        elif self.bcheck:
          m += '\x0f Black is in check'
      lines.append(m + '\x0f')
    lines.append('   a  b  c  d  e  f  g  h')
    return lines


# ---------------------------------------------------------------------------
# Move validation
# ---------------------------------------------------------------------------

def _domove(game, turn, p1x, p1y, p2x, p2y, recurse, do, board, moves):
  """Validate and execute a move. Returns (new_board, reason) or (None, error)."""
  b = board
  reason = ''
  piece = b[p1y * 8 + p1x]
  if piece == ' ':
    return None, 'No piece at %s' % _cplace(p1x, p1y)
  piece, color = _pc(b[p1y * 8 + p1x])
  if color != turn:
    return None, "That's not your piece"

  # Path clearance (except knights)
  if piece != 'N':
    if not _checkline(p1x, p1y, p2x, p2y, b):
      return None, "Path is blocked"

  # --- Pawn ---
  if piece == 'P':
    b2 = b
    px1, py1, px2, py2 = p1x, p1y, p2x, p2y
    if turn:
      b2 = b[::-1]
      px1, py1, px2, py2 = 7 - p1x, 7 - p1y, 7 - p2x, 7 - p2y
    if px1 == px2:
      if b2[px2 + py2 * 8] != ' ':
        return None, "Can't move there"
      if py2 == py1 - 2:
        if py1 != 6:
          return None, "Pawns can only move 2 squares on first move"
      elif py2 != py1 - 1:
        return None, "Can't move there"
    else:
      if not (abs(px2 - px1) == 1 and py1 - py2 == 1):
        return None, "Can't move there"
      if b2[py2 * 8 + px2] == ' ':
        # En passant check
        ep_from = _cplace(p2x, p2y + [-1, 1][color])
        ep_to = _cplace(p2x, p2y + [1, -1][color])
        ep_move = ep_from + ep_to
        if len(moves) >= 4 and moves[-4:] == ep_move and ep_from not in moves[:-4]:
          reason = 'En passant'
          idx = (p2y + [1, -1][color]) * 8 + p2x
          b = b[:idx] + ' ' + b[idx + 1:]
        else:
          return None, "Pawns can only capture diagonally"
    # Promotion
    if py2 == 0:
      if do:
        # Return special marker for promotion
        return 'PROMOTE', _cplace(p1x, p1y, p2x, p2y)
      else:
        return True, ''

  # --- Rook ---
  elif piece == 'R':
    if not (p1x == p2x or p1y == p2y):
      return None, "Rooks move in straight lines"

  # --- Knight ---
  elif piece == 'N':
    if (abs(p1x - p2x), abs(p1y - p2y)) not in ((1, 2), (2, 1)):
      return None, "Invalid knight move"

  # --- Bishop ---
  elif piece == 'B':
    if abs(p1x - p2x) != abs(p1y - p2y):
      return None, "Bishops move diagonally"

  # --- Queen ---
  elif piece == 'Q':
    if not (p1x == p2x or p1y == p2y or abs(p1x - p2x) == abs(p1y - p2y)):
      return None, "Invalid queen move"

  # --- King ---
  elif piece == 'K':
    if p1y == p2y and p2y == [7, 0][turn] and abs(p2x - p1x) == 2 and p1x == 4:
      # Castling
      rook_x = 7 if p2x > 4 else 0
      king_start = _cplace(4, p1y)
      rook_start = _cplace(rook_x, p1y)
      if king_start in moves or rook_start in moves:
        return None, "Castling: king or rook has already moved"
      if not _checkline(rook_x, p1y, p1x, p1y, b):
        return None, "Castling: pieces between king and rook"
      # Check if king passes through attacked square
      mid_x = (p1x + p2x) // 2
      for px in range(8):
        for py in range(8):
          r = _domove(game, not turn, px, py, mid_x, p2y, False, False, b, moves)
          if r[0] and r[0] is not True and r[0] != 'PROMOTE':
            return None, "Castling: king passes through check"
          r = _domove(game, not turn, px, py, p1x, p1y, False, False, b, moves)
          if r[0] and r[0] is not True and r[0] != 'PROMOTE':
            return None, "Castling: king is in check"
      # Move rook
      new_rook_x = 5 if p2x > 4 else 3
      rook_piece = 'R' if not turn else 'r'
      b = b[:p1y * 8 + rook_x] + ' ' + b[p1y * 8 + rook_x + 1:]
      b = b[:p1y * 8 + new_rook_x] + rook_piece + b[p1y * 8 + new_rook_x + 1:]
      reason = 'Castling'
    else:
      if abs(p1x - p2x) > 1 or abs(p1y - p2y) > 1:
        return None, "Kings move one square"
      if abs(p1x - p2x) + abs(p1y - p2y) not in (1, 2):
        return None, "Invalid king move"

  # Can't capture own piece
  if _clr(b[p2y * 8 + p2x]) == turn:
    return None, "Can't capture your own piece"

  # Execute move
  b = b[:p2y * 8 + p2x] + b[p1y * 8 + p1x] + b[p2y * 8 + p2x + 1:]
  b = b[:p1y * 8 + p1x] + ' ' + b[p1y * 8 + p1x + 1:]

  # Check if own king is in check after this move
  if recurse:
    king_char = 'K' if not turn else 'k'
    if king_char not in b:
      return None, "Invalid board state"
    kp = b.index(king_char)
    kx, ky = kp % 8, kp // 8
    for tx in range(8):
      for ty in range(8):
        r = _domove(game, not turn, tx, ty, kx, ky, False, False, b,
                    moves + _cplace(p1x, p1y, p2x, p2y))
        if r[0] and r[0] is not True and r[0] != 'PROMOTE':
          if (not turn and game.wcheck) or (turn and game.bcheck):
            return None, "Your king is in check"
          else:
            return None, "That would put your king in check"

  return b, reason


def _check_game_state(game, turn, board, move):
  """After a move, check for check/checkmate/stalemate."""
  # Is opponent's king in check?
  opp_king = 'k' if not turn else 'K'
  if opp_king not in board:
    return  # shouldn't happen
  kp = board.index(opp_king)
  kx, ky = kp % 8, kp // 8

  check = False
  for px in range(8):
    for py in range(8):
      r = _domove(game, turn, px, py, kx, ky, False, False, board, game.moves)
      if r[0] and r[0] is not True and r[0] != 'PROMOTE':
        check = True
        break
    if check:
      break

  if not turn:
    game.wcheck = game.wcheck  # preserve
    game.bcheck = check
  else:
    game.wcheck = check
    game.bcheck = game.bcheck

  # Can opponent make any legal move?
  has_move = False
  for p1y in range(8):
    for p1x in range(8):
      if has_move:
        break
      for p2x in range(8):
        for p2y in range(8):
          r = _domove(game, not turn, p1x, p1y, p2x, p2y, True, False, board, game.moves)
          if r[0] and r[0] is not True:
            has_move = True
            break
          if r[0] == 'PROMOTE':
            has_move = True
            break
        if has_move:
          break
    if has_move:
      break

  if check and not has_move:
    # Checkmate
    game.won = turn
    game.wcheck = False
    game.bcheck = False
    game.stalemate = False
    return 'Checkmate! %s wins.' % ['White', 'Black'][turn]
  elif check:
    return 'Check!'
  elif not has_move:
    game.stalemate = True
    return 'Stalemate — draw.'
  return None


# ---------------------------------------------------------------------------
# Plugin
# ---------------------------------------------------------------------------

class Chess(plugin.Callbacks):
  config_fields = [
    ('allowed_channels', str, '', 'Allowed channels\n(comma-separated, empty=off)'),
    ('command_prefix', str, '', 'Full command (e.g. "!chess", empty = use global plugin_prefix)'),
  ]

  def __init__(self, irc):
    super().__init__(irc)
    self.games = {}  # id -> ChessGame (cache, loaded on demand)
    self.current_game = {}  # ident -> game_id
    self._load_meta()

  def _cfg(self, key, default=None):
    return self.irc.get_config('chess', key, default)

  def _games_dir(self):
    config_dir = os.path.dirname(os.path.abspath(state.config.path))
    d = os.path.join(config_dir, 'chess')
    os.makedirs(d, exist_ok=True)
    return d

  def _meta_path(self):
    return os.path.join(self._games_dir(), '_meta.json')

  def _game_path(self, gid):
    return os.path.join(self._games_dir(), '%d.json' % gid)

  def _save_game(self, game):
    """Save a single game to its own file."""
    try:
      with open(self._game_path(game.id), 'w', encoding='utf-8') as f:
        json.dump(game.to_dict(), f, indent=2)
    except Exception as e:
      state.dbg(state.LOG_WARN, '[chess] Failed to save game #%d: %s' % (game.id, e))

  def _save_meta(self):
    """Save the metadata (next_id, current_game mapping)."""
    try:
      with open(self._meta_path(), 'w', encoding='utf-8') as f:
        json.dump({
          'next_id': ChessGame._next_id,
          'current_game': self.current_game,
        }, f, indent=2)
    except Exception as e:
      state.dbg(state.LOG_WARN, '[chess] Failed to save meta: %s' % e)

  def _load_meta(self):
    """Load metadata on startup."""
    path = self._meta_path()
    if not os.path.isfile(path):
      return
    try:
      with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
      ChessGame._next_id = data.get('next_id', 1)
      self.current_game = data.get('current_game', {})
    except Exception as e:
      state.dbg(state.LOG_WARN, '[chess] Failed to load meta: %s' % e)

  def _load_game(self, gid):
    """Load a game by ID (on demand). Returns the game or None."""
    if gid in self.games:
      return self.games[gid]
    path = self._game_path(gid)
    if not os.path.isfile(path):
      return None
    try:
      with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
      g = ChessGame.from_dict(data)
      self.games[g.id] = g
      return g
    except Exception as e:
      state.dbg(state.LOG_WARN, '[chess] Failed to load game #%d: %s' % (gid, e))
      return None

  def _save(self, game):
    """Save a game and update metadata."""
    self._save_game(game)
    self._save_meta()

  def die(self):
    self._save_meta()
    super().die()

  def _say(self, conn, target, text):
    """Send a message to channel or nick."""
    self.irc.msg(conn, target, text)

  def _say_lines(self, conn, target, lines):
    for line in lines:
      self._say(conn, target, line)

  def _find_game(self, ident):
    """Find the current game for an ident."""
    gid = self.current_game.get(ident)
    if gid:
      return self._load_game(gid)
    return None

  def chanmsg(self, irc, conn, user, channel, message):
    # Only respond in allowed channels
    allowed = self._cfg('allowed_channels', '')
    if not allowed:
      return
    allowed_list = [c.strip().lower() for c in allowed.split(',') if c.strip()]
    if allowed_list and channel.lower() not in allowed_list:
      return

    tokens = message.tokens if hasattr(message, 'tokens') else message.split()
    if not tokens:
      return
    cmd = tokens[0].lower()
    nick = user.split('!', 1)[0]
    ident = _get_ident(user)
    args = tokens[1:]
    full_cmd = (state.config.plugin_prefix + 'chess') if state.config.plugin_prefix else self._cfg('command_prefix', '!chess')
    # Derive the prefix character for aliases (e.g. "!" from "!chess")
    p = full_cmd[:-5] if full_cmd.lower().endswith('chess') else full_cmd[0:1]

    if cmd == full_cmd.lower():
      if not args:
        self._say(conn, channel, 'Usage: %s new|move|board|list|resign|colors|set|claim|password' % full_cmd)
        return True
      sub = args[0].lower()
      args = args[1:]
      if sub == 'new':
        self._cmd_new(conn, channel, nick, ident, args)
      elif sub == 'move':
        self._cmd_move(conn, channel, nick, ident, args)
      elif sub == 'board':
        self._cmd_board(conn, channel, nick, ident, args)
      elif sub == 'list':
        self._cmd_list(conn, channel, nick, ident)
      elif sub == 'resign':
        self._cmd_resign(conn, channel, nick, ident)
      elif sub == 'colors':
        self._cmd_colors(conn, channel)
      elif sub == 'set':
        self._cmd_set_scheme(conn, channel, nick, ident, args)
      elif sub == 'promote':
        self._cmd_promote(conn, channel, nick, ident, args)
      elif sub == 'password':
        self._cmd_password(conn, channel, nick, ident, args)
      elif sub == 'claim':
        self._cmd_claim(conn, channel, nick, ident, args)
      else:
        self._say(conn, channel, 'Unknown chess command: %s' % sub)
      return True
    # Shorthand aliases
    elif cmd == p + 'cn':
      self._cmd_new(conn, channel, nick, ident, args)
      return True
    elif cmd == p + 'cm':
      self._cmd_move(conn, channel, nick, ident, args)
      return True
    elif cmd == p + 'cb':
      self._cmd_board(conn, channel, nick, ident, args)
      return True
    elif cmd == p + 'cl':
      self._cmd_list(conn, channel, nick, ident)
      return True
    elif cmd == p + 'cr':
      self._cmd_resign(conn, channel, nick, ident)
      return True

  def _cmd_new(self, conn, channel, nick, ident, args):
    """!chess new <opponent> [white|black] [scheme#]"""
    if len(args) < 1:
      self._say(conn, channel, 'Usage: !chess new <opponent> [white|black] [scheme#]')
      return
    opponent_nick = args[0]
    if opponent_nick.lower() == nick.lower():
      self._say(conn, channel, "You can't play against yourself")
      return

    # Try to find opponent's ident from channel users
    opp_ident = ''
    if conn and hasattr(conn, 'client'):
      for chan in conn.client.channels.values():
        for u in chan.users.values():
          if u.nick and u.nick.lower() == opponent_nick.lower():
            opp_ident = _clean_ident(u.ident) if u.ident else ''
            break
        if opp_ident:
          break

    play_as = 'white'
    scheme = None
    for a in args[1:]:
      if a.lower() in ('white', 'black'):
        play_as = a.lower()
      elif a.isdigit():
        idx = int(a)
        if 1 <= idx <= len(COLOR_SCHEMES):
          scheme = list(COLOR_SCHEMES[idx - 1])
        else:
          self._say(conn, channel, 'Invalid color scheme (1-%d). Use !chess colors' % len(COLOR_SCHEMES))
          return

    if play_as == 'white':
      game = ChessGame(ident, nick, opp_ident, opponent_nick, scheme)
    else:
      game = ChessGame(opp_ident, opponent_nick, ident, nick, scheme)

    self.games[game.id] = game
    self.current_game[ident] = game.id
    if opp_ident:
      self.current_game[opp_ident] = game.id
    self._say(conn, channel, 'Chess game #%d created: %s (white) vs %s (black)' % (
      game.id, game.white_nick, game.black_nick))
    self._say_lines(conn, channel, game.render_board())
    self._save(game)

  def _cmd_move(self, conn, channel, nick, ident, args):
    """!chess move <from> <to>"""
    game = self._find_game(ident)
    if not game:
      self._say(conn, channel, 'No active game. Use !chess new or !chess board #id')
      return
    if game.finished:
      self._say(conn, channel, 'Game #%d is already finished' % game.id)
      return
    if game._pending_promotion:
      self._say(conn, channel, 'Pending promotion — use: !chess promote queen|rook|bishop|knight')
      return

    color = game.get_color(ident, nick)
    if color is None:
      self._say(conn, channel, "You're not in this game")
      return
    if color != game.turn:
      self._say(conn, channel, "It's not your turn")
      return

    if len(args) < 2:
      self._say(conn, channel, 'Usage: !chess move <from> <to> (e.g. !cm e2 e4)')
      return

    sq1 = _parse_square(args[0])
    sq2 = _parse_square(args[1])
    if not sq1 or not sq2:
      self._say(conn, channel, 'Invalid square. Use algebraic notation (e.g. e2, d7)')
      return

    p1x, p1y = sq1
    p2x, p2y = sq2
    result, reason = _domove(game, game.turn, p1x, p1y, p2x, p2y,
                             True, True, game.board, game.moves)
    if result is None:
      self._say(conn, channel, reason)
      return

    if result == 'PROMOTE':
      game._pending_promotion = (p1x, p1y, p2x, p2y)
      self._say(conn, channel, 'Pawn promotion! Choose: !chess promote queen|rook|bishop|knight')
      self._save(game)
      return

    move_str = _cplace(p1x, p1y, p2x, p2y)
    game.board = result
    game.moves += move_str
    game.update_nick(ident, nick)
    if reason:
      self._say(conn, channel, reason)

    state_msg = _check_game_state(game, game.turn, game.board, move_str)
    game.turn = 1 - game.turn
    if state_msg:
      self._say(conn, channel, state_msg)

    self._say_lines(conn, channel, game.render_board())
    self._save(game)

  def _cmd_promote(self, conn, channel, nick, ident, args):
    """Handle pawn promotion."""
    game = self._find_game(ident)
    if not game or not game._pending_promotion:
      self._say(conn, channel, 'No pending promotion')
      return

    color = game.get_color(ident, nick)
    if color != game.turn:
      self._say(conn, channel, "It's not your turn")
      return

    if not args:
      self._say(conn, channel, 'Usage: !chess promote queen|rook|bishop|knight')
      return

    piece_map = {'queen': 'Q', 'rook': 'R', 'bishop': 'B', 'knight': 'N',
                 'q': 'Q', 'r': 'R', 'b': 'B', 'n': 'N'}
    choice = args[0].lower()
    piece = piece_map.get(choice)
    if not piece:
      self._say(conn, channel, 'Invalid choice. Use queen, rook, bishop, or knight')
      return

    p1x, p1y, p2x, p2y = game._pending_promotion
    game._pending_promotion = None

    b = game.board
    if game.turn:
      piece = piece.lower()
    b = b[:p1y * 8 + p1x] + ' ' + b[p1y * 8 + p1x + 1:]
    b = b[:p2y * 8 + p2x] + piece + b[p2y * 8 + p2x + 1:]

    move_str = _cplace(p1x, p1y, p2x, p2y) + piece.upper()
    game.board = b
    game.moves += move_str
    game.update_nick(ident, nick)

    self._say(conn, channel, 'Promoted to %s' % choice.capitalize())

    state_msg = _check_game_state(game, game.turn, game.board, move_str)
    game.turn = 1 - game.turn
    if state_msg:
      self._say(conn, channel, state_msg)

    self._say_lines(conn, channel, game.render_board())
    self._save(game)

  def _cmd_board(self, conn, channel, nick, ident, args):
    """!chess board [#id]"""
    if args:
      gid_str = args[0].lstrip('#')
      try:
        gid = int(gid_str)
      except ValueError:
        self._say(conn, channel, 'Usage: !chess board [#id]')
        return
      game = self._load_game(gid)
      if not game:
        self._say(conn, channel, 'Game #%d not found' % gid)
        return
      self.current_game[ident] = gid
      self._save_meta()
    else:
      game = self._find_game(ident)
      if not game:
        self._say(conn, channel, 'No active game. Specify a game: !chess board #id')
        return
    self._say_lines(conn, channel, game.render_board())

  def _cmd_list(self, conn, channel, nick, ident):
    """!chess list — list games from saved files."""
    games_dir = self._games_dir()
    games = []
    for fname in os.listdir(games_dir):
      if fname.endswith('.json') and fname != '_meta.json':
        g = self._load_game(int(fname[:-5]))
        if g:
          games.append(g)
    active = [g for g in games if not g.finished]
    finished = [g for g in games if g.finished]
    if not active and not finished:
      self._say(conn, channel, 'No chess games')
      return
    if active:
      lines = []
      for g in active:
        lines.append('#%d: %s vs %s (%s\'s turn)' % (
          g.id, g.white_nick, g.black_nick, g.current_player_nick()))
      self._say(conn, channel, 'Active: ' + ' | '.join(lines))
    if finished:
      lines = []
      for g in finished:
        if g.won is not None:
          status = '%s won' % ['White', 'Black'][g.won]
        elif g.stalemate:
          status = 'stalemate'
        elif g.forfeit is not None:
          status = '%s resigned' % ['White', 'Black'][g.forfeit]
        else:
          status = 'draw'
        lines.append('#%d: %s vs %s (%s)' % (g.id, g.white_nick, g.black_nick, status))
      self._say(conn, channel, 'Finished: ' + ' | '.join(lines))

  def _cmd_resign(self, conn, channel, nick, ident):
    """!chess resign"""
    game = self._find_game(ident)
    if not game:
      self._say(conn, channel, 'No active game')
      return
    if game.finished:
      self._say(conn, channel, 'Game is already finished')
      return
    color = game.get_color(ident, nick)
    if color is None:
      self._say(conn, channel, "You're not in this game")
      return
    game.forfeit = color
    self._say(conn, channel, '%s resigns. %s wins game #%d.' % (
      nick, ['Black', 'White'][color], game.id))
    self._save(game)

  def _cmd_colors(self, conn, channel):
    """!chess colors — show available color schemes."""
    P = PIECE_CHARS['P']
    for i, v in enumerate(COLOR_SCHEMES):
      line = '\x0f %2d: \x03%d,%d %s \x03%d,%d %s \x03%d,%d %s \x0f' % (
        i + 1,
        v[5], v[1], P,
        v[4], v[0], P,
        v[3], v[1], P,
      )
      self._say(conn, channel, line)

  def _cmd_set_scheme(self, conn, channel, nick, ident, args):
    """!chess set <scheme#>"""
    game = self._find_game(ident)
    if not game:
      self._say(conn, channel, 'No active game')
      return
    if not args or not args[0].isdigit():
      self._say(conn, channel, 'Usage: !chess set <scheme#> (use !chess colors to see options)')
      return
    idx = int(args[0])
    if idx < 1 or idx > len(COLOR_SCHEMES):
      self._say(conn, channel, 'Invalid scheme (1-%d)' % len(COLOR_SCHEMES))
      return
    game.scheme = list(COLOR_SCHEMES[idx - 1])
    self._say(conn, channel, 'Color scheme updated')
    self._say_lines(conn, channel, game.render_board())
    self._save(game)

  def _cmd_password(self, conn, channel, nick, ident, args):
    """!chess password <password> — set a password on your current game (PM only)."""
    if channel.startswith('#'):
      self._say(conn, channel, 'Use this command in a private message for security')
      return
    game = self._find_game(ident)
    if not game:
      self._say(conn, nick, 'No active game')
      return
    if not args:
      self._say(conn, nick, 'Usage: /msg bot !chess password <password>')
      return
    game.password = args[0]
    self._say(conn, nick, 'Password set for game #%d' % game.id)
    self._save(game)

  def _cmd_claim(self, conn, channel, nick, ident, args):
    """!chess claim <game#> <password> [white|black] — claim a seat in a game."""
    if len(args) < 2:
      self._say(conn, channel, 'Usage: !chess claim <game#> <password> [white|black]')
      return
    try:
      gid = int(args[0].lstrip('#'))
    except ValueError:
      self._say(conn, channel, 'Invalid game ID')
      return
    game = self._load_game(gid)
    if not game:
      self._say(conn, channel, 'Game #%d not found' % gid)
      return
    if not game.password:
      self._say(conn, channel, 'Game #%d has no password set' % gid)
      return
    if args[1] != game.password:
      self._say(conn, channel, 'Wrong password')
      return

    # Determine which side to claim
    side = args[2].lower() if len(args) > 2 else None
    if side == 'white':
      game.white_ident = ident
      game.white_nick = nick
    elif side == 'black':
      game.black_ident = ident
      game.black_nick = nick
    else:
      # Auto: claim the side that doesn't match current ident
      if game.white_ident == ident or game.black_ident == ident:
        self._say(conn, channel, "You're already in this game")
        return
      # Claim whichever side is available or different
      game.white_ident = ident
      game.white_nick = nick

    self.current_game[ident] = gid
    self._say(conn, channel, '%s claimed a seat in game #%d' % (nick, gid))
    self._save(game)


Class = Chess
