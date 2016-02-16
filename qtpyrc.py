#structure:
#  Client instances - each client is associated with a main server window and all its attendant windows, like in mIRC
#    window <- main server window, the gui part
#    channels 
#    queries
#    conn <- underlying server connection, the IRCClient instance.  this can be removed/replaced/non-existent for a given Client instance
#    protocol <- underlying server connection, the IRCClient instance.  is this always the same as conn? beats me.
#                this member is automatically provided by Twisted Matrix.
#
#  Channel/Query instances
#    window <- the gui part
#    client <- points to its parent client instance
#    other info associated with a channel/query window that's not directly GUI-related is stored in the Channel instance,
#     not the window instance
#
#  IRCClient instances
#    factory <- this points to its parent Client instance. it's called factory and not client because this member is 
#               automatically provided by Twisted Matrix.
#    window  <- points to parent Client instance's window.  just for convenience.
#    channels <- points to parent Client instance's channels. just for convenience.
#    queries <- points to parent Client instance's queries.  just for convenience.
#    nickname <- nickname currently being used.  this member is automatically used (and changed? i dunno..) by twisted.words.protocols.irc
#
#  Script instances
#    module <- the script's entire module
#    script <- the script module's running Script() instance

from PyQt4.QtGui import *
from PyQt4.QtCore import *

import sys, qt4reactor, re, cPickle, os, types
import traceback
if __name__ == '__main__':
  qt4reactor.install() #why don't i need this?

from optparse import OptionParser 
from twisted.internet import protocol, reactor
from twisted.words.protocols import irc

class Config: pass

mirccolors = [Qt.white, Qt.black, Qt.darkBlue, Qt.darkGreen, Qt.red, Qt.darkRed, Qt.darkMagenta, QColor("#FC7F00"),
              Qt.yellow, Qt.green, Qt.darkCyan, Qt.cyan, Qt.blue, Qt.magenta, QColor("#7F7F7F"), QColor("#D2D2D2")]
             #the named colors may need corrected

mircre = re.compile(""" 
                      ( 
                        (?:
                          \x03
                          (?:
                            (\d\d?)
                            (?:,(\d\d?))?
                          )?
                        )
                        |\x02|\x1F|\x16|\x0F|^
                      )
                      ([^\x02\x1F\x16\x03\x0F]*)
                    """, re.VERBOSE)  

usersplit = re.compile("(?P<nick>.*?)!(?P<ident>.*?)@(?P<host>.*)").match
#can access  for example as:
#  nickidhost = "foo!bar@baz"
#  nick, host = usersplit(nickidhost).group("nick", "host")

def loadconfig():
  global redformat, defaultformat #<-- too criminal?
  configpath = os.path.join(mypath, "config.pkl")
  config = cPickle.load(open(configpath))
  redformat = QTextCharFormat()
  redformat.setForeground(QBrush(Qt.red))
  redformat.setBackground(QBrush(config.bgcolor))
  defaultformat = QTextCharFormat()
  defaultformat.setForeground(QBrush(config.fgcolor))
  defaultformat.setBackground(QBrush(config.bgcolor))
  return config

#class Switchbar(Bar):
#  def __init__(self):

#QToolBar.__init__(self, app.mainwin)

