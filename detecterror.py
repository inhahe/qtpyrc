import cPickle
import random
trivia = cPickle.load(open("trivia2.pkl", "rb"))


while 1:

      category, topics = random.choice(trivia.items())
      topic, (desc, questions) = random.choice(topics.items())

      e = random.choice(questions)

      try:
                question, (answer, explanation) = e
      except: 
        print question
