"""Trivia bot plugin for qtpyrc.

Commands (typed in a channel):
  !trivia start     — start trivia in this channel
  !trivia stop      — stop trivia
  !trivia next      — skip to next question
  !trivia topic <t> — restrict to a topic (or 'all' for all topics)
  !trivia topics    — list available topics
  !trivia scores    — show current scores
  !trivia hint      — show another hint immediately

Answers are matched using fuzzy string comparison (Levenshtein distance).

NOTE: The bundled trivia questions (trivia2.json) are outdated and may contain
inaccurate or obsolete answers. Free, high-quality trivia databases are hard to
find. You can replace the questions file with your own JSON in the same format.
See the 'questions_file' config option.
"""

import os
import json
import random
import time

from PySide6.QtCore import QTimer

import plugin
import state


# ---------------------------------------------------------------------------
# Fuzzy matching
# ---------------------------------------------------------------------------

def _levenshtein(s1, s2):
    """Damerau-Levenshtein distance (Python 3 compatible)."""
    len1, len2 = len(s1), len(s2)
    d = [[0] * (len2 + 1) for _ in range(len1 + 1)]
    for i in range(len1 + 1):
        d[i][0] = i
    for j in range(len2 + 1):
        d[0][j] = j
    for i in range(1, len1 + 1):
        for j in range(1, len2 + 1):
            cost = 0 if s1[i - 1] == s2[j - 1] else 1
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost)
            if i > 1 and j > 1 and s1[i - 1] == s2[j - 2] and s1[i - 2] == s2[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + cost)
    return d[len1][len2]


def _fuzzy_match(guess, answer, threshold=0.3):
    """Check if guess is close enough to answer.
    threshold is the max ratio of edits to answer length."""
    g = guess.lower().strip()
    a = answer.lower().strip()
    if g == a:
        return True
    if not g or not a:
        return False
    dist = _levenshtein(g, a)
    max_dist = max(1, int(len(a) * threshold))
    return dist <= max_dist


# ---------------------------------------------------------------------------
# mIRC color helpers
# ---------------------------------------------------------------------------

def _c(fg, text, bg=None):
    """Wrap text in mIRC color codes."""
    if bg is not None:
        return '\x03%d,%d%s\x03' % (fg, bg, text)
    return '\x03%d%s\x03' % (fg, text)


def _bold(text):
    return '\x02%s\x02' % text


# ---------------------------------------------------------------------------
# Plugin
# ---------------------------------------------------------------------------

