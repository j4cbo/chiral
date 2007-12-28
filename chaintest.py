from chiral.core import coroutine

def chain():
	yield
	raise coroutine.CoroutineRestart(chain())

print "..."
coroutine.Coroutine(chain(), autostart=True)
