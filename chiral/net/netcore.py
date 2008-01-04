"""
Network event handling.

Chiral's main event loop is provided by the `Reactor` class. After program initialization,
the Reactor is responsible for determining what internal (timer) and external (socket activity)
events have occured. The event loop is as such:

1. Determine when the next scheduled event should happen.
2. Identify which sockets have coroutines waiting on them.
3. Perform a system call that waits for socket activity or a timeout, whichever comes first.
4. Dispatch all incoming socket events.
5. Dispatch all timer events that are ready to run. 

These steps are performed by `Reactor._run_once`. The main `Reactor.run` function simply calls
``_run_once`` until it indicates that there are no more events to process.

Step (3) is traditionally performed by the ``select()`` system call. However, ``select()`` requires
that the set of "interesting" sockets be passed in to each call, and so scales O(n) as the number of
sockets increases. Due to its internal bitfield data structure, it is also generally limited to 1024
simultaneous file descriptors. As such, various platform-specific calls like ``epoll()`` (Linux 2.6+)
and ``kqueue`` (FreeBSD 4.1+, Mac OS X 10.3+) have been introduced. These have the key advantage that
step (2) is performed implicitly, when it is determined that the socket is "interesting", and not at
each event loop. They therefore scale O(1) with respect to the number of open, idle sockets.

Python does not include ``epoll()`` or ``kqueue()`` in its standard library, so ctypes-based bindings
are provided in `chiral.os`. When `chiral.net.netcore` is loaded, it automatically checks for the
availability of the platform-specific Reactor classes, and falls back to `SelectReactor` if they are not
available.

Chiral automatically instantiates a `Reactor` instance and makes it available as ``chiral.net.reactor``.
Users should not create new Reactors themselves.

See the documentation for the `Reactor` class for information on its specific methods.
"""

# Chiral, copyright (c) 2007 Jacob Potter
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 2.

from chiral.core import coroutine, stats

import time
import heapq
import select
import traceback
import weakref
import errno

class ConnectionException(Exception):
	"""Indicates that the connection has failed and will be closed."""

class ConnectionClosedException(ConnectionException):
	"""Indicates that the connection was closed by the remote end."""

class Reactor(object):
	"""Base class for Reactor objects."""

	def __init__(self):
		self._events = []

		self._close_list = weakref.WeakValueDictionary()

	def close_on_exit(self, sock):
		"""Add `sock` to a list of sockets to be closed when the reactor terminates."""
		self._close_list[id(sock)] = sock

	def _handle_scheduled_events(self):
		"""
		Handle any internally scheduled events.

		This should only be called by `Reactor._run_once`.
		"""

		while len(self._events) > 0:
			next_event_time, next_event_cb = self._events[0][:2]
			if next_event_time > time.time():
				break

			next_event_cb()

			del self._events[0]

	def _run_once(self):
		"""
		Run one iteration of the main event handling loop.

		This should only be called by `Reactor.run`.
		"""
		raise NotImplementedError

	def run(self):
		"""Run the main event processing loop."""

		# Because this is the main event processing loop, it cannot
		# be replaced when the module is reloaded. Therefore, as
		# little logic as possible should happen here.
		while True:
			stats.increment("chiral.net.netcore.%s.loops" % self.__class__.__name__)
			res = self._run_once()
			if not res:
				break

		close_list = list(self._close_list.itervalues())
		for sock in close_list:
			sock.close()
			
	@coroutine.returns_waitcondition
	def schedule(self, delay = None, callbacktime = None):
		"""
		Return a WaitCondition that will fire at some point in the future.

		If both ``time`` and ``delay`` are None, the WaitCondition will fire as soon as
		possible, during the next reactor loop. Since the reactor handles socket events
		before scheduled calls, one can yield control to the Reactor to handle incoming
		events (i.e. during a potentially CPU-intensive operation) with::

			yield reactor.schedule()

		:param callbacktime: An absolute time or UNIX timestamp.
		:type callbacktime: datetime.datetime, int, float
		:param delay: A timedelta object or relative number of seconds.
		:type delay: datetime.timedelta, int, float
		"""

		now = time.time()

		if delay:
			# If "delay" has been specified, use it instead
			if all(hasattr(delay, a) for a in ("days", "seconds", "microseconds")):
				delay = (delay.days * 86400) + delay.seconds + (delay.microseconds / 1e6)

			try:
				callbacktime = now + float(delay)
			except TypeError:
				raise TypeError("delay must be a number or timedelta")

		elif callbacktime:
			# Convert to timestamp if necessary
			if hasattr(callbacktime, "timetuple"):
				callbacktime = time.mktime(callbacktime.timetuple()) + \
					(callbacktime.microseconds / 1e6)

			else:
				try:
					callbacktime = float(callbacktime)
				except TypeError:
					raise TypeError("callbacktime must be a number or datetime")

		else:
			callbacktime = now

		callback = coroutine.WaitForCallback("reactor.schedule(callbacktime=%s)" % (callbacktime, ))

		# Now the time is normalized; just add it to the queue.
		heapq.heappush(self._events, (callbacktime, callback))

		return callback

	def wait_for_readable(self, sock):
		"""Return a WaitCondition for readability on a socket.

		When the resultant WaitCondition is yielded, ``sock`` will be added to the list of
		sockets of interest for the next event loop. If `WaitCondition.unbind` is called
		(due to the yielding coroutine being killed, for example), the socket will be automatically
		removed from the list. 

		"""
		raise NotImplementedError

	def wait_for_writeable(self, sock):
		"""
		Return a WaitCondition for writeability on a socket.

		This behaves analogously to `wait_for_readable`.
		"""
		raise NotImplementedError

	def time_to_next_event(self):
		"""Return the time, in seconds, until the next scheduled event."""
		if len(self._events) > 0:
			next_event_time = self._events[0][0]
			return max(next_event_time - time.time(), 0)
		else:
			return None


