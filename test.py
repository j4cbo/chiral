#!/usr/bin/env python2.5

print "Loading modules..."

from chiral.core import stats
from chiral.net import reactor

from chiral.web.httpd import HTTPServer
from chiral.shell import ChiralShellServer
from paste.urlmap import URLMap
from paste.httpexceptions import HTTPNotFound

print "Loading applications..."

from paste.pony import PonyMiddleware
from chiral.web.introspector import Introspector
from chiral.web.comet import CometClock, CometLibServer
from chiral.web.servers import StaticFileServer
from chiral.web.framework import *

print "Initializing..."

@coroutine_page()
@use_template("genshi", "chiral.web.templates.asdf")
def asyncpagetest():
	yield reactor.schedule(delay = 1)
	raise StopIteration({ "foo": "Test Page" })

application = URLMap()
application.update({
	"/pony": PonyMiddleware(HTTPNotFound()),
	"/introspector": Introspector(),
	"/static": StaticFileServer("/home/jacob/code/chiral/static"),
	"/tp": asyncpagetest,
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

import cProfile

def run():
	reactor.run()

cProfile.run(run.func_code, "test.prof")

stats.dump()
