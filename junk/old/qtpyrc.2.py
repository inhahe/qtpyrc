from PyQt4.QtGui import *
from PyQt4.QtCore import *

import sys, qt4reactor, re
from optparse import OptionParser 

app = QApplication(sys.argv)
qt4reactor.install()
from twisted.internet import protocol, reactor
from twisted.words.protocols import irc
reactor.addSystemEventTrigger('after', 'shutdown', app.quit)
QObject.connect(app, SIGNAL("lastWindowClosed()"), quit)

mode = "maximized" # maximized, cascaded, tiled
textinputheight = 20
nickswidth = 100

class ircclient(irc.IRCClient):
  def signedOn(self):
    self.factory.window.addline("connected")
  def connectionMade(self):
    self.nickname = self.factory.nickname
    irc.IRCClient.connectionMade(self)
    self.factory.window.addline("conection made")
  def connectionLost(self, reason):
    irc.IRCClient.connectionLost(self, reason)
    self.factory.window.addline("connection lost: " + reason)
  def lineReceived(self, line):
    self.factory.window.addline(line)

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

class ircclientfactory(protocol.ClientFactory):
  protocol = ircclient
  nickname = "inhahe2"
  def __init__(self, window, *args):
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

  def inputchanged(self):
    s = str(self.input.toPlainText())
    if self.input.textCursor().position() == len(s) and s.endswith(chr(10)):
      lineinput(self, str(self.input.toPlainText()))
      
  def keypress(self, *args):
    print 1

  def __init__(self, *args):
    QWidget.__init__(self, mainwin, *args)
    self.setWindowFlags(Qt.Window )
    self.resize(640, 320)
    self.input = QTextEdit(self)            # qlineedit
    self.input.setAcceptRichText(False)
    self.input.setFixedSize(self.width(), textinputheight)
    self.connect(self.input, SIGNAL('textChanged()'), self.inputchanged)
    self.connect(self.input, SIGNAL('keyPressEvent'), self.keypress)
    self.output = QTextEdit(self)
    self.output.setReadOnly(True)
    self.output.move(0, 0)
    self.tcpconnection = None
    self.inputhistory = []
    workspace.addWindow(self)
    self.show()
    
  def resizeEvent(self, event): self.alignstuff()
  def moveEvent(self, event): self.alignstuff()

  def addline(self, text):
    self.output.append(text)

def newserver():
  serverwindows.add(Serverwindow())

class Channelwindow(QWidget):
  def alignstuff(self):
    self.input.setFixedWidth(max(0, self.width()-self.nicks.width()))
    self.input.move(0, self.height()-textinputheight)
    self.output.setFixedSize(max(0, self.width()-self.nicks.width()), max(0, self.height()-textinputheight))
    self.nicks.move(self.width()-self.nicks.width(), 0)

  def __init__(self, *args):
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
    self.nicks.move(self.width()-nickswidth)
    self.inputhistory = []
    workspace.addWindow(self)
    self.show()

  def resizeEvent(self, event): self.alignstuff()
  def moveEvent(self, event): self.alignstuff()

def lineinput(window, text):

  window.input.clear()
  
  if text.lstrip().startswith("/"):
    s = text.split(' ', 1)
    cmd = s[0]
    txt = s[1] if len(s)==2 else None
    if cmd=="/server":
      parser = OptionParser()
      parser.add_option('-m')
      parser.add_option('-p')
      try:
        options, args = parser.parse_args(txt.split())
      except:
        raise
        pass # display error
      if options.m:
         pass        # open in new window
      else:
        window.tcpconnection = ircclientfactory(window)
        if len(args) > 1: port = int(args[1])
        elif options.p: port = options.p
        else: port = 6667
        reactor.connectTCP(args[0], port, window.tcpconnection)

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

reactor.runReturn()
sys.exit(app.exec_())
