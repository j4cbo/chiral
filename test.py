#/usr/bin/env python2.5

print "Loading modules..."

from chiral.core import stats
from chiral.inet import reactor

from chiral.web.httpd import HTTPServer
from chiral.shell import ChiralShellServer
from paste.urlmap import URLMap
from paste.httpexceptions import HTTPNotFound

print "Loading applications..."

from paste.pony import PonyMiddleware
from chiral.web.introspector import Introspector
from chiral.web.comet import CometClock
from chiral.web.servers import StaticFileServer
from chiral.web.framework import *

print "Initializing..."

@tasklet_page()
@use_template("genshi", "chiral.web.templates.asdf")
def asyncpagetest():
	yield
	raise StopIteration({ "foo": "Test Page" })

application = URLMap()
application.update({
	"/pony": PonyMiddleware(HTTPNotFound()),
	"/introspector": Introspector(),
	"/home": StaticFileServer("/home/jacob"),
	"/tp": asyncpagetest,
	"/": CometClock()
})

HTTPServer(
	bind_addr = ('', 8081),
	application = application
)

ChiralShellServer(
	bind_addr = ('', 9123)
)

print "Running..."

reactor.run()

stats.dump()
