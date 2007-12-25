#!/usr/bin/env python2.5

print "Loading modules..."

import sys

from chiral.core import stats
from chiral.net import reactor

from chiral.web.httpd import HTTPServer
from chiral.shell import ChiralShellServer
from paste.urlmap import URLMap
from paste.httpexceptions import HTTPNotFound
from chiral.core.threadpool import run_in_thread

print "Loading applications..."

from paste.pony import PonyMiddleware
from chiral.web.introspector import Introspector
from chiral.web.comet import CometClock, CometLibServer
from chiral.web.servers import StaticFileServer
from chiral.web.framework import *

print "Initializing..."

@coroutine_page()
@use_template("genshi", "chiral.web.templates.helloworld")
def asyncpagetest():
	yield
	raise StopIteration({ "foo": "Test Page" })

introspector = Introspector()

application = URLMap()
application.update({
	"/pony": PonyMiddleware(HTTPNotFound()),
	"/introspector": Introspector(),
	"/static": StaticFileServer("/home/jacob/code/chiral/static"),
	"/asyncpagetest": asyncpagetest,
	"/cometlib": CometLibServer(),
	"/": CometClock()
})

HTTPServer(
	bind_addr = ('', 8081),
	application = application
).start()

ChiralShellServer(
	bind_addr = ('', 9123)
).start()

print "Running..."

if "-prof" in sys.argv:
	import cProfile
	def run():
		reactor.run()
	cProfile.run(run.func_code, "test.prof")
else:
	reactor.run()

stats.dump()
