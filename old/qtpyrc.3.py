#todo
#show warning if no ping received within x seconds
#change window icon
#newyork.ny.us.undernet.org -- on notification to use /motd, has characters that don't
#show up right?
#exit cleanly on ctrl-brk?
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
textinputheight = 20 # todo: make this height on font, for non-multiline

class Config: pass
#todo: remember window positions, etc

def makeconfig():
  config = Config()
  config.multiline = False
  config.cmdprefix = "/"
  config.nickswidth = 100
  config.mode = "maximized"
  config.nickname = "inhahe2"
  config.identid = "inhahe"
  config.fontfamily = "Courier New"
  cPickle.dump(config, open(configpath, "w"))
  return config

def lineinput(window, inputwidget, text):
  inputwidget.setText("")
  
  print "text: ",repr(text)
  
  if text.startswith(config.cmdprefix):
    docommand(*(text[len(config.cmdprefix):].split(None, 1) + [window]))
  else:
    docommand("say", text, window)  
  
class Commands:
  def join(text, window):
    params = text.split(None)
    if len(params) == 1:
      window.factory.conn.join(params[0])
    elif len(params) == 2:
      window.factory.conn.join(*params)
    else:
      #todo
      #show error message? pop up window? show help?
      pass
  def say(text, curwin):
    if curwin.type == "server":
      pass
      # todo: show error
    elif curwin.type == "channel":
      curwin.conn.say(curwin.channel, text)
    elif curwin.type == "query":
      msg(text, curwin)      
  def msg(text, curwin):
      curwin.conn.privmsg("%s!%s@%s" %
                          (curwin.server.nick, curwin.server.ident, curwin.server.hostmask),
                          recip, message)
  def server(text, window):
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
      window.factory = ircclientfactory(window)
      if len(args) > 1: port = int(args[1])
      elif options.p: port = options.p
      else: port = 6667
      reactor.connectTCP(args[0], port, window.conn)

class identd(protocol.Protocol):
  def dataReceived(self, data):
    self.transport.write(data.strip() + " : USERID : UNIX : " + config.identid + "\r\n" )
    #todo: configure id per network/server/connection

class ircclient(irc.IRCClient):
  def connectionMade(self):
    irc.IRCClient.connectionMade(self)
    self.factory.window.addlinef('<font color="red">[Connected]</font>')
    self.factory.conn = self
  def connectionLost(self, reason):
    irc.IRCClient.connectionLost(self, reason)
    self.factory.window.addline("connection lost: " + reason.getErrorMessage())

  def bounce(self, info):
    #we have to detect if it gives us a domain name because 005 can mean different things
    self.factory.window.addline("[005] "+info)
    pass
  
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
    if command in ('001', '002', '003', '004', '005', '302', '303', '301', '305', '306',
                   '251', '252', '253', '254', '255', '375', '372', '376', 'ERROR', '265',
                   '266'):
      self.factory.window.addline(message + trailing)
    else:
      print line #debug
    
  def noticed(self, user, channel, message):
    pass

  def joined(self, channel):
    self.factory.channels[channel] = Channel()
    
    
    print 2
  
  def privmsg(self, user, channel, message):
    usernick, userathost = user.split("!", 1)
    if channel==self.factory.nickname: #this is a private message
      if userathost not in self.factory.queries:
        self.factory.queries[userathost] = Querywindow()
      self.factory.queries[userathost].addline("<%s> %s" % (usernick, message))
    else: # this is a channel message
      self.factory.channels[channel].addline("<%s> %s" % (usernick, message))
      
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
  def __init__(self, window, *args):
    self.protocol.nickname = config.nickname
    self.window = window
  
def quit():
  reactor.stop()
  app.quit()      
  sys.exit()

