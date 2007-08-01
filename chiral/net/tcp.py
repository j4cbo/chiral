"""TCP connection handling classes."""

from chiral.core import tasklet
from chiral.net import reactor
import sys
import socket
import errno
import weakref

if sys.version_info[:2] < (2, 5):
	raise RuntimeError("chiral.net.tcp requires Python 2.5 for generator expressions.")

try:
	from sendfile import sendfile
	_SENDFILE_AVAILABLE = True
except ImportError:
	_SENDFILE_AVAILABLE = False

class ConnectionOverflowException(Exception):
	"""Indicates that an excessive amount of data was received without line terminators."""
	pass

class ConnectionClosedException(Exception):
	"""Indicates that the connection was closed."""
	pass

class TCPConnection(tasklet.Tasklet):
	"""
	Provides basic interface for TCP connections.
	"""

	def connection_handler(self):
		"""
		Main event processing loop.

		The connection_handler() method will be run as a Tasklet when the TCPConnection
		is initialized. It should be overridden in the derived class.
		"""
		raise NotImplementedError

	def close(self, *args):
		"""
		Call self.close() on a connection to perform a clean shutdown.
		"""

		self.remote_sock.close()

	def _read_line_tasklet(self, max_len, delimiter):
		"""Helper tasklet created by read_line if data is not immediately available."""
		while True:
			# Read more data
			new_data = yield self.recv(max_len, try_now = False)
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


	@tasklet.returns_waitcondition
	def read_line(self, max_len = 1024, delimiter = "\r\n"):
		"""
		Read a line (delimited by any member of the "delimiters" tuple) from
		the client. If more than max_length characters are read before a
		delimiter is found, a ConnectionOverflowException will be raised.
		"""

		# Check if the delimiter is already in the buffer.
		if delimiter in self._buffer[:max_len]:
			out, self._buffer = self._buffer.split(delimiter, 1)
			return out

		# If not, attempt to recv()
		try:
			new_data = self.remote_sock.recv(max_len)
		except socket.error, exc:
			if exc[0] == errno.EAGAIN:
				# OK, we're going to need to spawn a new tasklet.
				return tasklet.WaitForTasklet(tasklet.Tasklet(self._read_line_tasklet(max_len, delimiter)))
			else:
				# Something else is broken; raise it again.
				raise exc

		# So recv() worked and we now have some more data. Add it to the buffer,
		# and check for the delimiter again.
		self._buffer += new_data
		if delimiter in self._buffer[:max_len]:
			out, self._buffer = self._buffer.split(delimiter, 1)
			return out

		# No luck finding the delimiter. Make sure we haven't overflowed...
		if len(self._buffer) > max_len:
			raise ConnectionOverflowException()

		# The line isn't available yet. Spawn a tasklet to deal with it.
		return tasklet.WaitForTasklet(tasklet.Tasklet(self._read_line_tasklet(max_len, delimiter)))



	@tasklet.returns_waitcondition
	@tasklet.task_waitcondition
	def read_exactly(self, length, read_increment = 32768):
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
				raise StopIteration(out)

			# Otherwise, read more
			bytes_left = length - len(self._buffer)
			new_data = yield self.recv(min(bytes_left, read_increment))
			if not new_data:
				raise ConnectionClosedException()

			self._buffer += new_data

	def _async_socket_operation(self, socket_op, cb_func, parameter, try_now):
		"""Helper function for asynchronous operations."""

		callback = tasklet.WaitForCallback(cb_func)

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

		if try_now:
			# Attempt socket_op now; only pass it to the callback if it
			# returns EAGAIN.
			try:
				res = socket_op(parameter)
			except socket.error, exc:
				if exc[0] == errno.EAGAIN:
					cb_func(self, self.remote_sock, blocked_operation_handler)
					return callback
				else:
					raise exc
		else:
			# Don't bother. (try_now is set False by functions like read_line,
			# which attempt the low-level operations themselves first to avoid
			# creating Tasklets unnecessarily.)
			cb_func(self, self.remote_sock, blocked_operation_handler)
			return callback

		return res

	@tasklet.returns_waitcondition
	def recv(self, buflen, try_now=True):
		"""
		Read data from the socket. Set try_now to False if a low-level recv() has
		already been attempted.
		"""
		return self._async_socket_operation(
			self.remote_sock.recv,
			reactor.wait_for_readable,
			buflen,
			try_now
		)

	def _sendall_tasklet(self, data):
		"""Helper tasklet created by sendall if not all data could be sent."""
		while data:
			res = yield self.send(data)
			data = data[res:]

	@tasklet.returns_waitcondition
	def sendall(self, data):
		"""
		Send all of data to the socket. The send() method and underlying system
		call are not guaranteed to write all the supplied data; sendall() will
		loop if necessary until all data is written.
		"""

		# Try writing the data.
		try:
			res = self.remote_sock.send(data)
		except socket.error, exc:
			if exc[0] == errno.EPIPE:
				raise ConnectionClosedException()
			elif exc[0] != errno.EAGAIN:
				raise exc
		else:
			# Only return now if /all/ the data was written
			if res == len(data):
				return
			else:
				data = data[res:]

		# There's still more data to be sent, so hand things off to the tasklet.
		return tasklet.WaitForTasklet(tasklet.Tasklet(self._sendall_tasklet(data)))

	@tasklet.returns_waitcondition
	def send(self, data, try_now=True):
		"""
		Send data, and return the number of bytes actually sent. Note that the
		send() system call does not guarantee that all of data will actually be
		sent; in most cases, sendall() should be used.
		"""
		return self._async_socket_operation(
			self.remote_sock.send,
			reactor.wait_for_writeable,
			data,
			try_now
		)

	@tasklet.returns_waitcondition
	def sendfile(self, infile, offset, length):
		"""
		Send up to len bytes of data from infile, starting at offset.
		Returns the amount actually written, which may be less than
		all the data given. Use sendall() if all the data must be sent.
		"""

		if not _SENDFILE_AVAILABLE:
			# We don't have the sendfile() system call available, so just do the
			# read and write ourselves.
			# XXX: This should respect offset.
			data = infile.read(length)
			return self.sendall(data)

		# sendfile() is available. It takes a number of parameters, so we can't just use
		# the _async_socket_operation helper.
		try:
			res = sendfile(self.remote_sock.fileno(), infile.fileno(), offset, length)
		except OSError, exc:
			if exc.errno == errno.EAGAIN:
				callback = tasklet.WaitForCallback("sendfile")

				def blocked_operation_handler():
					"""Callback for asynchronous operations."""
					# Prevent pylint from complaining about "except Exception"
					# pylint: disable-msg=W0703
					try:
						res = sendfile(self.remote_sock.fileno(), infile.fileno(), offset, length)
					except Exception, exc:
						callback.throw(exc)
					else:
						callback(res[1])

				reactor.wait_for_writeable(self, self.remote_sock, blocked_operation_handler)
				return callback
			else:
				raise exc

		# sendfile() worked, so we're done.
		return res[1]

	def __init__(self, sock, addr, server=None):

		self.remote_sock = sock
		self.remote_addr = addr
		self.server = server

		# Set the socket nonblocking. Socket objects have some magic that
		# pylint doesn't grok, so suppress its "no setblocking member" warning.
		self.remote_sock.setblocking(0) # pylint: disable-msg=E1101

		self._buffer = ""

		tasklet.Tasklet.__init__(self, self.connection_handler(), autostart=False)

class TCPServer(tasklet.Tasklet):
	"""
	This is a general-purpose TCP server. It manages one master
	socket which listens on a TCP port and accepts connections;
	each connection is tracked and closed when necessary.

	The connection_class attribute sets the class that will be created for
	new connections; it should be derived from TCPConnection.
	"""

	connection_class = TCPConnection

	def __init__(self, bind_addr = ('', 80)):
		self.bind_addr = bind_addr
		self.connections = weakref.WeakValueDictionary()

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

					callback = tasklet.WaitForCallback("master readable")
					reactor.wait_for_readable(self, self.master_socket, callback)
					yield callback
				else:
					break

			# Create a new TCPConnection for the socket 
			new_conn = self.connection_class(client_socket, client_addr, self)
			self.connections[id(new_conn)] = new_conn
			new_conn.start()

__all__ = [
	"TCPServer",
	"TCPConnection",
	"ConnectionClosedException",
	"ConnectionOverflowException"
]
