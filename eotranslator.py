#!/usr/bin/env python
#-*- coding: utf-8 -*-
words = s.split()

import re

def splitword(word):
  suffixes = r"(aĵ|ec|ind|ebl|ej|ul|ig|iĝ|in|eg|at|it|ot|ant|int|ont|ad)*(((o|a|e)+(n|j|jn)?)|(u|i|is|as|os))"
  prefixes = r"(mal|ex|pra|bo|dis|eks|fi|ge|mis)*"
  return = re.findall(prefixes+"(.*)"+suffixes)
  
for word in words:
  print splitword("malnova")
  