class SelectReactor(Reactor):
	"""Reactor using select()"""

	def __init__(self):
		Reactor.__init__(self)
		self._read_sockets = {}
		self._write_sockets = {}

	class WaitForEvent(coroutine.WaitCondition):
		"""
		Select for readability on the given socket in the next iteration of
		the reactor's select() loop.
		"""

		def __init__(self, sock, reactor_instance, event_list):
			"""
			Constructor.

			sock will be passed to select.select(); reactor must be a SelectReactor.
			"""
			self.sock = sock
			self.reactor = reactor_instance
			self.event_list = event_list
			self.bound_coro = None

		def bind(self, coro):
			"""Bind to coro, adding the socket to the select list."""
			assert self.bound_coro is None
			assert coro not in self.event_list
			self.event_list[self.sock] = coro
			self.bound_coro = coro

		def unbind(self, coro):
			"""Unbind from coro and remove the socket from the select list."""
			assert self.bound_coro is coro
			del self.event_list[self.sock]
			self.bound_coro = None

		def __repr__(self):
			return "<SelectReactor.WaitForEvent: fd %r>" % (self.sock.fileno(), )


	def wait_for_readable(self, sock):
		"""Return a WaitCondition for readability on ``sock``."""
		return self.WaitForEvent(sock, self, self._read_sockets)

	def wait_for_writeable(self, sock):
		"""Return a WaitCondition for writeability on ``sock``."""
		return self.WaitForEvent(sock, self, self._write_sockets)

	def _run_once(self):
		"""Run one iteration of the event handler."""

		stats.increment("chiral.net.netcore.select_calls")

		delay = self.time_to_next_event()

		if delay is None and len(self._read_sockets) == 0 and len(self._write_sockets) == 0:
			return False

		try:
			rlist, wlist = select.select(
				self._read_sockets.keys(),
				self._write_sockets.keys(),
				(),
				delay
			)[:2]
		except KeyboardInterrupt:
			# Just return.
			return False


		def _handle_events(items, event_list):
			"""
			For each item in items: resume the coroutine in event_list whose key is that item.
			key is that item.
			"""
			for key in items:
				if key not in event_list:
					continue

				coro = event_list[key]
				del event_list[key]

				# Yes, we really do want to catch /all/ Exceptions
				# pylint: disable-msg=W0703
				try:
					coro.resume(None)
				except Exception:
					print "Unhandled exception in TCP event %s:" % (coro, )
					traceback.print_exc() 


		_handle_events(rlist, self._read_sockets)
		_handle_events(wlist, self._write_sockets)

		self._handle_scheduled_events()

		return True


