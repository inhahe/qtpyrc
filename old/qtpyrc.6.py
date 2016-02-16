#todo
#change window icon
#newyork.ny.us.undernet.org -- on notification to use /motd, has characters that don't
#show up right?
#exit cleanly on ctrl-brk?
#history in input widget
#don't let type more than x-nicklen chrs
#case normalization for channels and nicks
#dynamicize colors and format strings
#it might be useful if we could get the actual server name when you connect to for example
#irc.dal.net.  maybe we'd have to get getPeer() and then do a dns on it?
#remember window positions, etc
#make a class for a window and inherit channel, server, query windows from it
#get that better mirc color regex to work, (^|(?:mirc color))(*.?)
#linereceived should pass the information from irc.py after generic parsing, not before it 
#figure out the differences between current irc.py and latest, like motd = None
#fix isupport calls bounce in irc.py
#"actions"
#try to capture console ctrl break to give a reasonable exit
#/server invaliddomain doesn't seem to make an error
from PyQt4.QtGui import *
from PyQt4.QtCore import *

import sys, qt4reactor, re, cPickle, os
from optparse import OptionParser 

app = QApplication(sys.argv)
qt4reactor.install()
from twisted.internet import protocol, reactor
from twisted.words.protocols import irc
reactor.addSystemEventTrigger('after', 'shutdown', app.quit)
QObject.connect(app, SIGNAL("lastWindowClosed()"), quit)

configpath = os.path.join(os.path.dirname(__file__), "config.pkl")

class Config: pass
def makeconfig():
  config = Config()
  config.multiline = False
  config.cmdprefix = "/"
  config.nickswidth = 100
  config.mode = "maximized"
  config.nickname = "inhahe2"
  config.identid = "inhahe"
  config.fontfamily = "Courier New"
  config.fontheight = 20
  config.fgcolor = Qt.black
  config.bgcolor = Qt.white
  cPickle.dump(config, open(configpath, "w"))
  return config

def lineinput(client, window, text):
  window.input.setText("")
  if text.startswith(config.cmdprefix):
    docommand(client, window, *(text[len(config.cmdprefix):].split(" ", 1)))
  else:
    docommand(client, window, "say", text)  
  
colors = [Qt.white, Qt.black, Qt.darkBlue, Qt.darkGreen, Qt.red, Qt.darkRed, Qt.darkMagenta, QColor("#FC7F00"),
          Qt.yellow, Qt.green, Qt.darkCyan, Qt.cyan, Qt.blue, Qt.magenta, QColor("#7F7F7F"), QColor("#D2D2D2")]
          #the named colors may need corrected

class Commands:
  def join(client, window, text):
    params = text.split(None)
    if 1 <= len(params) <= 2:
      client.factory.conn.join(*params)
    else:
      window.cur.setCharFormataddline('-\n[Error: /join takes 1 or 2 parameters]', redformat)
      #todo: include link to help on /join in error message 
  def say(client, window, text):
    if window.type == "server":
      addlinef(window, '-\n[Error: cannot speak in a server window]', redformat)
    elif window.type == "channel":
      client.factory.conn.say(window.channel, text)
      addline(window, "<%s> %s" % (client.nick, text))
    elif window.type == "query":
      client.factory.conn.say(window.remotenick, text)
      addline(window, "<%s> %s" % (client.nick, text))
  def msg(client, window, text):
    recip, text = text.split(" ", 1)
    window.factory.conn.privmsg(
     "%s!%s@%s" %
     (client.nick, client.ident, client.hostmask),
     recip, text)
  def server(client, window, text):
    parser = OptionParser()
    parser.add_option('-m')
    parser.add_option('-p')
    try:
      options, args = parser.parse_args(text.split())
    except:
      raise
      pass # todo: display error
    if options.m:
      pass        # todo: open in new window
    else:
      if len(args) > 1: port = int(args[1])
      elif options.p: port = options.p
      else: port = 6667
      client.hostname = args[0]
      client.factory = ircclientfactory(client)
      reactor.connectTCP(args[0], port, client.factory)

