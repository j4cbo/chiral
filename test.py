#/usr/bin/env python2.5

print "Loading modules..."

from chiral.core import stats
from chiral.inet import reactor

from chiral.http.wsgihttpd import HTTPServer
from chiral.shell import ChiralShellServer

from paste.pony import PonyMiddleware
from chiral.http.introspect import IntrospectorApplication
from chiral.web.comet import CometClock

print "Initializing..."

def app(environ, start_response):
	"""Simplest possible application object"""
	print repr(environ)
	start_response('200 OK', [('Content-Type', 'text/html')])
	return ['Hello world!\n']


HTTPServer(
	bind_addr = ('', 8081),
	application = PonyMiddleware(IntrospectorApplication(CometClock()))
)

ChiralShellServer(
	bind_addr = ('', 9123)
)

print "Running..."

reactor.run()

stats.dump()
