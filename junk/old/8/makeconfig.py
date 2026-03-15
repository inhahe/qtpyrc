#!/usr/bin/env python
import os
from ruamel.yaml import YAML

def makeconfig():
  config = {
    'multiline': False,
    'cmdprefix': '/',
    'nickswidth': 100,
    'mode': 'maximized',
    'nickname': 'inhahe2',
    'identid': 'inhahe',
    'fontfamily': 'Courier New',  # can we include fixedsys somehow?
    'fontheight': 20,
    'fgcolor': 'black',
    'bgcolor': 'white',
    'menuitemh': 10,
    'menuitemw': 50,
  }

  yaml = YAML()
  yaml.default_flow_style = False
  with open(configpath, 'w') as f:
    yaml.dump(config, f)
  return config

mypath = os.path.dirname(__file__)
configpath = os.path.join(mypath, "config.yaml")
makeconfig()
