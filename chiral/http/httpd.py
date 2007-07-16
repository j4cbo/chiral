"""
Chiral HTTP server
"""

from chiral.inet import tcp

import socket

class HTTPResponse(object):
	"""Response to an HTTP request."""

	codes = {
		100: "Continue",
		101: "Switching Protocols",
		200: "OK",
		201: "Created",
		202: "Accepted",
		203: "Non-Authoritative Information",
		204: "No Content",
		205: "Reset Content",
		206: "Partial Content",
		300: "Multiple Choices",
		301: "Moved Permanently",
		302: "Found",
		303: "See Other",
		304: "Not Modified",
		305: "Use Proxy",
		307: "Temporary Redirect",
		400: "Bad Request",
		401: "Unauthorized",
		403: "Forbidden",
		404: "Not Found",
		405: "Method Not Allowed",
		406: "Not Acceptable",
		407: "Proxy Authentication Required",
		408: "Request Conflict",
		409: "Conflict",
		410: "Gone",
		411: "Length Required",
		412: "Precondition Failed",
		413: "Request Entity Too Large",
		414: "Request-URI Too Long",
		415: "Unsupported Media Type",
		416: "Requested Range Not Satisfiable",
		417: "Expectation Failed",
		500: "Internal Server Error",
		501: "Not Implemented",
		502: "Bad Gateway",
		503: "Service Unavailable",
		504: "Gateway Timeout",
		505: "HTTP Version Not Supported"
	}

	def __init__(self, conn, code=500, headers = None, content=None):
		if headers is None:
			headers = {}

		self.conn = conn
		self.headers = headers
		self.content = content
		self.code = code

	def default_error(self, code, extra_content=''):
		"""Sets the response to show a default error message."""

		self.code = code
		self.headers["Content-Type"] = "text/html"

		code_string = "%s %s" % (code, HTTPResponse.codes[code])
		self.content = "<html><head><title>%s</title></head><body><h1>%s</h1></body>%s</html>" % (
			code_string,
			code_string,
			extra_content
		)

	def render_headers(self):
		"""Return the response line and headers as a string."""

		return "HTTP/1.1 %s %s\r\n%s\r\n\r\n" % (
			self.code,
			self.codes[self.code],
			"\r\n".join(("%s: %s" % (k, v)) for k, v in self.headers.iteritems())
		)

	def write_out(self):
		"""Write the complete response to its associated HTTPConnection."""

		self.headers["Content-Length"] = len(self.content)
		output = self.render_headers() + self.content
		#print "--HTTP OUTPUT--\n%s\n----" % (output, )
		#print "writing on fd %s" % (sock.socket.fileno(),)
		return self.conn.send(output)


class HTTPRequest(object):
	"""An incoming HTTP request."""

	def __init__(self, lines):
		self.request_line = lines[0].split(" ")

		if len(self.request_line) != 3:
			raise ValueError("Bad request line")

		self.method, self.url, self.http_version = self.request_line

		self.__headers = dict(
			(key.lower(), value.strip())
			for key, value
			in (line.split(":", 1) for line in lines[1:] if line)
		)

	def __get_headers(self):
		"""Returns a dict of the request headers. All field names are lower-case."""
		return self.__headers

	headers = property(__get_headers)

	def get_header(self, key, default=""):
		"""Return the value of a given field (case-insensitive) in the request headers."""
		return self.__headers.get(key.lower(), default)

	def get_headers(self):
		"""Returns a copy of the complete request headers."""
		return dict(self.__headers)

	def should_keep_alive(self):
		"""Return True if the request headers specify that it should be kept alive, False otherwise."""
		if self.http_version == "HTTP/1.1" and "close" not in self.get_header("connection"):
			return True

		if self.http_version == "HTTP/1.0" and self.get_header("connection").lower() == "keep-alive":
			return True

		return False

class HTTPConnection(tcp.TCPConnection):
	"""An HTTP connection."""

	MAX_REQUEST_LENGTH = 8192

	def send_error(self, code, extra_content = ""):
		"""Create and send an HTTPResponse for the given status code."""
		resp = HTTPResponse(conn = self)
		resp.default_error(code, extra_content)
		return resp.write_out()

	def handler(self):
		"""The main request processing loop."""

		#print "request loop running"
		while True:
			# Read the request lines
			req_lines = []
			while True:
				try:
					line = yield self.read_line(delimiter="\r\n")
				except tcp.ConnectionOverflowException:
					self.send_error(400).add_completion_callback(self.close)
				except (tcp.ConnectionClosedException, socket.error):
					return

				if not line:
					break

				req_lines.append(line)

			# Set up request and response objects
			req = HTTPRequest(req_lines)
			response = HTTPResponse(self)

			# Request processing here
			if req.url == "/":
				response.code = 200
				response.content = "<html><body>Hello, World!</body></html>"
			else:
				response.default_error(404)

			if req.should_keep_alive():
				# It's a persistent connection; send the response and then wait for another request
				response.headers["Connection"] = "keep-alive"
				#print "writing out"
				yield response.write_out()
				#print "writing out done"
			else:
				# Just write and close.
				response.headers["Connection"] = "close"
				#print "writing out (no keep-alive)"
				yield response.write_out()
				#print "writing out done (no keep-alive)"
				self.close()
				return

class HTTPServer(tcp.TCPServer):
	"""An HTTP server, based on chiral.inet.tcp.TCPServer."""
	connection_class = HTTPConnection
