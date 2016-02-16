from twisted.protocols import protocol
from twisted.words import service, ircservice
from twisted.internet import passport

class SimpleService(service.Service):
     """A simple IRC service that creates users on the fly."""

     def removePerspective(self, name):
         if self.participants.has_key(name):
             del self.participants[name]
             self.application.authorizer.removeIdentity(name)

     def createParticipant(self, name):
         if not self.participants.has_key(name):
             log.msg("Created New Participant: %s" % name)

     def getPerspectiveNamed(self, name):
         if self.participants.has_key(name):
             raise service.WordsError, "user exists"
         else:
             p = service.Participant(name)
             p.setService(self)
             ident = passport.Identity(name, self.application)
             ident.setPassword("ugly hack")
             self.application.authorizer.addIdentity(ident)
             p.setIdentity(ident)
             ident.addKeyForPerspective(p)
             self.participants[name] = p
             return p


class IRCChatter(ircservice.IRCChatter):

     passwd = "ugly hack" # remove this to force user to send password

     def connectionLost(self):
         ircservice.IRCChatter.connectionLost(self)
         print self.nickname
         self.service.removePerspective(self.nickname)


class IRCGateway(protocol.Factory):

     def __init__(self, service):
         self.service = service

     def buildProtocol(self, connection):
         """Build an IRC protocol to talk to my chat service.
         """
         i = IRCChatter()
         i.service = self.service
         return i


def main():
     """Run an IRC server"""
     from twisted.internet import main
     app = main.Application("irc")
     svc = SimpleService("twisted.words", app)
     irc = IRCGateway(svc)
     app.listenTCP(6667, irc)
     app.run(0)


if __name__ == '__main__':
     main()

