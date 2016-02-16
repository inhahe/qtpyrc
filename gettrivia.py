import urllib2, re, pickle
trivia = {}
for x in xrange(0, 244, 30):
  print x
  url1 = "http://www.triviachamp.com/Trivia-Index.php?currentrec=" + str(x)
  data1 = ' '.join(urllib2.urlopen(url1).read().split())
  for section, url2 in re.findall(r"\?gamename\s*=\s*(.*?)'.*?href\s*=\s*'(.*?)'.*?HTML GAME", data1):
    print '  ' + section
    data2 = ' '.join(urllib2.urlopen("http://www.triviachamp.com"+url2).read().split())
    for answer, question in re.findall(r'alt\s*=\s*"(.*?)".*?qmark\.gif".*?\>(.*?)\<\s*br', data2):
       trivia.setdefault(section, []).append((question, answer))
    print "    got " + str(len(trivia.get(section, []))) + " questions"
    pickle.dump(trivia, open("trivia.pkl","w"))


