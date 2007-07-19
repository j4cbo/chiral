"""
Chiral HTTP server supporting WSGI
"""

from chiral.inet import tcp
from cStringIO import StringIO

from paste.util.quoting import html_quote

import socket
import sys
import traceback

_CHIRAL_RELOADABLE = True

class HTTPResponse(object):
	"""Response to an HTTP request."""

	def __init__(self, conn, status = "500 Internal Server Error", headers = None, content=None):
		self.conn = conn
		self.headers = headers or {}
		self.content = content
		self.status = status

	def default_error(self, status, extra_content=''):
		"""Sets the response to show a default error message."""

		self.status = status
		self.headers["Content-Type"] = "text/html"

	def render_headers(self):
		"""Return the response line and headers as a string."""
		return "HTTP/1.1 %s\r\n%s\r\n\r\n" % (
			self.status,
			"\r\n".join(("%s: %s" % (k, v)) for k, v in self.headers.iteritems())
		)

	def write_out(self):
		"""Write the complete response to its associated HTTPConnection."""

		output = self.render_headers()
		return self.conn.send(output)

	def send(self, data):
		"""Send data to the response's connection. Returns a WaitCondition."""
		return self.conn.send(data)


class HTTPConnection(tcp.TCPConnection):
	"""An HTTP connection."""

	MAX_REQUEST_LENGTH = 8192

	def send_error(self, status, extra_content = ""):
		"""Create and send an HTTPResponse for the given status code."""
		resp = HTTPResponse(conn = self, status = status, headers = { "Content-Type": "text/html" })

		content = "<html><head><title>%s</title></head><body><h1>%s</h1></body>%s</html>" % (
			status, status, extra_content
		)

		resp.headers["Content-Length"] = len(content)

		return self.send(resp.render_headers() + content)

	def handler(self):
		"""The main request processing loop."""

		while True:
			# Read the first line
			try:
				method, url, protocol = (yield self.read_line(delimiter="\r\n")).split(' ')
			except (tcp.ConnectionOverflowException, ValueError):
				yield self.send_error("400 Bad Request")
				self.close()
				return
			except (tcp.ConnectionClosedException, socket.error):
				return

			waiting_tasklet = []

			def set_tasklet(tlet):
				"""
				Tell the HTTP response to not close until Tasklet has completed.
				"""
				waiting_tasklet[:] = [ tlet ]

			# Prepare WSGI environment
			environ = {
				'chiral.http.set_tasklet': set_tasklet,
				'chiral.http.connection': self,
				'wsgi.version': (1, 0),
				'wsgi.url_scheme': 'http',
				'wsgi.input': '',
				'wsgi.errors': sys.stderr,
				'wsgi.multithread': False,
				'wsgi.multiprocess': True,
				'wsgi.run_once': False,
				'REQUEST_METHOD': method,
				'PATH_INFO': url,
				'SCRIPT_NAME': '',
				'SERVER_NAME': self.server.bind_addr[0],
				'SERVER_PORT': self.server.bind_addr[1],
				'SERVER_PROTOCOL': protocol
			}

			# Split query string out of URL, if present
			url = url.split('?', 1)
			if len(url) > 1:
				environ["PATH_INFO"], environ["QUERY_STRING"] = url
			else:
				environ["PATH_INFO"], = url

			# Read the rest of the request header, updating the WSGI environ dict
			# with each key/value pair
			while True:
				try:
					line = yield self.read_line(delimiter="\r\n")
					if not line:
						break
					name, value = line.split(":", 1)
				except (tcp.ConnectionOverflowException, ValueError):
					self.send_error("400 Bad Request")
					self.close()
					return
				except (tcp.ConnectionClosedException, socket.error):
					return

				key = "HTTP_%s" % (name.upper().replace("-", "_"), )
				environ[key] = value.strip()

			# WSGI requires these two headers
			environ["CONTENT_TYPE"] = environ.get("HTTP_CONTENT_TYPE", "")
			environ["CONTENT_LENGTH"] = environ.get("HTTP_CONTENT_LENGTH", "")

			# If this is a POST request with Content-Length, read its data
			if method == "POST" and environ["CONTENT_LENGTH"]:
				postdata = yield self.read_exactly(int(environ["CONTENT_LENGTH"]))
				environ["wsgi.input"] = StringIO(postdata)

			# Determine if we may do a keep-alive.
			connection_header = environ.get("HTTP_CONNECTION", "").lower()
			should_keep_alive = (
				(protocol == "HTTP/1.1" and "close" not in connection_header) or
				(protocol == "HTTP/1.0" and connection_header == "keep-alive")
			)

			# Get ready for a WSGI response
			response = HTTPResponse(self)

			def write(data):
				"""Bogus write method for WSGI applications."""
				# AWW HELL NAW
				raise NotImplementedError

			def start_response(status, response_headers, exc_info=None):
				"""start_response() callback for WSGI applications"""
				# start_response may not be called after headers have already
				# been set.
				if exc_info and response.headers:
					raise exc_info[0], exc_info[1], exc_info[2]

				response.status = status
				response.headers.update(response_headers)

				if exc_info:
					response.status = "500 Internal Server Error"
					exc_info = None

				return write

			# Invoke the application.
			try:
				result = self.server.application(environ, start_response)

				# If it returned an iterable of length 1, then our Content-Length
				# is that of the first result. Otherwise, we'll close the connection
				# to indicate completion.
				try:
					if len(result) == 1 and not waiting_tasklet:
						response.headers["Content-Length"] = len(result[0])
					else:
						should_keep_alive = False
				except TypeError:
					should_keep_alive = False

				headers_sent = False

				# Iterate through the result chunks provided by the application.
				for data in result:
					# Ignore empty chunks
					if not data:
						continue

					# Sending the headers is delayed until the first actual
					# data chunk comes back.
					if not headers_sent:
						headers_sent = True
						if should_keep_alive:
							response.headers["Connection"] = "keep-alive"
						else:
							response.headers["Connection"] = "close"

						yield self.send(response.render_headers() + data)

					else:
						yield self.send(data)

			except Exception:
				exc_formatted = "<pre>%s</pre>" % html_quote(traceback.format_exc())
				yield self.send_error("500 Internal Server Error", exc_formatted)

				# Close if necessary
				if not should_keep_alive:
					self.close()
					break
				continue

			# If no data at all was returned, the headers won't have been sent yet.
			if not headers_sent:
				headers_sent = True
				if should_keep_alive:
					response.headers["Connection"] = "keep-alive"
				else:
					response.headers["Connection"] = "close"
				yield self.send(response.render_headers())

			# If set_tasklet has been set, waiting_tasklet is a Tasklet that will
			# complete once the response is done.
			if waiting_tasklet:
				tlet, = waiting_tasklet
				tlet.start(force=False)
				yield tlet

			# Call any close handler on the WSGI app's result
			if hasattr(result, 'close'):
				result.close()

			# Close if necessary
			if not should_keep_alive:
				self.close()
				break

class HTTPServer(tcp.TCPServer):
	"""An HTTP server, based on chiral.inet.tcp.TCPServer."""
	connection_class = HTTPConnection
	def __init__(self, bind_addr = ('', 80), application=None):
		self.application = application
		tcp.TCPServer.__init__(self, bind_addr)