class Window(QWidget):
 
  def lineinput(self, text):
    self.input.setText("")
    if text.startswith(config.cmdprefix):
      docommand(self, *(text[len(config.cmdprefix):].split(" ", 1)))
    else:
      docommand(self, "say", text)
      
  def alignstuff(self):
    self.input.setFixedWidth(self.width())
    self.input.move(0, self.height()-config.fontheight)
    self.output.setFixedSize(self.width(), max(0, self.height()-config.fontheight))
    
  def __init__(self, client):
    QWidget.__init__(self, app.mainwin)
    self.setWindowFlags(Qt.Window)
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
    self.input.installEventFilter(self)
    self.output.setFontFamily(config.fontfamily)
    self.cur = QTextCursor(self.output.document())
    app.mainwin.workspace.addWindow(self)
    self.showMaximized() #todo: config. also: does this result in alignstuff being called twice?
        
  def addline(self, line):  # i would create a separate function to take a string with mirc colors and return 
                              # a qt document fragment or block or something, but
                              # apparently you can't insert documents, and i cannot
                              # figure out how to create a cursor from a document fragment
                              # so i can't colorize one. 
        
    stb = self.vs.value() == self.vs.maximum()
    if self.cur.position():
      self.cur.insertText('\n')
    #start parsing mirc codes
    bold = underline = False
    fg = config.fgcolor
    bg = config.bgcolor
    tf = QTextCharFormat()
    cur = self.cur
    
    #todo: color 99 is transparent according to mIRC documentation, but it doesnt work in mirc
    #colors > 15 get modulo 16 according to doc, but it doesnt work in mirc
        
    for code, fgs, bgs, text in mircre.findall(line):
      if code in "\x03\x0F":
        fg, bg = config.fgcolor, config.bgcolor
        tf.setForeground(fg)
        tf.setBackground(bg)
        if code=="\x0F":
          underline = False
          reverse = False
          bold = False
          tf.setFontUnderline(False)
          tf.setFontWeight(QFont.Normal)
      elif code.startswith("\x03"): #color
        fgi = int(fgs)
        if "," in code:     #fg,bg
          bgi = int(bgs)
          bg = mirccolors[(bgi % 16) if fgi < 99 else config.bgcolor] 
        fg = mirccolors[(fgi % 16)] if fgi < 99 else bg # no idea if i'm interpreting the doc right
        tf.setForeground(fg)                                        
        tf.setBackground(bg)

      elif code=="\x1F": #underline
        underline = not underline
        tf.setFontUnderline(underline)      

      elif code=="\x16": #let's do it the cool way
        fg, bg = bg, fg
        tf.setForeground(fg)
        tf.setBackground(bg)

      elif code=="\x02": #bold
        bold = not bold
        tf.setFontWeight(QFont.Bold if bold else QFont.Normal)
      
      elif code=="":
        tf.setForeground(fg)
        tf.setBackground(bg)

      cur.insertText(text, tf)
      cur.movePosition(cur.End)

    if stb:# we only want to scroll to the bottom if it had already been scrolled to the bottom
      self.vs.setValue(self.vs.maximum()) 
    
  def redmessage(self, text):
    if self.cur.position():
      self.addlinef("-", redformat)
    self.addlinef(text, redformat)
       
  def addlinef(self, text, format):
    stb = self.vs.value() == self.vs.maximum()
    if self.cur.position():
      self.cur.insertText('\n'+text, format)
    else:
      self.cur.insertText(text, format)
    self.cur.movePosition(self.cur.End)
    if stb:
      self.vs.setValue(self.vs.maximum())

  def eventFilter(self, obj, event):
    if event.type()==QEvent.KeyPress:
      if event.text() == '\r':
        m = event.modifiers()
        if not (config.multiline and m and Qt.ShiftModifier):
          self.lineinput(str(obj.toPlainText()))
          return True
    return False
  
  def resizeEvent(self, event):
    self.alignstuff()
  def moveEvent(self, event):
    self.alignstuff()
    QWidget.moveEvent(self, event)