class ircclient(irc.IRCClient):
  def connectionMade(self):
    irc.IRCClient.connectionMade(self)
    addlinef(self.factory.client.window, '[Connected to %s]' % self.factory.client.hostname, redformat)
    self.factory.conn = self
    
  def connectionLost(self, reason):
    irc.IRCClient.connectionLost(self, reason)
    addlinef(self.factory.client.window, '[Connection lost: %s' % reason.getErrorMessage(), redformat)

  def bounce(self, server, port):
    print "bounced!" #todo
    
  def lineReceived(self, line):
    irc.IRCClient.lineReceived(self, line)
    if line.startswith(":"):
      line = line.split(None, 1)[1]
    command, params = line.split(None, 1)
    try:
      recip, message = params.split(None, 1)
    except:
      recip, message = None, params
    try:
      message, trailing = message.split(":", 1)
    except:
      trailing = ""
    if command in ('001 002 003 004 005 302 303 301 305 306 251 252 253 254 255 375 372 '
                   '376 265 266 433 462 ERROR'): #todo: find all of these that i need
      addline(self.factory.client.window, message + trailing)
    else:
      print line #debug
    
  def noticed(self, user, channel, message):
    #todo
    pass

  def joined(self, channel):
    self.factory.client.channels[channel] = Channel()
  
  def names(self, channel, names):
    for nick in names:
      try:
        self.factory.client.channels[channel].addnick(nick)
      except:
        raise#debug
        pass #if this happened our server is being weird
    
  def privmsg(self, user, channel, message):
    usernick, userathost = user.split("!", 1)
    if channel==self.nickname: #this is a private message
      if userathost not in self.factory.client.queries:
        self.factory.client.queries[userathost] = Querywindow()
      addline(self.factory.client.queries[userathost].window, "<%s> %s" % (usernick, message))
    else: # this is a channel message
      addline(self.factory.client.channels[channel].window, "<%s> %s" % (usernick, message))
      
  """
  def action(self, user, channel, data):
  def bounce(self, info):
  def connectionMade(self):
  def topicUpdated(self, user, channel, newTopic):
  def modeChanged(self, user, channel, set, modes, args):
  def pong(self, user, secs):
  def privmsg(self, user, channel, message):
  def noticed(self, user, channel, message):
  def userJoined(self, user, channel):
  def joined(self, channel):
  def userKicked(self, kickee, channel, kicker, message):
  def kickedFrom(self, channel, kicker, message):
  def userLeft(self, user, channel):
  def left(self, channel):
  def userQuit(self, user, quitMessage):
  def userRenamed(self, oldname, newname):
  def nickChanged(self, nick):
  """
  

class ircclientfactory(protocol.ClientFactory): # do i really need a client factory? 
  protocol = ircclient                          # state can be kept in window object
  def __init__(self, client, *args):
    self.protocol.nickname = config.nickname
    self.client = client
  
def quit():
  reactor.stop()
  app.quit()      
  sys.exit()

class Client:
  def __init__(self):
    self.channels = {}
    self.queries = {}
    self.window = Serverwindow(self)
    self.factory = None
    
class Serverwindow(QWidget):
  def alignstuff(self):
    self.input.setFixedWidth(self.width())
    self.input.move(0, self.height()-config.fontheight)
    self.output.setFixedSize(self.width(), max(0, self.height()-config.fontheight))
    
  def __init__(self, client):
    QWidget.__init__(self, mainwin)
    self.setWindowFlags(Qt.Window )
    self.resize(640, 320)
    self.input = QTextEdit(self)            # qlineedit
    self.input.setAcceptRichText(False)
    self.input.setFixedSize(self.width(), config.fontheight)
    self.client = client
    self.output = QTextEdit(self)
    self.output.setReadOnly(True)
    self.output.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
    self.input.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
    self.vs = self.output.verticalScrollBar()
    self.output.move(0, 0)
    self.inputhistory = []
    workspace.addWindow(self)
    self.input.installEventFilter(self)
    self.type = "server"
    self.output.setFontFamily(config.fontfamily)
    self.cur = QTextCursor(self.output.document())
    
    self.show()
    
  def eventFilter(self, obj, event):
    if event.type()==QEvent.KeyPress:
      if event.text() == '\r':
        m = event.modifiers()
        if not (config.multiline and m and Qt.ShiftModifier):
          lineinput(self.client, self, str(obj.toPlainText()))
          return True
    return False
  
  def resizeEvent(self, event):
    self.alignstuff()
  def moveEvent(self, event):
    self.alignstuff()
    QWidget.moveEvent(self, event)

def newclient():
  clients.add(Client())

colorre = re.compile ('(?:\x0b(?:10|11|12|13|14|15|0\\d|\\d)'
                      '(?:'
                         ',(?:10|11|12|13|14|15|0\\d|\\d)'
                      ')?)|\x02|\xA5|\xA2')

