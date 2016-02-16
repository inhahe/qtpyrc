#!/usr/bin/env python
import pickle
import os
from PyQt4.Qt import Qt

class Config: pass
def makeconfig():
  config = Config()
  config.multiline = False
  config.cmdprefix = "/"
  config.nickswidth = 100
  config.mode = "maximized"
  config.nickname = "inhahe2"
  config.identid = "inhahe"
  config.fontfamily = "Courier New" # can we include fixedsys somehow?
  config.fontheight = 20
  config.fgcolor = Qt.black
  config.bgcolor = Qt.white
  config.menuitemh = 10
  config.menuitemw = 50

  pickle.dump(config, open(configpath, "w"))
  return config

mypath = os.path.dirname(__file__)
configpath = os.path.join(mypath, "config.pkl")
makeconfig()