class Commands:

  def join(window, text): #todo: check if connected first
    params = text.split(None)
    if 1 <= len(params) <= 2:
      window.client.conn.join(*params)
    else:
      window.redmessage('[Error: /join takes 1 or 2 parameters]')
      #todo: include link to help on /join in error message 

  def say(window, text):
    if window.type == "server":
      window.redmessage("[Error: Can't talk in a server window]")
    elif window.type == "channel":
      window.client.conn.say(window.channel.name, text)
      window.addline("<%s> %s" % (window.client.protocol.nickname, text))
    elif window.type == "query":
      window.client.conn.say(window.remotenick, text)
      window.addline("<%s> %s" % (window.client.protocol.nickname, text))

  def msg(window, text):
    recip, text = text.split(" ", 1)
    window.conn.msg(recip, text)
    recip = window.conn.irclower(recip)
    if recip in window.client.queries:
      window.client.queries[recip].addline("<%s> %s" & (window.client.nickname, recip))

  def server(window, text):
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
      window.client.hostname = args[0]
      window.client.port = port
      try:
        window.client.reactor.disconnect()
        window.client.protocol.transport.loseConnection() # if you try to change servers while it's trying to connect nothing happens. maybe this will fix it?
      except AttributeError: pass            # less ugly way to do this?
      window.redmessage("[Connecting to %s]" % args[0])
      window.client.reactor = reactor.connectTCP(args[0], port, window.client, 60)
      
  def nick(window, text):
    n = text.strip()
    try:
      window.client.conn.setNick(n)
    except:
      client.window.protocol.nickname = n # this is probably all wrong

