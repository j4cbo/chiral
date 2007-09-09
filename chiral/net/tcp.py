"""TCP connection handling classes."""

# Chiral, copyright (c) 2007 Jacob Potter
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 2.

from chiral.core import coroutine
from chiral.net import reactor
from chiral.net.netcore import ConnectionException, ConnectionClosedException

import os
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

class ConnectionOverflowException(ConnectionException):
	"""Indicates that an excessive amount of data was received by read_line()."""

class TCPConnection(coroutine.Coroutine):
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

	def connection_handler_completed(self, value, exception):
		"""
		Completion callback.

		This will be run as a completion callback when the connection handler 
		terminates; see ``chiral.core.coroutine.Coroutine.add_completion_callback()``.

		By default, this swallows ConnectionClosedExceptions, and closes the connection.
		It may be overridden in a derived class.
		"""

		if exception:
			_exc_type, exc_value, _exc_traceback = exception
			if isinstance(exc_value, ConnectionClosedException):
				return (None, None)

		self.close()

	def close(self):
		"""
		Call self.close() on a connection to perform a clean shutdown.
		"""

		self.remote_sock.close()

	@coroutine.as_coro
	def _read_line_coro(self, max_len, delimiter):
		"""Helper coroutine created by read_line if data is not immediately available."""
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


	@coroutine.returns_waitcondition
	def read_line(self, max_len = 1024, delimiter = "\r\n"):
		"""
		Read a line (delimited by any member of the "delimiters" tuple) from
		the client. If more than max_length characters are read before a
		delimiter is found, a ConnectionOverflowException will be raised.
		"""

		# Check if the delimiter is already in the buffer.
		if delimiter in self._buffer[:max_len]:
			out, self._buffer = self._buffer.split(delimiter, 1)
			return coroutine.WaitForNothing(out)

		# If not, attempt to recv()
		try:
			new_data = self.remote_sock.recv(max_len)
		except socket.error, exc:
			if exc[0] == errno.EAGAIN:
				# OK, we're going to need to spawn a new coroutine.
				return self._read_line_coro(max_len, delimiter)
			else:
				# Something else is broken; raise it again.
				raise exc

		# So recv() worked and we now have some more data. Add it to the buffer,
		# and check for the delimiter again.
		self._buffer += new_data
		if delimiter in self._buffer[:max_len]:
			out, self._buffer = self._buffer.split(delimiter, 1)
			return coroutine.WaitForNothing(out)

		# No luck finding the delimiter. Make sure we haven't overflowed...
		if len(self._buffer) > max_len:
			raise ConnectionOverflowException()

		# The line isn't available yet. Spawn a coroutine to deal with it.
		return self._read_line_coro(max_len, delimiter)



	@coroutine.as_coro_waitcondition
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

		callback = coroutine.WaitForCallback(cb_func)

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
					cb_func(self.remote_sock, blocked_operation_handler)
					return callback
				else:
					raise exc
		else:
			# Don't bother. (try_now is set False by functions like read_line,
			# which attempt the low-level operations themselves first to avoid
			# creating Tasklets unnecessarily.)
			cb_func(self.remote_sock, blocked_operation_handler)
			return callback

		return coroutine.WaitForNothing(res)

	@coroutine.returns_waitcondition
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

	@coroutine.as_coro_waitcondition
	def _sendall_coro(self, data):
		"""Helper coroutine created by sendall if not all data could be sent."""
		while data:
			res = yield self.send(data)
			data = data[res:]

	@coroutine.returns_waitcondition
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
			if exc[0] in (errno.EPIPE, errno.EBADF):
				raise ConnectionClosedException()
			elif exc[0] != errno.EAGAIN:
				raise exc
		else:
			# Only return now if /all/ the data was written
			if res == len(data):
				return
			else:
				data = data[res:]

		# There's still more data to be sent, so hand things off to the coroutine.
		return self._sendall_coro(data)

	@coroutine.returns_waitcondition
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

	@coroutine.returns_waitcondition
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
				callback = coroutine.WaitForCallback("sendfile")

				def blocked_operation_handler():
					"""Callback for asynchronous operations."""
					# Prevent pylint from complaining about "except Exception"
					# pylint: disable-msg=W0703
					try:
						res = sendfile(self.remote_sock.fileno(), infile.fileno(), offset, length)
					except OSError, exc:
						if exc.errno in (errno.EPIPE, errno.EBADF):
							callback.throw(ConnectionClosedException())
						else:
							callback.throw(exc)
					except Exception, exc:
						callback.throw(exc)
					else:
						callback(res[1])

				reactor.wait_for_writeable(self.remote_sock, blocked_operation_handler)
				return callback
			elif exc.errno in (errno.EPIPE, errno.EBADF):
				raise ConnectionClosedException()
			else:
				raise exc

		# sendfile() worked, so we're done.
		return coroutine.WaitForNothing(res[1])

	@coroutine.returns_waitcondition
	def connect(self):
		"""
		Connect or reconnect to the remote server.

		If this TCPConnection was created by passing an existing socket object to __init__,
		then connect() cannot be used and will raise RuntimeError.

		Otherwise, the TCPConnection must be connected with connect() before it can be used,
		and may be reconnected after any method raises a ConnectionClosedException.
		"""

		if not self._may_connect:
			raise RuntimeError("This TCPConnection may not be reconnected.")

		self.remote_sock.close()
		self.remote_sock = socket.socket()

		# Set the new socket nonblocking
		self.remote_sock.setblocking(0) # pylint: disable-msg=E1101

		try:
			self.remote_sock.connect(self.remote_addr)
		except socket.error, exc:
			if exc[0] == errno.EINPROGRESS:
				# The socket is connecting.

				callback = coroutine.WaitForCallback("connect")

				def blocked_connect_handler():
					"""Callback for asynchronous connect"""
					res = self.remote_sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
					if res == 0:
						callback()
					else:
						callback.throw(ConnectionException(res, os.strerror(res)))

				# Wait for the connection to go through
				reactor.wait_for_writeable(self.remote_sock, blocked_connect_handler)
				return callback

			elif exc[0] == errno.ECONNREFUSED:
				raise ConnectionException(errno.ECONNREFUSED, "Connection refused")
			else:
				raise exc
		else:
			return coroutine.WaitForNothing()

	def __init__(self, remote_addr, sock=None, server=None):
		"""
		Constructor.

		If the corresponding socket has already been created and connected, i.e. by a
		TCPServer calling `socket.accept()`, then it should be passed in as ``sock``.
		Otherwise, a new socket is created. The TCPConnection is then in an
		unconnected state; to connect to remote_addr, call `connect()` on the
		TCPConnection.
		"""
		
		self.remote_addr = remote_addr

		if sock:
			self.remote_sock = sock
			self._may_connect = False
		else:
			self.remote_sock = socket.socket()
			self._may_connect = True

		self.server = server

		# Set the socket nonblocking. Socket objects have some magic that
		# pylint doesn't grok, so suppress its "no setblocking member" warning.
		self.remote_sock.setblocking(0) # pylint: disable-msg=E1101

		self._buffer = ""

		coroutine.Coroutine.__init__(
			self,
			self.connection_handler(),
			default_callback = self.connection_handler_completed
		)

class TCPServer(coroutine.Coroutine):
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

		coroutine.Coroutine.__init__(self, self.acceptor())

	def acceptor(self):
		"""Main coroutine function.

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

					callback = coroutine.WaitForCallback("master readable")
					reactor.wait_for_readable(self.master_socket, callback)
					yield callback
				else:
					break

			# Create a new TCPConnection for the socket 
			new_conn = self.connection_class(client_addr, client_socket, self)
			self.connections[id(new_conn)] = new_conn
			new_conn.start()

__all__ = [
	"TCPServer",
	"TCPConnection",
	"ConnectionException",
	"ConnectionClosedException",
	"ConnectionOverflowException"
]
