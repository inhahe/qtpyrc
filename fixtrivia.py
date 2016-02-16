import pickle
trivia = pickle.load(open("trivia2.pkl"))
for cat in trivia.values():
  for topic in cat.values():
    for i, question in enumerate(topic[1]):
      q, a = question
      if len(a)>2:
        topic[1][i] = (q, (a[0],) + (" - ".join(a[1:]),))
pickle.dump(trivia, open("trivia3.pkl","w"))

