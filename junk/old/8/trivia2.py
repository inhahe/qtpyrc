import pickle
inf = open('trivia.txt')
trivia = pickle.load(open('trivia.pkl'))
newtrivia = {}
for line in inf:
  if '\x09' in line:
    topic = line.split('\x09')[0]
    desc = ''
    if '- ' in topic:
      topic, desc = tuple(map(str.strip, topic.split("- ", 1)))
    newtopic = [(question, tuple(answer.split(' - ', 1))) if ' - ' in answer else (question, (answer, '')) for question, answer in trivia[topic]]
    newtrivia[cat][topic] = desc, newtopic
  else:
    if line.strip():
      cat = line.strip()
      newtrivia[cat] = {}

pickle.dump(newtrivia, open('trivia2.pkl','w'))

#fix &#*
#fix who's
#fix it's
#fix ?'
#fix </p - re-scrape
#Which of these islands was not a penal colony?  missing choices