def addline(window, text):
  stb = window.vs.value() == window.vs.maximum()
  if window.cur.position():
    window.cur.insertText('\n')
  #start parsing mirc codes
  text = text + '\xA5' # one extra code because text is always added one iteration behind
  bold = underline = reverse = False
  tf = QTextCharFormat()
  cur = window.cur
  tf.setForeground(config.fgcolor)
  lge = 0
  bgi = config.bgcolor
  for match in colorre.finditer(text):
    code = match.group(0)
    ltext = text[lge:match.start(0)]
    lge = match.end(0)
    cur.insertText(ltext, tf)
    cur.movePosition(cur.End)
    if code[0]=="\x0b": #color
      if "," in code:     #fg,bg
        fgi, bgi = map(int, code[1:].split(","))
        tf.setForeground(colors[fgi])
        tf.setBackground(colors[bgi])
      else:               #fg
        fgi = int(code[1:])
        tf.setForeground(colors[fgi])
    elif code=="\xA5": #underline
      underline = not underline
      tf.setFontUnderline(underline)      
    elif code=="\xA2": #reverse
      reverse = not reverse
      if reverse:
        tf.setForeground(Qt.white)
        tef.setBackground(Qt.black)
      else:
        tf.setForeground(colors[fgi])
        tf.setBackground(colors[bgi])
    elif code=="\x02": #bold
      bold = not bold
      tf.setFontWeight(QFont.Bold if bold else QFont.Normal)
  if stb:# we only want to scroll to the bottom if it had already been scrolled to the bottom
    window.vs.setValue(window.vs.maximum()) 
    
def addlinef(window, text, format):
  stb = window.vs.value() == window.vs.maximum()
  if window.cur.position():
    window.cur.insertText('\n'+text, format)
  else:
    window.cur.insertText(text, format)
  window.cur.movePosition(window.cur.End)
  if stb:
    window.vs.setValue(window.vs.maximum())

def docommand(client, window, command, text):
  command = command.lower()
  if hasattr(Commands, command) and not command.startswith("_"): #potentially dangerous if we allow scripting?
    getattr(Commands, command).im_func(client, window, text)
  
class Inputwidget(QTextEdit):
  def __init__(self):
    QTextEdit.__init__(self)
  
class Channel:
  def __init__(self):
    self.window = Channelwindow()
    self.nicks = set()
  def addline(self, text):
    self.window.output.insertPlainText(text + '\n')
  def addnick(self, nick):
    self.nicks.add(nick)
    self.updatenicklist()
  def removenick(self, nick):
    self.nicks.remove(nick)
    self.updatenicklist()
  def updatenicklist(self):
    self.window.nicks.setText('\n'.join(sorted(self.nicks)))
    #todo: nick formatting options, sorting by status options, right-clickable, hoverable, size according to longest nick option (can we even do this?)

class Channelwindow(QWidget):
  def alignstuff(self):
    self.input.setFixedWidth(max(0, self.width()-self.nicks.width()))
    self.input.move(0, self.height()-config.fontheight)
    self.output.setFixedSize(max(0, self.width()-self.nicks.width()), max(0, self.height()-config.fontheight))
    self.nicks.setGeometry(self.width()-self.nicks.width(), 0, config.nickswidth, self.height())

  def __init__(self, *args):
    QWidget.__init__(self, mainwin, *args)
    self.setWindowFlags(Qt.Window )
    self.resize(640, 320)
    self.input = QTextEdit(self)            # qlineedit
    self.input.setAcceptRichText(False)
    self.input.setFontFamily(config.fontfamily)
    self.input.setFixedSize(self.width(), config.fontheight)
    self.output = QTextEdit(self)
    self.output.setReadOnly(True)
    self.output.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
    self.input.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
    self.output.move(0, 0)
    self.nicks = QTextEdit(self)
    self.nicks.setReadOnly(True)
    self.nicks.setGeometry(self.width()-config.nickswidth, 0, config.nickswidth, self.height())
    self.inputhistory = []
    self.vs = self.output.verticalScrollBar()
    self.cur = QTextCursor(self.output.document())
    workspace.addWindow(self)
    self.show()

  def resizeEvent(self, event):
    self.alignstuff()
    QWidget.resizeEvent(self, event)
  def moveEvent(self, event):
    self.alignstuff()
    QWidget.moveEvent(self, event)

if os.path.exists(configpath):
  config = cPickle.load(open(configpath))
else:
  config = makeconfig()

redformat = QTextCharFormat()
redformat.setForeground(QBrush(Qt.red))
blackformat = QTextCharFormat()
blackformat.setForeground(QBrush(Qt.black))

mainwin = QMainWindow()
workspace = QWorkspace()
mainwin.setCentralWidget(workspace)

menubar = mainwin.menuBar()
mnufile = menubar.addMenu('&File')
mnuclose = mnufile.addAction('&Close')
mnunew = mnufile.addMenu("&New")
mnunewclient = mnunew.addAction("&Server window")
mnunewclient.connect(mnunewclient, SIGNAL('triggered()'), newclient)

clients = set([Client()])

mainwin.showMaximized()

class identd(protocol.Protocol):
  def dataReceived(self, data):
    self.transport.write(data.strip() + " : USERID : UNIX : " + config.identid + "\r\n" )
    #todo: configure id per network
identf = protocol.ServerFactory()
identf.protocol = identd
try: reactor.listenTCP(113,identf)
except:
  print "Could not run identd server."
  #todo: show it in the gui 

reactor.runReturn()
sys.exit(app.exec_())
