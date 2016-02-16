from PyQt4.QtCore import *
from PyQt4.QtGui import *
import sys

class Widget(QWidget):
    def __init__(self, parent=None):
       super(Widget, self).__init__(parent)
       
       # Create a Text edit widget and lay it out...

       self.textedit = QTextEdit()
       self.layout = QVBoxLayout(self)
       self.layout.addWidget(self.textedit)

       self.setLayout(self.layout)

       # Install an event filter for the QTextEdit
       self.textedit.installEventFilter(self)  # all events will call self.eventFilter(self.textedit, event)

    def eventFilter(self, obj, ev): # http://qt.nokia.com/doc/4.5/qobject.html#eventFilter
       if ev.type() == QEvent.KeyPress: # List of events: http://qt.nokia.com/doc/4.5/qevent.html#Type-enum
          print "Pressed key: " + str(ev.key()) + " in widget " + str(obj)
          
          # If we return True, it's not possible to enter text in the TextEdit.
          # It's possible to check if event.key() == Qt.Key_Return and then return True and emit a signal, otherwise return False

          # From the documentation:
          # In your reimplementation of this function, if you want to filter the 
          # event out, i.e. stop it being handled further, return true; otherwise return false.
          return True 
 
       QWidget.eventFilter(self,  obj,  ev)
       return False 

app = QApplication(sys.argv)
widget = Widget()
widget.show()
sys.exit(app.exec_())
