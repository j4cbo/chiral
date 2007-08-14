"""
Chiral HTTP server supporting WSGI
"""

# Chiral, copyright (c) 2007 Jacob Potter
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 2.

from chiral.net import tcp
from chiral.core import coroutine
from cStringIO import StringIO

from paste.util.quoting import html_quote

import socket
import sys
import traceback
from datetime import datetime

_CHIRAL_RELOADABLE = True

class HTTPResponse(object):
	"""Response to an HTTP request."""

	def __init__(self, conn, status = "500 Internal Server Error", headers = None, content=None):
		self.conn = conn
		self.headers = headers or {}
		self.content = content
		self.status = status

		self.should_keep_alive = False

	def default_error(self, status, extra_content=''):
		"""Sets the response to show a default error message."""

		self.status = status
		self.headers["Content-Type"] = "text/html"

	def render_headers(self, no_content = False):
		"""Return the response line and headers as a string."""

		# If we don't have a Content-Length, don't do a keep-alive
		if not no_content and "content-length" not in (key.lower() for key in self.headers.iterkeys()):
			self.should_keep_alive = False

		self.headers.update({
			"Date": datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S GMT"),
			"Connection": "keep-alive" if self.should_keep_alive else "close",
			"Server": "chiral.web.httpd/0.0.1"
		})

		return "HTTP/1.1 %s\r\n%s\r\n\r\n" % (
			self.status,
			"\r\n".join(("%s: %s" % (k, v)) for k, v in self.headers.iteritems())
		)

class WSGIFileWrapper(object):
	def __init__(self, filelike, blocksize = 4096):
		self.filelike = filelike
		self.blocksize = blocksize

	def close(self):
		if hasattr(self.filelike, "close"):
			self.filelike.close()

