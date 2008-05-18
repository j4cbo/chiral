#/usr/bin/env python2.5

print "Loading modules..."

from chiral.core import stats, threadpool
from chiral.core.coroutine import as_coro
from chiral.net import reactor, dbus
import time

from chiral.web.httpd import HTTPServer
from chiral.web.introspector import Introspector

print "Running..."

@as_coro
def threadpool_test():
	print "sleeping..."
	yield threadpool.run_in_thread(time.sleep, 1)
	print "done"

#HTTPServer(bind_addr = ('', 8082), application = Introspector())

threadpool_test().start()

reactor.run()
stats.dump()
