#/usr/bin/env python2.5

from chiral.inet import netcore, tcp
from chiral.core import callbacks, stats
from chiral.http import httpd

c = netcore.EpollLooper()

daemon = httpd.HTTPServer(c, bind_addr = ('', 8082))

def reloader():
	c.schedule(reloader, 1)

	print "Reloading HTTP server..."
	try:
		reload(httpd)
		print "Reload complete."
	except Exception, e:
		print "Reload failed: %s" % e

#c.schedule(reloader, 5)

c.run()

stats.dump()
