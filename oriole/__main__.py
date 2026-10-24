import os
import sys

from .gui import run

code = run()
if sys.stdout is not None:                  # None under pythonw
    sys.stdout.flush()
# os._exit, not sys.exit: at exit pyjavaz tells Micro-Manager to free its objects and waits for its answer, forever
# when Micro-Manager was restarted or closed meanwhile (an Oriole without a window, left running, was seen)
os._exit(code)
