#/usr/bin/env python2.5

print "Loading..."

from chiral.inet import netcore
from chiral.core import stats, tasklet
from chiral.http.wsgihttpd import HTTPServer
from chiral.shell import ChiralShellServer
from paste.pony import PonyMiddleware
from chiral.http.introspect import IntrospectorApplication
from chiral.web.comet import CometClock

c = netcore.EpollLooper()

def app(environ, start_response):
	"""Simplest possible application object"""
	print repr(environ)
	start_response('200 OK', [('Content-Type', 'text/html')])
	return ['Hello world!\n']


HTTPServer(
	c,
	bind_addr = ('', 8082),
	application = PonyMiddleware(IntrospectorApplication(CometClock(c)))
)

ChiralShellServer(c, bind_addr = ('', 9123))

print "Running..."

c.run()

stats.dump()
