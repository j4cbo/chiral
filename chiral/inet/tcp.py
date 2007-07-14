"""TCP connection handling classes."""

from chiral.core import tasklet
import sys
import socket
import errno

if sys.version_info[:2] < (2, 5):
	raise RuntimeError("chiral.inet.tcp requires Python 2.5 for generator expressions.")

class ConnectionOverflowException(Exception):
	"""Indicates that an excessive amount of data was received without line terminators."""
	pass

class ConnectionClosedException(Exception):
	"""Indicates that the connection was closed."""
	pass

class TCPConnection(object):
	"""
	Provides basic interface for TCP connections.
	"""

	def handle_client_close(self):
		"""
		Connection.handle_client_close() will be called when the connection
		has been closed on the client end. Perform any necessary cleanup here.
		"""

	def handle_server_close(self):
		"""
		Connection.handle_server_close() will be called when the connection
		is about to be closed by the server. Perform any necessary cleanup here.
		"""
		
	def close(self):
		"""
		Call self.close() on a connection to perform a clean shutdown. The
		handle_server_close() method will be called before closing the actual
		socket.
		"""

		self.handle_server_close()
		self.client_sock.close()
		self.server.connections.remove(self)

	def client_closed(self):
		"""
		Call self.client_closed() on a connection whenever it is detected
		that the client has closed it (i.e. recv() returns zero bytes). The
		built-in read_line and read_exactly functions will call this when
		necessary. client_closed() calls handle_client_close() before removing
		the connection.
		"""
		self.handle_client_close()
		self.client_sock.close()
		self.server.connections.remove(self)


	def read_line(self, max_len = 1024, delimiter = "\n"):
		"""
		Read a line (delimited by any member of the "delimiters" tuple) from
		the client. If more than max_length characters are read before a
		delimiter is found, a ConnectionOverflowException will be raised.
		"""

		def _read_line_tasklet():
			"""Helper tasklet created by read_line if data is not immediately available."""
			while True:
				# Read more data
				new_data = yield self.recv(max_len)
				if not new_data:
					raise ConnectionClosedException()

				self._buffer += new_data
				# Check if the delimiter is already in the buffer.
				if delimiter in self._buffer[:max_len]:
					out, self._buffer = self._buffer.split(delimiter, 1)
					raise StopIteration(out)

				# If not, and the buffer's longer than our expected line,
				# we've had an overflow
				if len(self._buffer) > max_len:
					raise ConnectionOverflowException()

		# Check if the delimiter is already in the buffer.
		if delimiter in self._buffer[:max_len]:
			out, self._buffer = self._buffer.split(delimiter, 1)
			return tasklet.WaitForNothing(out)

		# Otherwise, we need to spawn a new tasklet
		return tasklet.WaitForTasklet(tasklet.Tasklet(_read_line_tasklet()))


	def read_exactly(self, length, read_increment = 4096):
		"""
		Read and return exactly length bytes.

		If length is less than or equal to read_increment, then only length octets
		will be read from the socket; otherwise, data will be read read_increment
		octets at a time.
		"""

		while True:
			# If we have enough bytes, return them
			if len(self._buffer) >= length:
				out = self._buffer[:length]
				self._buffer = self._buffer[length:]
				yield out
				return

			# Otherwise, read more
			bytes_left = length - len(self._buffer)
			new_data = yield self.recv(min(bytes_left, read_increment))
			if not new_data:
				raise ConnectionClosedException()

			self._buffer += new_data

	def _async_socket_operation(self, socket_op, cb_func, parameter):
		"""Helper function for asynchronous operations."""

		try:
			res = socket_op(parameter)
		except socket.error, exc:
			if exc[0] == errno.EAGAIN:
				callback = tasklet.WaitForCallback()

				def blocked_operation_handler():
					"""Callback for asynchronous operations."""
					# Prevent pylint from complaining about "except Exception"
					# pylint: disable-msg=W0703
					try:
						res = socket_op(parameter)
					except Exception, exc:
						callback.throw(exc)
					else:
						callback(res)

				cb_func(self.client_sock, blocked_operation_handler)
				return callback
			else:
				callback.throw(exc)

		return tasklet.WaitForNothing(res)

	def recv(self, buflen):
		"""
		Read data from the socket. Returns a Callback, which will
		fire as soon as data is available.
		"""
		return self._async_socket_operation(
			self.client_sock.recv,
			self.server.looper.wait_for_readable,
			buflen
		)

	def send(self, data):
		"""
		Send data. Returns a Callback, which fires once the data has been sent.
		"""

		return self._async_socket_operation(
			self.client_sock.send,
			self.server.looper.wait_for_writeable,
			data
		)

	def __init__(self, server, sock, addr):
		self.server = server
		self.server.connections.append(self)

		self.client_sock = sock
		self.client_addr = addr
		self._buffer = ""

class TCPServer(tasklet.Tasklet):
	"""
	This is a general-purpose TCP server. It manages one master
	socket which listens on a TCP port and accepts connections;
	each connection is tracked and closed when necessary.

	The connection_class attribute sets the class that will be created for
	new connections; it should be derived from TCPConnection.
	"""

	connection_class = TCPConnection

	connections = []
	acceptors = []

	def __init__(self, looper, bind_addr = ('', 80)):
		self.looper = looper
		self.bind_addr = bind_addr


		self.master_socket = socket.socket()
		self.master_socket.setblocking(0)
		self.master_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
		self.master_socket.bind(self.bind_addr)
		self.master_socket.listen(5)

		tasklet.Tasklet.__init__(self, self.acceptor())

	def acceptor(self):
		"""Main tasklet function.

		Continously calls accept() and creates new connection objects.
		"""

		# Continuously accept new connections
		while True:

			# Keep trying to accept() until we get a socket
			while True:
				try:
					client_socket, client_addr = self.master_socket.accept()
				except socket.error, exc:
					if exc[0] != errno.EAGAIN:
						print "Error in accept(): %s" % exc

					callback = tasklet.WaitForCallback()
					self.looper.wait_for_readable(self.master_socket, callback)
					yield callback
				else:
					break

			# Create a new TCPConnection for the socket 
			self.connection_class(self, client_socket, client_addr)