class EpollReactor(Reactor):
	"""
	Reactor using epoll()
	"""

	def __init__(self, default_size = 10):
		Reactor.__init__(self)

		self.epoll = epoll.Epoll(default_size)

		self._sockets = {}

	class WaitForEvent(coroutine.WaitCondition):
		"""Wait for an event."""

		def __init__(self, sock, reactor_instance, event):
			"""
			Constructor.

			sock will be passed to epoll_ctl(); reactor must be an EpollReactor.
			"""

			self.sock = sock
			self.reactor = reactor_instance
			self.event = event
			self.bound_coro = None

		def bind(self, coro):
			"""Bind to coro, adding the socket to the epoll list."""
			assert self.bound_coro is None
			assert self.sock.fileno() not in self.reactor._sockets
			self.reactor._sockets[self.sock.fileno()] = self.sock, coro, self.event
			self.reactor.epoll.ctl(epoll.EPOLL_CTL_ADD, self.sock.fileno(), self.event)
			self.bound_coro = coro

		def unbind(self, coro):
			"""Unbind from coro and remove the socket from the select list."""
			assert self.bound_coro is coro
			assert self.sock.fileno() in self.reactor._sockets
			del self.reactor._sockets[self.sock.fileno()]
			try:
				self.reactor.epoll.ctl(epoll.EPOLL_CTL_DEL, self.sock.fileno(), 0)
			except OSError, exc:
				if exc.errno == errno.ENOENT:
					# The fd isn't in the list anymore anyway; fine.
					pass
				else:
					raise exc

			self.bound_coro = None

		def __repr__(self):
			return "<EpollReactor.WaitForEvent: fd %r>" % (self.sock.fileno(), )

	def wait_for_readable(self, sock):
		"""Return a WaitCondition for readability on ``sock``."""
		return self.WaitForEvent(sock, self, epoll.EPOLLIN)

	def wait_for_writeable(self, sock):
		"""Return a WaitCondition for writeability on ``sock``."""
		return self.WaitForEvent(sock, self, epoll.EPOLLOUT)

	def _run_once(self):
		"""Run one iteration of the event handler."""

		delay = self.time_to_next_event()

		if delay is None and len(self._sockets) == 0:
			return False

		try:
			events = self.epoll.wait(10, delay)
		except KeyboardInterrupt:
			# Just return.
			return False

		for _event_flags, event_fd in events:
			if event_fd not in self._sockets:
				continue

			sock, coro, _interested = self._sockets[event_fd]
			del self._sockets[event_fd]

			self.epoll.ctl(epoll.EPOLL_CTL_DEL, sock.fileno(), 0)

			# Yes, we really do want to catch /all/ Exceptions
			# pylint: disable-msg=W0703

			try:
				coro.resume(None)
			except Exception:
				print "Unhandled exception in TCP event %s:" % (coro, )
				traceback.print_exc() 

		self._handle_scheduled_events()

		return True

class KqueueReactor(Reactor):
	"""
	Reactor using kqueue()/kevent()
	"""

	def __init__(self):
		Reactor.__init__(self)

		self.queue = kqueue.Kqueue()
		self._sockets = {}

	class WaitForEvent(coroutine.WaitCondition):
		"""Wait for an event."""

		def __init__(self, sock, reactor_instance, event):
			"""
			Constructor.

			reactor must be a KqueueReactor.
			"""

			self.sock = sock
			self.reactor = reactor_instance
			self.event = event
			self.bound_coro = None

		def bind(self, coro):
			"""Bind to coro, adding the socket to the epoll list."""
			assert self.bound_coro is None
			assert self.sock.fileno() not in self.reactor._sockets
			self.reactor._sockets[self.sock.fileno()] = self.sock, coro
			self.reactor.queue.change_events((
				self.sock.fileno(),
				self.event,
				kqueue.EV_ADD | kqueue.EV_ONESHOT,
				0,
				None,
				None
			))
			self.bound_coro = coro

		def unbind(self, coro):
			"""Unbind from coro and remove the socket from the select list."""
			assert self.bound_coro is coro
			assert self.sock.fileno() in self.reactor._sockets
			del self.reactor._sockets[self.sock.fileno()]
			try:
				self.reactor.queue.change_events((
					self.sock.fileno(),
					self.event,
					kqueue.EV_DELETE,
					0,
					None,
					None
				))
			except OSError, exc:
				if exc.errno == errno.ENOENT:
					# The fd isn't in the list anymore anyway; fine.
					pass
				else:
					raise exc

			self.bound_coro = None

		def __repr__(self):
			return "<KqueueReactor.WaitForEvent: fd %r>" % (self.sock.fileno(), )

	def wait_for_readable(self, sock):
		"""Return a WaitCondition for readability on ``sock``."""
		return self.WaitForEvent(sock, self, kqueue.EVFILT_READ)

	def wait_for_writeable(self, sock):
		"""Return a WaitCondition for writeability on ``sock``."""
		return self.WaitForEvent(sock, self, kqueue.EVFILT_WRITE)

	def _run_once(self):
		"""Run one iteration of the event handler."""

		delay = self.time_to_next_event()

		if delay is None and len(self._sockets) == 0:
			return False

		try:
			events = self.queue.kevent(None, return_count = 10, timeout = delay)
		except KeyboardInterrupt:
			# Just return.
			return False

		for ident, _filter, _flags, _fflags, _data, _udata in events:
			if ident not in self._sockets:
				continue

			sock, coro = self._sockets[ident]
			del self._sockets[ident]

			# Yes, we really do want to catch /all/ Exceptions
			# pylint: disable-msg=W0703
			try:
				coro.resume(None)
			except Exception:
				print "Unhandled exception in TCP event %s:" % (coro, )
				traceback.print_exc() 

		self._handle_scheduled_events()

		return True

# Attempt to import epoll. If it's not available, forget about EpollReactor.
# "DefaultReactor" is still a class, not a constant.
#pylint: disable-msg=C0103
try:
	from chiral.os import epoll
	DefaultReactor = EpollReactor
	del KqueueReactor
except ImportError:
	del EpollReactor
	try:
		from chiral.os import kqueue
		DefaultReactor = KqueueReactor
	except ImportError:
		del KqueueReactor
		DefaultReactor = SelectReactor

reactor = DefaultReactor()

__all__ = [
	"ConnectionException",
	"ConnectionClosedException",
]