class TriviaBot(plugin.Callbacks):
    """Trivia bot plugin."""

    config_fields = [
        ('command_prefix', str, '!trivia', 'Command prefix'),
        ('questions_file', str, '', 'Questions file'),
        ('hint_interval', int, 15, 'Hint interval (sec)'),
        ('max_hints', int, 5, 'Max hints'),
        ('question_timeout', int, 60, 'Question timeout (sec)'),
        ('fuzzy_threshold', float, 0.3, 'Fuzzy threshold'),
        ('use_colors', bool, True, 'Use mIRC colors'),
        ('no_color_channels', str, '', 'No-color channels'),
        ('color_question', int, 3, 'Question color'),
        ('color_answer', int, 4, 'Answer color'),
        ('color_hint', int, 7, 'Hint color'),
        ('color_score', int, 12, 'Score color'),
        ('auto_channels', str, '', 'Auto-start channels'),
        ('channel_mode', str, 'allow_all', 'Channel mode',
            ['allow_all', 'block_all']),
        ('blocked_channels', str, '', 'Blocked channels'),
        ('allowed_channels', str, '', 'Allowed channels'),
        ('scores_interval', int, 0, 'Scores interval'),
    ]

    def __init__(self, irc):
        super().__init__(irc)
        self._games = {}  # (network_key, irclower_channel) -> game state
        self._questions = None  # loaded on demand
        self._all_questions = []  # flat list of (topic, subtopic, question, answer, fun_fact)

    def _cfg(self, key, default=None):
        return self.irc.get_config('triviabot', key, default)

    def _load_questions(self):
        if self._questions is not None:
            return
        path = self._cfg('questions_file', '')
        if not path:
            # Look for bundled file
            import state
            config_dir = os.path.dirname(os.path.abspath(state.config.path))
            for candidate in [
                os.path.join(config_dir, 'scripts', 'triviabot', 'trivia2.json'),
                os.path.join(config_dir, 'plugins', 'triviabot', 'trivia2.json'),
                os.path.join(os.path.dirname(__file__), 'triviabot', 'trivia2.json'),
            ]:
                if os.path.isfile(candidate):
                    path = candidate
                    break
        if not path or not os.path.isfile(path):
            self._questions = {}
            return
        with open(path, 'r', encoding='utf-8') as f:
            self._questions = json.load(f)
        # Build flat list
        self._all_questions = []
        for topic, subtopics in self._questions.items():
            if isinstance(subtopics, dict):
                for subtopic, items in subtopics.items():
                    if isinstance(items, list) and len(items) > 1:
                        qa_list = items[1] if isinstance(items[1], list) else items
                        for qa in qa_list:
                            if isinstance(qa, list) and len(qa) >= 2:
                                q = qa[0]
                                a_data = qa[1]
                                answer = a_data[0] if isinstance(a_data, list) else a_data
                                fun_fact = a_data[1] if isinstance(a_data, list) and len(a_data) > 1 else ''
                                self._all_questions.append((topic, subtopic, q, str(answer), str(fun_fact)))

    def _get_questions(self, topic_filter=None):
        """Get questions, optionally filtered by topic."""
        self._load_questions()
        if not topic_filter or topic_filter.lower() == 'all':
            return list(self._all_questions)
        tf = topic_filter.lower()
        return [q for q in self._all_questions
                if tf in q[0].lower() or tf in q[1].lower()]

    def _game_key(self, conn, channel):
        nk = conn.client.network_key or ''
        return (nk.lower(), conn.irclower(channel))

    def _match_channel_list(self, conn, channel, list_str):
        """Check if channel matches a comma-separated network/channel list."""
        if not list_str:
            return False
        nk = (conn.client.network_key or '').lower()
        ch = channel.lower()
        for entry in list_str.split(','):
            entry = entry.strip().lower()
            if not entry:
                continue
            if '/' in entry:
                net, chan = entry.split('/', 1)
                if net == nk and chan == ch:
                    return True
            elif entry == ch:
                return True
        return False

    def _is_blocked(self, conn, channel):
        mode = self._cfg('channel_mode', 'allow_all').lower().strip()
        if mode == 'block_all':
            # Block everything except the allowed list
            return not self._match_channel_list(conn, channel,
                                                self._cfg('allowed_channels', ''))
        else:
            # Allow everything except the blocked list
            return self._match_channel_list(conn, channel,
                                            self._cfg('blocked_channels', ''))

    def _use_colors(self, conn, channel):
        """Check if colors should be used for this channel."""
        if not self._cfg('use_colors', True):
            return False
        no_color = self._cfg('no_color_channels', '')
        if no_color:
            nk = (conn.client.network_key or '').lower()
            ch = channel.lower()
            for entry in no_color.split(','):
                entry = entry.strip().lower()
                if '/' in entry:
                    net, chan = entry.split('/', 1)
                    if net == nk and chan == ch:
                        return False
                elif entry == ch:
                    return False
        return True

    def _msg(self, conn, channel, text):
        """Send a message, stripping colors if disabled for this channel."""
        if not self._use_colors(conn, channel):
            import re
            text = re.sub(r'[\x02\x03\x0F\x16\x1D\x1F]|\x03\d{0,2}(?:,\d{0,2})?', '', text)
        self.irc.say(conn, channel, text)

    def _start_game(self, conn, channel, topic=None):
        key = self._game_key(conn, channel)
        if key in self._games:
            self._msg(conn, channel, '%s Trivia is already running! Use %s stop to end it.' % (
                _bold('[!]'), (state.config.plugin_prefix + 'trivia') if state.config.plugin_prefix else self._cfg('command_prefix', '!trivia')))
            return
        questions = self._get_questions(topic)
        if not questions:
            self._msg(conn, channel, '%s No questions found%s.' % (
                _bold('[X]'), ' for topic "%s"' % topic if topic else ''))
            return
        random.shuffle(questions)
        game = {
            'conn': conn,
            'channel': channel,
            'questions': questions,
            'index': 0,
            'scores': {},
            'current_q': None,
            'current_a': None,
            'hints_shown': 0,
            'hint_mask': None,
            'answered': 0,
            'topic': topic,
            'last_hint_time': 0,
        }
        self._games[key] = game
        topic_str = ' (%s)' % _c(self._cfg('color_question', 3), topic) if topic else ''
        prefix = (state.config.plugin_prefix + 'trivia') if state.config.plugin_prefix else self._cfg('command_prefix', '!trivia')
        self._msg(conn, channel, '%s Trivia started%s! %d questions loaded. Type your answers!' % (
            _bold('[TRIVIA]'), topic_str, len(questions)))
        self._msg(conn, channel, '  Commands: %s start [topic], %s stop, %s next, '
            '%s topic <name>, %s topics [name], %s scores, %s hint, %s help' % (
            prefix, prefix, prefix, prefix, prefix, prefix, prefix, prefix))
        self._msg(conn, channel, '  (Note: bundled questions may be outdated — see plugin config to use your own)')
        self._next_question(game)

        # Start hint timer
        interval = self._cfg('hint_interval', 15)
        timer = QTimer()
        timer.setInterval(interval * 1000)
        timer.timeout.connect(lambda k=key: self._on_hint_timer(k))
        timer.start()
        game['timer'] = timer

    def _stop_game(self, conn, channel):
        key = self._game_key(conn, channel)
        game = self._games.pop(key, None)
        if not game:
            self._msg(conn, channel, '%s No trivia game running.' % _bold('[X]'))
            return
        if 'timer' in game:
            game['timer'].stop()
        self._show_scores(conn, channel, game, final=True)
        self._msg(conn, channel, '%s Trivia stopped!' % _bold('[STOP]'))

    def _next_question(self, game):
        if game['index'] >= len(game['questions']):
            random.shuffle(game['questions'])
            game['index'] = 0
            self._msg(game['conn'], game['channel'],
                      '%s All questions used — reshuffled!' % _bold('[~]'))
        topic, subtopic, q, a, fun_fact = game['questions'][game['index']]
        game['index'] += 1
        game['current_q'] = q
        game['current_a'] = a
        game['fun_fact'] = fun_fact
        game['hints_shown'] = 0
        game['last_hint_time'] = time.time()
        # Build hint mask — all hidden initially
        game['hint_mask'] = ['*' if c != ' ' else ' ' for c in a]
        cq = self._cfg('color_question', 3)
        cat = '%s/%s' % (topic, subtopic)
        self._msg(game['conn'], game['channel'],
                  '%s [%s] %s' % (_bold('[Q]'), _c(cq, cat), _c(cq, q)))

    def _show_hint(self, game):
        max_hints = self._cfg('max_hints', 5)
        if game['hints_shown'] >= max_hints:
            # Time's up — reveal answer
            ca = self._cfg('color_answer', 4)
            self._msg(game['conn'], game['channel'],
                      '%s Time\'s up! The answer was: %s' % (_bold('[TIME]'), _c(ca, game['current_a'])))
            if game.get('fun_fact'):
                self._msg(game['conn'], game['channel'], '[*] %s' % game['fun_fact'])
            self._next_question(game)
            return
        # Reveal some more letters
        answer = game['current_a']
        mask = game['hint_mask']
        hidden = [i for i, c in enumerate(mask) if c == '*']
        if hidden:
            # Reveal ~25% of remaining hidden letters
            to_reveal = max(1, len(hidden) // 4)
            for i in random.sample(hidden, min(to_reveal, len(hidden))):
                mask[i] = answer[i]
        game['hints_shown'] += 1
        game['last_hint_time'] = time.time()
        ch = self._cfg('color_hint', 7)
        hint_str = ''.join(mask)
        self._msg(game['conn'], game['channel'],
                  '%s Hint %d/%d: %s' % (_bold('[*]'), game['hints_shown'], max_hints,
                                         _c(ch, hint_str)))

    def _show_scores(self, conn, channel, game, final=False):
        scores = game['scores']
        if not scores:
            self._msg(conn, channel, '%s No scores yet.' % _bold('[#]'))
            return
        cs = self._cfg('color_score', 12)
        header = '%s Final Scores:' if final else '%s Scores:'
        self._msg(conn, channel, header % _bold('[#]'))
        for i, (nick, score) in enumerate(sorted(scores.items(), key=lambda x: -x[1]), 1):
            self._msg(conn, channel, '  %s. %s — %s' % (
                i, _c(cs, nick), _bold(str(score))))

    def _check_answer(self, conn, channel, nick, message):
        key = self._game_key(conn, channel)
        game = self._games.get(key)
        if not game or not game['current_a']:
            return
        threshold = self._cfg('fuzzy_threshold', 0.3)
        if _fuzzy_match(message, game['current_a'], threshold):
            game['scores'][nick] = game['scores'].get(nick, 0) + 1
            game['answered'] += 1
            ca = self._cfg('color_answer', 4)
            cs = self._cfg('color_score', 12)
            self._msg(conn, channel,
                      '%s %s got it! The answer was: %s (Score: %s)' % (
                          _bold('[OK]'), _c(cs, nick), _c(ca, game['current_a']),
                          _bold(str(game['scores'][nick]))))
            if game.get('fun_fact'):
                self._msg(conn, channel, '[*] %s' % game['fun_fact'])
            # Auto-show scores every N questions
            si = self._cfg('scores_interval', 0)
            if si and game['answered'] % si == 0:
                self._show_scores(conn, channel, game)
            self._next_question(game)

    def _on_hint_timer(self, key):
        """Called by QTimer to show the next hint."""
        game = self._games.get(key)
        if not game:
            return
        self._show_hint(game)

    # --- IRC event handler ---

    def chanmsg(self, irc, conn, user, channel, message):
        nick = user.split('!', 1)[0]
        msg = message.strip()
        prefix = (state.config.plugin_prefix + 'trivia') if state.config.plugin_prefix else self._cfg('command_prefix', '!trivia')
        if prefix and msg.lower().startswith(prefix.lower()):
            args = msg[len(prefix):].strip().split(None, 1)
            cmd = args[0].lower() if args else ''
            rest = args[1] if len(args) > 1 else ''

            if cmd == 'start':
                if self._is_blocked(conn, channel):
                    self._msg(conn, channel, '%s Trivia is not allowed in this channel.' % _bold('[X]'))
                    return True
                self._start_game(conn, channel, rest.strip() or None)
                return True
            elif cmd == 'stop':
                self._stop_game(conn, channel)
                return True
            elif cmd == 'next':
                key = self._game_key(conn, channel)
                game = self._games.get(key)
                if game:
                    ca = self._cfg('color_answer', 4)
                    self._msg(conn, channel, '%s Skipped! Answer was: %s' % (
                        _bold('[>>]'), _c(ca, game['current_a'])))
                    self._next_question(game)
                return True
            elif cmd == 'topic':
                key = self._game_key(conn, channel)
                game = self._games.get(key)
                if not game:
                    self._msg(conn, channel, '%s Start trivia first with %s start' % (
                        _bold('[X]'), prefix))
                    return True
                topic = rest.strip()
                if not topic:
                    self._msg(conn, channel, '%s Current topic: %s' % (
                        _bold('[i]'), game['topic'] or 'all'))
                    return True
                questions = self._get_questions(topic)
                if not questions:
                    self._msg(conn, channel, '%s Unknown topic: %s' % (_bold('[X]'), topic))
                    return True
                game['questions'] = questions
                random.shuffle(game['questions'])
                game['index'] = 0
                game['topic'] = topic
                self._msg(conn, channel, '%s Topic changed to %s (%d questions)' % (
                    _bold('[i]'), _c(self._cfg('color_question', 3), topic), len(questions)))
                self._next_question(game)
                return True
            elif cmd == 'topics':
                self._load_questions()
                if not self._questions:
                    return True
                if rest.strip():
                    # Show subtopics for a specific topic
                    topic_name = rest.strip()
                    # Case-insensitive match
                    matched = None
                    for t in self._questions:
                        if t.lower() == topic_name.lower():
                            matched = t
                            break
                    if not matched:
                        # Try partial match
                        for t in self._questions:
                            if topic_name.lower() in t.lower():
                                matched = t
                                break
                    if matched and isinstance(self._questions[matched], dict):
                        subtopics = sorted(self._questions[matched].keys())
                        self._msg(conn, channel, '%s %s subtopics: %s' % (
                            _bold('[i]'), matched, ', '.join(subtopics)))
                    elif matched:
                        self._msg(conn, channel, '%s %s has no subtopics.' % (_bold('[i]'), matched))
                    else:
                        self._msg(conn, channel, '%s Unknown topic: %s' % (_bold('[X]'), topic_name))
                else:
                    topics = sorted(self._questions.keys())
                    self._msg(conn, channel, '%s Topics: %s' % (_bold('[i]'), ', '.join(topics)))
                    self._msg(conn, channel, '  Use %s topics <name> to see subtopics. '
                        'Use %s topic <name> or %s start <name> to filter by topic or subtopic.' % (
                        prefix, prefix, prefix))
                return True
            elif cmd == 'scores':
                key = self._game_key(conn, channel)
                game = self._games.get(key)
                if game:
                    self._show_scores(conn, channel, game)
                else:
                    self._msg(conn, channel, '%s No game running.' % _bold('[#]'))
                return True
            elif cmd == 'hint':
                key = self._game_key(conn, channel)
                game = self._games.get(key)
                if game:
                    self._show_hint(game)
                return True
            elif cmd == 'help' or cmd == '':
                self._msg(conn, channel, '%s Trivia commands:' % _bold('[Q]'))
                self._msg(conn, channel, '  %s start [topic/subtopic] — start trivia (optionally filtered)' % prefix)
                self._msg(conn, channel, '  %s stop — stop trivia and show final scores' % prefix)
                self._msg(conn, channel, '  %s next — skip to the next question' % prefix)
                self._msg(conn, channel, '  %s topic <name> — change topic or subtopic mid-game' % prefix)
                self._msg(conn, channel, '  %s topics — list topics (%s topics <name> for subtopics)' % (prefix, prefix))
                self._msg(conn, channel, '  %s scores — show current scores' % prefix)
                self._msg(conn, channel, '  %s hint — request an extra hint' % prefix)
                return True
            else:
                self._msg(conn, channel, '%s Unknown command: %s. Try %s help' % (
                    _bold('[X]'), cmd, prefix))
                return True

        # Check if message is an answer attempt
        key = self._game_key(conn, channel)
        if key in self._games:
            self._check_answer(conn, channel, nick, msg)

    def signedOn(self, irc, conn, message):
        """Auto-start trivia in configured channels after connecting."""
        auto = self._cfg('auto_channels', '')
        if not auto:
            return
        nk = (conn.client.network_key or '').lower()
        for entry in auto.split(','):
            entry = entry.strip()
            if '/' in entry:
                net, ch = entry.split('/', 1)
                if net.lower() == nk:
                    # Delay auto-start to allow channel join
                    QTimer.singleShot(10000, lambda c=conn, channel=ch: self._auto_start(c, channel))

    def _auto_start(self, conn, channel):
        """Auto-start trivia in a channel after a delay."""
        if conn and conn.client:
            chnlower = conn.irclower(channel)
            if chnlower in conn.client.channels:
                self._start_game(conn, channel)

    def die(self):
        """Clean up all games and timers."""
        for game in self._games.values():
            if 'timer' in game:
                game['timer'].stop()
        self._games.clear()
        super().die()


Class = TriviaBot
