"""Comet support"""

from chiral.inet import reactor
from chiral.core import tasklet
import datetime
import time

_CHIRAL_RELOADABLE = True

class CometPage(tasklet.Tasklet):
	"""
	Helper for handling Comet pages.

	To use CometPage, create a new instance from your WSGI application and pass
	the WSGI-provided environ and start_response, then return the CometPage instance.
	Example::

		return CometClockPage(CometPage.METHOD_MXMR, environ, start_response)

	Alternately, a CometPage-based class (the class itself, not an instance) may be used
	directly as a WSGI application. You may wish to override __init__ and modify defaults
	there, like so::

		class JavascriptCometPage(CometPage):
			def __init__(self, environ, start_resp):
				CometPage.__init__(self, environ, start_resp, method=self.METHOD_JS)
	@cvar METHOD_CHUNKED: Send data in chunks delimited by self.delimiter
	@cvar METHOD_JS: Send data (which should be serialized JSON) as calls to self.jsmethod
	@cvar METHOD_MXMR: Send data as a multipart/x-mixed-replace document

	@cvar default_method: The method to use if none is specified in the constructor.

	"""

	METHOD_CHUNKED, METHOD_JS, METHOD_MXMR = xrange(3)

	def __init__(self, environ, start_response, method=METHOD_MXMR, delimiter="\r\n\r\n---\r\n", jsmethod="chiral._comet_handler", content_type="text/plain"):
		"""
		Constructor.

		@param environ: WSGI "environ" parameter
		@type environ: C{dict}
		@param start_response: WSGI "start_response" parameter
		@type start_response: C{callable}
		"""

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

	def send_chunk(self, data):
		"""
		Send a chunk of data to the client.

		The exact definition of "chunk" depends on the method chosen when the object
		was created. In the case of METHOD_MXMR and METHOD_CHUNKED, the chunk may be
		any data provided it does not include the boundary string. For METHOD_JS, the
		given data will be included literally inside the parentheses of a Javascript
		function call to self.jsmethod; it must therefore be a valid Javascript or
		JSON expression or list of expressions.
		"""

		if self.method == self.METHOD_CHUNKED:
			return self.http_connection.sendall("%s%s" % (data, self.delimiter))
		elif self.method == self.METHOD_JS:
			return self.http_connection.sendall("%s(%s)" % (self.jsmethod, data))
		elif self.method == self.METHOD_MXMR:
			data = str(data)
			message = "Content-type: %s\r\n\r\n%s\r\n--ChiralMXMRBoundary\r\n" % (
				self.content_type,
				data
			)
			return self.http_connection.sendall(message)

	def run(self):
		"""
		Run the application.

		Override this in your CometPage class with a Tasklet generator that 
		performs whatever tasks the page will do. It may call and yield self.send_chunk
		to return data to the client.
		"""
		raise NotImplementedError

class CometClockPage(CometPage):
	"""
	CometPage displaying a simple HTML clock that updates every second.
	"""

	def run(self):
		curtime = time.time()
		while True:
			chunk = "<html><body><h1>Clock Test</h1><p>%s</p></body></html>" % (
				datetime.datetime.now(),
			)
			yield self.send_chunk(chunk)
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