class IRCClient(object, irc.IRCClient):

  def networkChanged(self, networkname):
    self.factory.networkname = self.factory
    #todo: wipe out channels

  def connectionMade(self):
    irc.IRCClient.connectionMade(self)
    self.factory.conn = self
    self.window.redmessage('[Connected to %s]' % self.factory.hostname)
    self.window.conn = self
     
  #def connectionFailed(self, reason):
    
  def connectionLost(self, reason): #according to api doc, this is always a non-clean exit.
    irc.IRCClient.connectionLost(self, reason)
    self.window.redmessage('[Connection lost: %s]' % reason.getErrorMessage())
    #self.factory.reactor.disconnect()
    #self.factory.protocol.transport.loseConnection() # trying not to get weird error where nickname already in use if connection failed
    self.factory.reactor = reactor.connectTCP(self.factory.hostname, self.factory.port, self.factory, 60) #todo: only if config says to do this
    self.window.redmessage("[Connecting to %s]" % self.factory.hostname) # <- redundancy is bad.
    self.window.setWindowTitle("[not connected] - " + self.nickname)
    
  def bounce(self, server, port):
    print "bounced!" #todo
    
  def irc_unknown(self, prefix, command, params):
    self.window.addline(' '.join(params[1:])) # make sure we don't throw away a param here for any command    

  def irc_RPL_WELCOME(self, prefix, params):
    network = params[1].split()[3]
    if network != self.factory.network:
      self.networkChanged(network)
    self.factory.network = network
    self.client.window.setWindowTitle(network + " - " + self.nickname)

  def handleCommand(self, command, prefix, params):
    irc.IRCClient.handleCommand(self, command, prefix, params)
    if command in ('RPL_WELCOME RPL_YOURHOST RPL_CREATED RPL_MYINFO'
                   ' RPL_ISON RPL_USERHOST RPL_LUSERCLIENT RPL_LUSERUNKNOWN RPL_LUSERME'
                   ' RPL_ADMINME RPL_ADMINLOC RPL_STANTSONLINE RPL_TRYAGAIN ERROR 265 266'
                   ' RPL_MOTD RPL_ENDOFMOTD RPL_LUSEROP RPL_LUSERCHANNELS RPL_MOTDSTART'
                   ' RPL_ISUPPORT'):
      self.window.addline(' '.join(params[1:])) # make sure we don't throw away a param here for any command
    else:
      print "irc known: ", (command, params) #debug
    
  def noticed(self, user, channel, message):
    #todo
    pass

  def joined(self, chname):
    chnlower = self.irclower(chname)
    if chnlower in self.channels:
      self.channels[chnlower].rejoined()
    else:
      self.channels[chnlower] = Channel(self.factory, chname)
    
  def names(self, chname, names):
    chnlower = self.irclower(chname)
    for nick in names:
      try:
        self.channels[chnlower].addnick(nick)
      except:  #if this happened our server is being weird
        raise #debug
        pass 
    
  def privmsg(self, user, message):
    nick, ident, host = irc.usersplit(user).groups()
    if (ident, host) not in self.queries:
      self.queries[ident, host] = Query(self.factory, nick)
    self.queries[ident, host].window.addline("<%s> %s" % (nick, message))
      
  def chanmsg(self, user, channel, message):
    self.factory.channels[self.irclower(channel)].window.addline("<%s> %s" % (self.nickname, message))
    #todo: redundancy is bad

  def userRenamed(self, oldname, newname):
    loldname = self.irclower(oldname)
    if (self.network, loldname) in self.client.queries:
      self.client.queries[self.network, self.irclower(newname)] = query[self.network, loldname]
      self.queries[self.network, self.irclower(newname)].nick = newname
   
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
  def nickChanged(self, nick):
  """
  
  
  
class Script: 
  def __init__(self, module, script):
    self.module = module
    self.script = script

class Client(protocol.ClientFactory): 
  protocol = IRCClient                          
  def __init__(self, *args):
    self.protocol.nickname = config.nickname
    self.channels = {}
    self.queries = {}
    self.window = Serverwindow(self)
    self.protocol.window = self.window
    self.protocol.channels = self.channels
    self.protocol.queries = self.queries
    self.network = None
  def clientConnectionFailed(self, connector, reason):
    
    #self.reactor.disconnect()
    #self.protocol.transport.loseConnection() # trying not to get weird error where nickname already in use if connection failed

    self.window.redmessage('[Connection failed: %s]' % reason.getErrorMessage())
    self.reactor = reactor.connectTCP(self.hostname, self.port, self, 60) #todo: only if config says to do this
    self.window.redmessage("[Connecting to %s]" % self.hostname) # <- redundancy is bad.
    
    for script in activescripts:
      obj = getattr(script.script, name, None)
      try:
        if obj and obj(self, connector.factory, reason): #connector.factory works?
          break
      except:
        traceback.print_exc()
  
def newclient():
  clients.add(Client())

def quit():
  reactor.stop()
  app.quit()      
  sys.exit()

class Serverwindow(Window):
  def __init__(self, client):
    Window.__init__(self, client)
    self.type = "server"
    self.setWindowTitle("[not connected] - " + client.protocol.nickname)
    self.show()
    
class Query:
  def __init__(self, client, nick):
    self.nick = nick
    self.window = Querywindow(client)
    self.window.setWindowTitle(nick)

class Querywindow(Window):
  def __init__(self, client):
    Window.__init__(self, client)
    self.type = "query"
    self.show()
    
def docommand(window, command, text):
  command = command.lower()
  if hasattr(Commands, command) and not command.startswith("_"): 
    getattr(Commands, command).im_func(window, text)
    
class Inputwidget(QTextEdit):
  def __init__(self):
    QTextEdit.__init__(self)
  
class Channel:
  def __init__(self, client, name):
    self.nicks = set()
    self.client = client
    self.name = name
    self.window = Channelwindow(client, self)
    self.window.setWindowTitle(name)
  def addnick(self, nick):
    self.nicks.add(nick)
    self.updatenicklist()
  def removenick(self, nick):
    self.nicks.remove(nick)
    self.updatenicklist()
  def updatenicklist(self):
    self.window.nicks.setText('\n'.join(sorted(self.nicks)))
    #todo: nick formatting options, sorting by status options, right-clickable, hoverable, size according to longest nick option (can we even do this?)
  def post(self, message):
    self.client.conn.say(self.name, message) #todo: length check
    self.window.addline("<%s> %s" % (self.client.protocol.nickname, message))
  def rejoined(self):
    pass #todo

class Channelwindow(Window):
  def __init__(self, client, channel):
    Window.__init__(self, client)
    self.nicks = QTextEdit(self)
    self.nicks.setReadOnly(True)
    self.nicks.setGeometry(self.width()-config.nickswidth, 0, config.nickswidth, self.height())
    self.type = "channel"
    self.channel = channel 
    self.show()
    
  def alignstuff2(self): #extend input to under nicks?
    self.input.setFixedWidth(max(0, self.width()-self.nicks.width()))
    self.input.move(0, self.height()-config.fontheight)
    self.output.setFixedSize(max(0, self.width()-self.nicks.width()), max(0, self.height()-config.fontheight))
    self.nicks.setGeometry(self.width()-self.nicks.width(), 0, config.nickswidth, self.height())

class identd(protocol.Protocol):
  def dataReceived(self, data):
    self.transport.write(data.strip() + " : USERID : UNIX : " + config.identid + "\r\n" )
    #todo: configure id per network

def makeapp(args):
  app = QApplication(args)
  app.mainwin = QMainWindow()
  app.mainwin.workspace = QWorkspace()
  app.mainwin.setCentralWidget(app.mainwin.workspace)
  app.mainwin.menubar = app.mainwin.menuBar()
  app.mainwin.mnufile = app.mainwin.menubar.addMenu('&File')
  app.mainwin.mnuclose = app.mainwin.mnufile.addAction('&Close')
  app.mainwin.mnunew = app.mainwin.mnufile.addMenu("&New")
  
  #app.mainwin.addToolBar(Qt.TopToolBarArea, bar)

  
  app.mainwin.mnunewclient = app.mainwin.mnunew.addAction("&Server window")
  app.mainwin.mnunewclient.connect(app.mainwin.mnunewclient, SIGNAL('triggered()'), newclient)
  app.mainwin.showMaximized()
  QObject.connect(app, SIGNAL("lastWindowClosed()"), quit)
  return app

def runidentd():
  identf = protocol.ServerFactory()
  identf.protocol = identd
  try:
    reactor.listenTCP(113,identf)
  except:
    print "Could not run identd server."
    #todo: show it in the gui 
  return identf

def loadscripts():
  scripts = {}
  scriptspath = os.path.join(mypath, "scripts")
  for scriptfn in os.listdir(os.path.join(mypath, "scripts")):
    if not scriptfn.startswith("_"):
      scriptpath = os.path.join(scriptspath, scriptfn)
      if scriptfn.lower().endswith(".py"):
        scriptname = scriptfn[:-3]
      elif os.path.isdir(scriptpath):
        if os.path.exists(os.path.join(scriptpath, "__init__.py")):
          scriptname = scriptfn
      try:
        __import__("scripts."+scriptname)
        script = sys.modules["scripts."+scriptname]
        scripts[scriptname] = Script(script, script.Script(clients))
      except Exception, inst:
        raise #debug
        print 'Could not load script "%s" from "%s" because of error: %s' % (scriptname, scriptpath, inst.message)
        #todo: gui
  return scripts

def makefunc(name, obj):#one way to do closure in Python
  def f(self, *args, **kwargs): 
    for script in activescripts.itervalues():
      obj2 = getattr(script.script, name, None)
      try:
        if obj2 and obj2(self, *args, **kwargs):
          break
      except:
        traceback.print_exc()
    else:
      return obj(self, *args, **kwargs)
  return f
  
for name in dir(IRCClient):
  if not name.startswith('_'):
    obj = getattr(IRCClient, name)
    if callable(obj):
      setattr(IRCClient, name, makefunc(name, obj))

if __name__ == '__main__': 
  mypath = os.path.dirname(__file__)
  config = loadconfig()
  app = makeapp(sys.argv)
  clients = set([Client()]) #todo: auto-connect, etc. 
  scripts = loadscripts()
  activescripts = dict(scripts) #scripts currently running = a copy of all loaded script references
  identf = runidentd()  
  reactor.addSystemEventTrigger('after', 'shutdown', app.quit)
  reactor.runReturn()
  app.exec_()