class HTTPConnection(tcp.TCPConnection):
	"""An HTTP connection."""

	MAX_REQUEST_LENGTH = 8192

	def send_error(self, status, resp, extra_content = ""):
		"""Create and send an HTTPResponse for the given status code."""

		if resp:
			resp.status = status
			resp.headers = { "Content-Type": "text/html" }
		else:
			resp = HTTPResponse(
				conn = self,
				status = status,
				headers = { "Content-Type": "text/html" }
			)

		content = "<html><head><title>%s</title></head><body><h1>%s</h1></body>%s</html>" % (
			status, status, extra_content
		)

		resp.headers["Content-Length"] = len(content)

		return self.sendall(resp.render_headers() + content)

	def connection_handler(self):
		"""The main request processing loop."""

		while True:
			# Read the first line
			try:
				line = yield self.read_line()
				# Ignore blank lines, as suggested by the RFC
				if not line:
					continue
				method, url, protocol = line.split(' ')
			except (tcp.ConnectionOverflowException, ValueError):
				yield self.send_error("400 Bad Request")
				self.close()
				return
			except (tcp.ConnectionClosedException, socket.error):
				return

			waiting_coro = []

			# Get ready for a WSGI response
			response = HTTPResponse(self)

			# Prepare WSGI environment
			environ = {
				'chiral.http.connection': self,
				'chiral.http.response': response,
				'wsgi.version': (1, 0),
				'wsgi.url_scheme': 'http',
				'wsgi.input': '',
				'wsgi.errors': sys.stderr,
				'wsgi.file_wrapper': WSGIFileWrapper,
				'wsgi.multithread': False,
				'wsgi.multiprocess': True,
				'wsgi.run_once': False,
				'REQUEST_METHOD': method,
				'SCRIPT_NAME': '',
				'SERVER_NAME': self.server.bind_addr[0],
				'SERVER_PORT': self.server.bind_addr[1],
				'SERVER_PROTOCOL': protocol
			}

			# Split query string out of URL, if present
			url = url.split('?', 1)
			if len(url) > 1:
				url, environ["QUERY_STRING"] = url
			else:
				url, = url

			# Read the rest of the request header, updating the WSGI environ dict
			# with each key/value pair
			last_key = None
			while True:
				try:
					line = yield self.read_line()
					if not line:
						break
				except tcp.ConnectionOverflowException:
					yield self.send_error("400 Bad Request")
					self.close()
					return
				except (tcp.ConnectionClosedException, socket.error):
					return

				# Allow for headers split over multiple lines.
				if last_key and line[0] in (" ", "\t"):
					environ[last_key] += "," + line
					continue

				if ":" not in line:
					yield self.send_error("400 Bad Request")
					self.close()
					return

				name, value = line.split(":", 1)

				# Convert the field name to WSGI's format
				key = "HTTP_" + name.upper().replace("-", "_")

				if key in environ:
					# RFC2616 4.2: Multiple copies of a header should be
					# treated # as though they were separated by commas.
					environ[key] += "," + value.strip()
				else:
					environ[key] = value.strip()

			# Accept absolute URLs, as required by HTTP 1.1
			if url.startswith("http://"):
				url = url[7:]
				if "/" in url:
					environ["HTTP_HOST"], url = url.split("/", 1)
				else:
					environ["HTTP_HOST"], url = url, ""
	
			environ["PATH_INFO"] = url

			# HTTP/1.1 requires that requests without a Host: header result in 400
			#if protocol == "HTTP/1.1" and "HTTP_HOST" not in environ:
			#	yield self.send_error("400 Bad Request")
			#	continue

			# WSGI requires these two headers
			environ["CONTENT_TYPE"] = environ.get("HTTP_CONTENT_TYPE", "")
			environ["CONTENT_LENGTH"] = environ.get("HTTP_CONTENT_LENGTH", "")

			# If a 100 Continue is expected, send it now.
			if protocol == "HTTP/1.1" and "100-continue" in environ.get("HTTP_EXPECT", ""):
				yield self.sendall("HTTP/1.1 100 Continue\r\n\r\n")

			# If this is a POST request with Content-Length, read its data
			if method == "POST" and environ["CONTENT_LENGTH"]:
				postdata = yield self.read_exactly(int(environ["CONTENT_LENGTH"]))
				environ["wsgi.input"] = StringIO(postdata)

			def set_coro(coro):
				"""
				Tell the HTTP response to not close until coro has completed.
				"""
				waiting_coro[:] = [ coro ]
			environ['chiral.http.set_coro'] = set_coro


			# Determine if we may do a keep-alive.
			connection_header = environ.get("HTTP_CONNECTION", "").lower()
			response.should_keep_alive = (
				(protocol == "HTTP/1.1" and "close" not in connection_header) or
				(protocol == "HTTP/1.0" and connection_header == "keep-alive")
			)

			write_data_buffer = StringIO()

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

				return write_data_buffer.write

			# Invoke the application.
			try:
				result = self.server.application(environ, start_response)
			except Exception:
				exc_formatted = "<pre>%s</pre>" % html_quote(traceback.format_exc())
				yield self.send_error("500 Internal Server Error", response, exc_formatted)

				# Close if necessary
				if not response.should_keep_alive:
					self.close()
					break

				continue

			# If the iterable has length 1, then we can determine the length
			# of the whole result now.
			try:
				if len(result) == 1 and not waiting_coro:
					response.headers["Content-Length"] = len(result[0])
			except TypeError:
				pass

			# Handle file wrapper
			if isinstance(result, WSGIFileWrapper):

				# Use TCP_CORK if available
				if hasattr(socket, "TCP_CORK"):
					self.remote_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_CORK, 1)

				yield self.sendall(response.render_headers())

				res = yield self.sendfile(result.filelike, 0, result.blocksize)

				if hasattr(socket, "TCP_CORK"):
					self.remote_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_CORK, 0)

				offset = res
				while res:
					res = yield self.sendfile(result.filelike, offset, result.blocksize)
					offset += res

				# Close the file
				result.close()

				# Close if necessary
				if not response.should_keep_alive:
					self.close()
					break

				# And we're now done with this request.
				continue

			# Did they use write()?
			write_data = write_data_buffer.getvalue()
			if write_data:
				headers_sent = True
				yield self.sendall(response.render_headers() + write_data)
			
			# Iterate through the result chunks provided by the application.
			res_iter = iter(result)
			headers_sent = False
			while True:
				try:
					data = res_iter.next()
				except StopIteration:
					break
				except Exception:
					yield self.send_error(
						"500 Internal Server Error",
						response,
						"<pre>%s</pre>" % (
							html_quote(traceback.format_exc()),
						)
					)
					self.close()
					return

				# Ignore empty chunks
				if not data:
					continue

				# Sending the headers is delayed until the first actual
				# data chunk comes back.
				if not headers_sent:
					headers_sent = True
					yield self.sendall(response.render_headers() + data)
				else:
					yield self.sendall(data)

			# If no data at all was returned, the headers won't have been sent yet.
			if not headers_sent and not waiting_coro:
				headers_sent = True
				yield self.sendall(response.render_headers(no_content=True))

			# If set_coro has been called, waiting_coro is a Coroutine that will
			# complete once the response is done.
			if waiting_coro:
				try:
					delayed_data = yield waiting_coro[0]
					del waiting_coro[:]

					# See if we can set Content-Length 
					try:
						if type(delayed_data) in (list, tuple) and len(delayed_data) == 1:
							response.headers["Content-Length"] = len(delayed_data[0])
					except TypeError:
						pass

					# Iterate through and send any delayed data as well
					if delayed_data:
						for data in delayed_data:
							# Ignore empty chunks
							if not data:
								continue

							# Add the headers, if not already sent
							if not headers_sent:
								headers_sent = True
								data = response.render_headers() + data

							yield self.sendall(data)
				except Exception:
					exc_formatted = "<pre>%s</pre>" % html_quote(traceback.format_exc())
					yield self.send_error("500 Internal Server Error", response, exc_formatted)

			# If the waiting coro didn't return any data at all, send the headers already.
			if not headers_sent:
				headers_sent = True
				yield self.sendall(response.render_headers(no_content=True))
				
			# Call any close handler on the WSGI app's result
			if hasattr(result, 'close'):
				result.close()

			# Close if necessary
			if not response.should_keep_alive:
				self.close()
				break

class HTTPServer(tcp.TCPServer):
	"""An HTTP server, based on chiral.net.tcp.TCPServer."""
	connection_class = HTTPConnection
	def __init__(self, bind_addr = ('', 80), application=None):
		self.application = application
		tcp.TCPServer.__init__(self, bind_addr)
