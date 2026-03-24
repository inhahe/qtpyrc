I wrote this in 2009, and then lost the source for 6 years and then found it again. 

It's an IRC client written in Python 2.x, using Twisted and PyQt (version 4 I think). I thought the existence of a free graphical (or even non-graphical) IRC client written in Python was sorely lacking out there. 

It's not really finished, it's very rudimentary. It doesn't even have configuration dialogs yet, for example. 

Not too long before I rediscovered the source to this version, I started over from scratch, so I will be posting the second incarnation in another repository after this one, called qttmwirc. (I had originally called it qtpyrc also, but then changed its name to qttmwirc.)

This version is, I think, less developed than the second version, though I think it does have plugin support, which the second version lacks. I know I wrote a trivia plugin for it (which will be included in this repository) and I think vaguely recall coding the support for such plugins in the client.   

There may be files included that aren't really necessary for the project.

I'm posting these two projects so that people can contribute to them and do the work of making them fully fledged that I'm too lazy to do. =) Maybe someone can even bring together the best parts of both projects into one of these two projects or into a new project. (It would be sad if I'm not the owner of said new project, though. :)) 

It uses t.i.p.irc, but I think I made some custom modifications to irc.py that the program relies on, hence the inclusion of irc.py in the repository. No idea if that violates Twisted's usage license.