class Serverwindow(QWidget):
  def alignstuff(self):
    self.input.setFixedWidth(self.width())
    self.input.move(0, self.height()-textinputheight)
    self.output.setFixedSize(self.width(), max(0, self.height()-textinputheight))
    
  def __init__(self, *args):
    QWidget.__init__(self, mainwin, *args)
    self.setWindowFlags(Qt.Window )
    self.resize(640, 320)
    self.input = QTextEdit(self)            # qlineedit
    self.input.setAcceptRichText(False)
    self.input.setFixedSize(self.width(), textinputheight)
    #self.connect(self.input, SIGNAL('textChanged()'), self.inputchanged)
    self.output = QTextEdit(self)
    self.output.setReadOnly(True)
    self.output.move(0, 0)
    self.factory = None
    self.inputhistory = []
    workspace.addWindow(self)
    self.input.installEventFilter(self)
    self.type = "server"
    self.output.setFontFamily(config.fontfamily)
    self.input.setFontFamily(config.fontfamily)
    self.show()
    
  def eventFilter(self, obj, event):
    if event.type()==QEvent.KeyPress:
      if event.text() == '\r':
        m = event.modifiers()
        if not (config.multiline and m and Qt.ShiftModifier):
          lineinput(self, obj, str(obj.toPlainText()))
          return True
    return False
  
  def resizeEvent(self, event): self.alignstuff()
  def moveEvent(self, event): self.alignstuff()

  def addline(self, text):
    self.output.insertPlainText(text+"\n")
  def addlinef(self, html):
    self.output.insertHtml(html+"<br>")

def newserver():
  serverwindows.add(Serverwindow())

def docommand(command, text, curwin):
  command = command.lower()
  if hasattr(Commands, command) and not command.startswith("_"): #potentially dangerous if we allow scripting?
    getattr(Commands, command).im_func(text, curwin)
  
class Inputwidget(QTextEdit):
  def __init__(self):
    QTextEdit.__init__(self)
  #def keyPressEvent(self,  event):
  # if self.__sendMessageOnReturn:
  #    if event.key() == Qt.Key_Return:
  #      if event.modifiers() == Qt.ShiftModifier:
  #        event = QKeyEvent(QEvent.KeyPress,  Qt.Key_Return,  Qt.NoModifier)
  #      else:
  #        self.sendmessage()
  #        return
  #  QTextEdit.keyPressEvent(self,  event)
  #def sendmessage(self):
  #  text = self.getText()
  #  self.clear()
  #  if text.startswith(config.cmdprefix):
  #    docommand(*(text[len(config.cmdprefix):].split(None, 1) + [self.curwin]))
  #  else:
  #    docommand("say", text, self.curwin)  

class Channel:
  def __init__(self):
    self.window = Channelwindow()
  def addline(text):
    self.window.output.insertPlainText(text + '\n')

class Channelwindow(QWidget):
  def alignstuff(self):
    self.input.setFixedWidth(max(0, self.width()-self.nicks.width()))
    self.input.move(0, self.height()-textinputheight)
    self.output.setFixedSize(max(0, self.width()-self.nicks.width()), max(0, self.height()-textinputheight))
    self.nicks.move(self.width()-self.nicks.width(), 0)

  def __init__(self, *args):
    
    print 4

    QWidget.__init__(self, mainwin, *args)
    self.setWindowFlags(Qt.Window )
    self.resize(640, 320)
    self.input = QTextEdit(self)            # qlineedit
    self.input.setAcceptRichText(False)
    self.input.setFixedSize(self.width(), textinputheight)
    self.output = QTextEdit(self)
    self.output.setReadOnly(True)
    self.output.move(0, 0)
    self.nicks = QTextEdit(self)
    self.nicks.setReadOnly(True)
    self.nicks.move(self.width()-config.nickswidth)
    self.inputhistory = []
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

mainwin = QMainWindow()
workspace = QWorkspace()
mainwin.setCentralWidget(workspace)

menubar = mainwin.menuBar()
mnufile = menubar.addMenu('&File')
mnuclose = mnufile.addAction('&Close')
mnunew = mnufile.addMenu("&New")
mnunewserver = mnunew.addAction("&Server window")
mnunewserver.connect(mnunewserver, SIGNAL('triggered()'), newserver)

serverwindows = set()
channelwindows = set()

newserver()

mainwin.showMaximized()

identf = protocol.ServerFactory()
identf.protocol = identd
try: reactor.listenTCP(113,identf)
except:
  print "Could not run identd server."
  #todo: show it in the gui 

reactor.runReturn()
sys.exit(app.exec_())
