from chiral.inet import reactor
from chiral.core import tasklet
import datetime
import time

_CHIRAL_RELOADABLE = True

class CometPage(tasklet.Tasklet):
	"""
	Helper for handling COMET pages.

	@cvar METHOD_CHUNKED: Send data in chunks delimited by self.delimiter
	@cvar METHOD_JS: Send data (which should be serialized JSON) as calls to self.jsmethod
	@cvar METHOD_MXMR: Send data as a multipart/x-mixed-replace document

	To use CometPage, create a new instance from your WSGI application and pass
	the WSGI-provided environ and start_response, then return the CometPage instance.
	Example:
	"""

	def comet_wsgi_app(environ, start_response):
		return MyCometPage(CometPage.METHOD_CHUNKED, environ, start_response)

	METHOD_CHUNKED, METHOD_JS, METHOD_MXMR = xrange(3)

	def __init__(self, method, environ, start_response, delimiter="\r\n\r\n---\r\n", jsmethod="chiral._comet_handler", content_type="text/plain"):
		self.jsmethod = jsmethod
		self.delimiter = delimiter
		self.method = method

		if method == self.METHOD_CHUNKED:
			outer_content_type = content_type
		elif method == self.METHOD_JS:
			outer_content_type = "text/javascript"
		elif method == self.METHOD_MXMR:
			outer_content_type = "multipart/x-mixed-replace;boundary=ChiralMXMRBoundary"

		self.content_type = content_type

		self.http_connection = environ["chiral.http.connection"]

		start_response('200 OK', [('Content-Type', outer_content_type)])
		environ["chiral.http.set_tasklet"](self)

		tasklet.Tasklet.__init__(self, self.run(), autostart=False)

	def __iter__(self):
		if self.method == self.METHOD_MXMR:
			return iter(["--ChiralMXMRBoundary\r\n"])
		else:
			return iter([""])

	def send_chunk(self, data, content_type=None):
		print "sending %s" % (data, )
		if self.method == self.METHOD_CHUNKED:
			return self.http_connection.sendall("%s%s" % (data, self.delimiter))
		elif self.method == self.METHOD_JS:
			return self.http_connection.sendall("%s(%s)" % (self.jsmethod, data))
		elif self.method == self.METHOD_MXMR:
			data = str(data)
			message = "Content-type: %s\r\n\r\n%s\r\n--ChiralMXMRBoundary\r\n" % (
				content_type or self.content_type,
				data
			)
			return self.http_connection.sendall(message)


class CometClockPage(CometPage):
	def run(self):
		curtime = time.time()
		while True:
			yield self.send_chunk("<html><body><h1>Clock Test</h1><p>%s</p></body></html>" % datetime.datetime.now(), "text/html")
			curtime += 1
			yield reactor.schedule(self, callbacktime = curtime)


class CometClock(object):

	def __call__(self, environ, start_response):
		path_info = environ.get('PATH_INFO', '')

		if path_info == '/':
			return CometClockPage(CometPage.METHOD_MXMR, environ, start_response)
		else:
			start_response('404 Not Found', [('Content-Type', 'text/html')])
			return [ "404 Not Found" ]
