import pprint, pickle
trivia = pickle.load(open('trivia2.pkl'))
open('pp.out.txt','w').write(pprint.pformat(trivia))