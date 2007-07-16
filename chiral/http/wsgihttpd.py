"""
Chiral HTTP server supporting WSGI
"""

from chiral.inet import tcp
from cStringIO import StringIO

import socket
import sys

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

		self.content = "<html><head><title>%s</title></head><body><h1>%s</h1></body>%s</html>" % (
			status, status, extra_content
		)

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

	def send_error_and_close(self, status, extra_content = ""):
		"""Create and send an HTTPResponse for the given status code."""
		resp = HTTPResponse(conn = self)
		resp.default_error(status, extra_content)
		resp.write_out()
		self.close()

	def handler(self):
		"""The main request processing loop."""

		while True:
			# Read the first line
			try:
				method, url, protocol = (yield self.read_line(delimiter="\r\n")).split(' ')
			except (tcp.ConnectionOverflowException, ValueError):
				self.send_error_and_close("400 Bad Request")
				return
			except (tcp.ConnectionClosedException, socket.error):
				return

			waiting_tasklet = []

			def set_tasklet(tlet):
				"""
				Tell the HTTP response to not close until Tasklet has completed.
				Returns the HTTPConnection of the current request.
				"""
				waiting_tasklet[:] = [ tlet ]
				return self

			# Prepare WSGI environment
			environ = {
				'chiral.http.set_tasklet': set_tasklet,
				'wsgi.version': (1, 0),
				'wsgi.url_scheme': 'http',
				'wsgi.input': '',
				'wsgi.errors': sys.stderr,
				'wsgi.multithread': False,
				'wsgi.multiprocess': True,
				'wsgi.run_once': False,
				'REQUEST_METHOD': method,
				'PATH_INFO': url,
				'SERVER_NAME': self.server.bind_addr[0],
				'SERVER_PORT': self.server.bind_addr[1],
				'SERVER_PROTOCOL': protocol
			}

			# Read the rest of the request header, updating the WSGI environ dict
			# with each key/value pair
			while True:
				try:
					line = yield self.read_line(delimiter="\r\n")
					if not line:
						break
					name, value = line.split(":", 1)
				except (tcp.ConnectionOverflowException, ValueError):
					self.send_error_and_close("400 Bad Request")
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

			# Determine if we may do a keep-alive. This
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
			result = self.server.application(environ, start_response)

			# If it returned an iterable of length 1, then our Content-Length
			# is that of the first result. Otherwise, we'll close the connection
			# to indicate completion.
			try:
				if len(result) == 1:
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

			# If no data at all was returned, the headers won't have been sent yet.
			if not headers_sent:
				headers_sent = True
				if should_keep_alive:
					response.headers["Connection"] = "keep-alive"
				else:
					response.headers["Connection"] = "close"
				yield self.send(response.render_headers())

			# Call any close handler on the WSGI app's result
			if hasattr(result, 'close'):
				result.close()

			# If set_tasklet has been set, waiting_tasklet is a Tasklet that will
			# complete once the response is done.
			if waiting_tasklet:
				yield waiting_tasklet[0]

			# Close if necessary
			if not should_keep_alive:
				self.close()
				break

class HTTPServer(tcp.TCPServer):
	"""An HTTP server, based on chiral.inet.tcp.TCPServer."""
	connection_class = HTTPConnection
	def __init__(self, looper, bind_addr = ('', 80), application=None):
		self.application = application
		tcp.TCPServer.__init__(self, looper, bind_addr)