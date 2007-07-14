from chiral.core import tasklet
from chiral.inet import tcp

import socket
import traceback
import sys

class HTTPResponse(object):

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

	def __init__(self, code=500, content_type="text/plain", headers = {}, content=None):
		self.headers = headers
		self.content = content
		self.code = code

	def default_error(self, code):
		self.code = code
		code_string = "%s %s" % (code, HTTPResponse.codes[code])
		self.content = "<html><head><title>%s</title></head><body><h1>%s</h1></body></html>" % (
			code_string,
			code_string
		)

	def render_headers(self):
		return "HTTP/1.1 %s %s\r\n%s\r\n\r\n" % (
			self.code,
			self.codes[self.code],
			"\r\n".join(("%s: %s" % (k, v)) for k, v in self.headers.iteritems())
		)

	def write_out(self, sock):
		self.headers["Content-Length"] = len(self.content)
		output = self.render_headers() + self.content
		#print "--HTTP OUTPUT--\n%s\n----" % (output, )
		#print "writing on fd %s" % (sock.socket.fileno(),)
		yield sock.send(output)
		return


class HTTPRequest(object):

	def __init__(self, lines):
		self.request_line = lines[0].split(" ")

		if len(self.request_line) != 3:
			raise ValueError("Bad request line")

		self.method, self.url, self.http_version = self.request_line

		self.headers = dict(
			(key.lower(), value.strip())
			for key, value
			in (line.split(":", 1) for line in lines[1:] if line)
		)

	def should_keep_alive(self):
		if self.http_version == "HTTP/1.1" and "close" not in self.headers.get("connection", ""):
			return True

		if self.http_version == "HTTP/1.0" and self.headers.get("connection", "").lower() == "keep-alive":
			return True

		return False

class HTTPConnection(tcp.TCPConnection):

	MAX_REQUEST_LENGTH = 8192

	def send_error(self, code, extra_content = ""):
		resp = HTTPResponse(
			code = code,
			content = "<html><head><title>%s</title></head><body><h1>%s</h1>%s</body></html>" % (
				HTTPResponse.codes[code],
				HTTPResponse.codes[code],
				extra_content
			)
		)
		return resp.write_out(self.sock)

	def request_loop_done(self, tasklet, res, exc_info):
		# If an error is thrown from request_loop, print it out and close
		# the connection with a 500.
		if not exc_info: return
		#print "done with requests on fd %s" % (self.sock.socket.fileno(), )

		error_string = "<p>%s</p><pre>%s</pre>" % (
			exc_info[1],
			"".join(traceback.format_exception(*exc_info))
		)

		print "AAAA!"
		print "".join(traceback.format_exception(*exc_info))

		#self.send_error(500, error_string).add_completion_callback(self.sock.close)

	def request_loop(self):

		#print "request loop running"
		while True:
			# Read the request lines
			req_lines = []
			while True:
				try:
					line = yield self.read_line(delimiter="\r\n")
#					print "read %s on fd %s" % (line, self.sock.socket.fileno())
					#print "read \"%s\"" % (line, )
				except tcp.ConnectionOverflowException:
					self.send_error(400).add_completion_callback(self.close)
				except (tcp.ConnectionClosedException, socket.error):
					return

				if not line: break
				req_lines.append(line)

			# Set up request and response objects
			req = HTTPRequest(req_lines)
			response = HTTPResponse()

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
				yield response.write_out(self.sock)
				#print "writing out done"
			else:
				# Just write and close.
				response.headers["Connection"] = "close"
				#print "writing out (no keep-alive)"
				yield response.write_out(self.sock)
				#print "writing out done (no keep-alive)"
				self.close()
				return

	def __init__(self, server, sock, client_addr):
		tcp.TCPConnection.__init__(self, server, sock, client_addr)

		#print "new HTTP connection!"
		tasklet.Tasklet(self.request_loop())#.add_completion_callback(self.request_loop_done)

class HTTPServer(tcp.TCPServer):
	connection_class = HTTPConnection
