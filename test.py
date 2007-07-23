#/usr/bin/env python2.5

print "Loading modules..."

from chiral.core import stats
from chiral.inet import reactor

from chiral.http.wsgihttpd import HTTPServer
from chiral.shell import ChiralShellServer
from paste.urlmap import URLMap
from paste.httpexceptions import HTTPNotFound

from paste.pony import PonyMiddleware
from chiral.http.introspect import IntrospectorApplication
from chiral.web.comet import CometClock
from chiral.web.servers import StaticFileServer



print "Initializing..."

application = URLMap()
application.update({
	"/pony": PonyMiddleware(HTTPNotFound()),
	"/introspector": IntrospectorApplication(),
	"/home": StaticFileServer("/home/jacob"),
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
