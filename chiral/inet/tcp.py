from chiral.inet import netcore
from chiral.core import tasklet
import traceback
import sys
import socket
import errno

if sys.version_info[:2] < (2, 5):
	raise RuntimeError("chiral.inet.tcp requires Python 2.5 for generator expressions.")

class ConnectionOverflowException(Exception):
	pass

class ConnectionClosedException(Exception):
	pass

class TCPConnection(object):

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
		self.sock.close()
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
		self.sock.close()
		self.server.connections.remove(self)


	def read_line(self, max_len = 1024, delimiter = "\n"):
		"""
		Read a line (delimited by any member of the "delimiters" tuple) from
		the client. If more than max_length characters are read before a
		delimiter is found, a ConnectionOverflowException will be raised.
		"""

		def _read_line_tasklet():
			while True:
				# Read more data
				new_data = yield self.sock.recv(max_len)
				if not new_data:
					raise ConnectionClosedException()

				self._buffer += new_data
				# Check if the delimiter is already in the buffer.
				if delimiter in self._buffer[:max_len]:
					out, self._buffer = self._buffer.split(delimiter, 1)
					raise StopIteration(out)
					return

				# If not, and the buffer's longer than our expected line,
				# we've had an overflow
				if len(self._buffer) > max_len:
					raise ConnectionOverflowException()

		if not hasattr(self, "_buffer"): self._buffer = ""

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

		if not hasattr(self, "_buffer"): self._buffer = ""

		while True:
			# If we have enough bytes, return them
			if len(self._buffer) >= length:
				out = self._buffer[:length]
				self._buffer = self._buffer[length:]
				yield out
				return

			# Otherwise, read more
			bytes_left = length - len(self._buffer)
			new_data = yield self.sock.recv(min(bytes_left, read_increment))
			if not new_data:
				raise ConnectionClosedException()

			self._buffer += new_data

class TCPServer(object):
	"""
	This is a general-purpose TCP server. It manages one master
	AsyncSocket which listens on a TCP port and accepts connections;
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


		self.master_socket = AsyncSocket(self.looper)
		self.master_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
		self.master_socket.bind(self.bind_addr)
		self.master_socket.listen(5)

		tasklet.Tasklet(self.acceptor())

	def acceptor(self):

		# Continuously accept new connections
		while True:
			nc_socket, nc_addr = yield self.master_socket.accept()
#			print "Accepting connection: %s from %s" % (nc_socket, nc_addr)
			nc = self.connection_class(self, nc_socket, nc_addr)


class AsyncSocket(object):
	"""
	Asynchronous version of Socket. All potentially blocking operations
	(recv, recvfrom, send, sendto, accept) return Callback objects that
	fire once the operation is complete.
	"""

	def __init__(self, looper, _sock=None, **kwargs):
		self.looper = looper
		if _sock:
			self.socket = _sock
		else:
			self.socket = socket.socket(**kwargs)

		self.socket.setblocking(0)

	def _async_socket_operation(self, op, cb_func, *args, **kwargs):
		try:
			res = op(*args, **kwargs)
		except socket.error, e:
			if e[0] == errno.EAGAIN:
				cb = tasklet.WaitForCallback()

				def blocked_operation_handler():
					try:
						res = op(*args, **kwargs)
					except Exception, e:
						cb.throw(e)
					else:
						cb(res)

				cb_func(self.socket, blocked_operation_handler)
				return cb
			else:
				cb.throw(e)

		return tasklet.WaitForNothing(res)

	def recv(self, buflen):
		"""
		Read data from the socket. Returns a Callback, which will
		fire as soon as data is available.
		"""
		return self._async_socket_operation(
			self.socket.recv,
			self.looper.wait_for_readable,
			buflen
		)


	def recvfrom(self, buflen):
		"""
		Read data and source address from the socket. Returns a Callback,
		which will fire as soon as data is available.
		"""

		return self._async_socket_operation(
			self.socket.recvfrom,
			self.looper.wait_for_readable,
			buflen
		)


	def send(self, data):
		"""
		Send data. Returns a Callback, which fires once the data has been sent.
		"""

		return self._async_socket_operation(
			self.socket.send,
			self.looper.wait_for_writeable,
			data
		)

	def accept(self):
		"""
		Return a callback which fires when a new connection has been accepted.
		"""

		# This call goes directly to the internal _sock object,
		# allowing us to ensure that the returned value is an
		# AsyncSocket rather than the original socket.socket class.
		try:
			res, addr = self.socket.accept()
			res = (AsyncSocket(looper = self.looper, _sock = res), addr)
		except socket.error, e:
			if e[0] == errno.EAGAIN:
				cb = tasklet.WaitForCallback()

				def blocked_accept_handler():
					try:
						res, addr = self.socket.accept()
					except Exception, e:
						cb.throw(e)
					else:
						res = (AsyncSocket(looper = self.looper, _sock = res), addr)
						cb(res)

				self.looper.wait_for_readable(self.socket, blocked_accept_handler)
				return cb
			else:
				cb.throw(e)

		return tasklet.WaitForNothing(res)


	def bind(self, *args, **kwargs): return self.socket.bind(*args, **kwargs)
	def listen(self, *args, **kwargs): return self.socket.listen(*args, **kwargs)
	def setsockopt(self, *args, **kwargs): return self.socket.setsockopt(*args, **kwargs)
	def getsockopt(self, *args, **kwargs): return self.socket.getsockopt(*args, **kwargs)
	def close(self):
		cr = self.socket.close()
		return cr

	def __del__(self):
		self.socket.close()
