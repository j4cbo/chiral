"""Network event handling."""

from chiral.core import tasklet, stats

import time
import heapq
import select
import traceback
import weakref

# Use psyco for this module, if available.
try:
	import psyco.classes
except ImportError:
	psyco = False

class Looper(psyco.classes.psyobj if psyco else object):
	"""
	Base class for Looper objects.
	"""

	def __init__(self):
		self._events = []

		self._close_list = weakref.WeakValueDictionary()

	def close_on_exit(self, sock):
		"""Add sock to a list of sockets to be closed when the looper terminates."""
		self._close_list[id(sock)] = sock

	def _handle_scheduled_events(self):
		"""Handle any internally scheduled events."""

		while len(self._events) > 0:
			next_event_time, next_event_cb = self._events[0]
			if next_event_time > time.time():
				break

			next_event_cb()

			del self._events[0]

	def _run_once(self):
		"""
		Run one iteration of the main event handling loop.

		This should be overridden in a derived class.
		"""
		raise NotImplementedError

	def run(self):
		"""Run the main event processing loop."""

		# Because this is the main event processing loop, it cannot
		# be replaced when the module is reloaded. Therefore, as
		# little logic as possible should happen here.
		while True:
			stats.increment("chiral.inet.netcore.%s.loops" % self.__class__.__name__)
			res = self._run_once()
			if not res:
				break

		print "Done."

		close_list = list(self._close_list.itervalues())
		for sock in close_list:
			sock.close()
			

	def schedule(self, delay = None, callbacktime = None):
		"""
		Run callback at some point in the future, either absolute or relative. The
		"time" parameter, if given, should be a datetime.datetime object or UNIX
		timestamp; "delay" may be either a number of seconds or a datetime.timedelta.
		time in seconds or a datetime.timedelta.

		If both time and delay are None, the callback will be run as soon as possible.
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
			raise ValueError("must specify either callbacktime or delay")

		callback = tasklet.WaitForCallback()

		# Now the time is normalized; just add it to the queue.
		heapq.heappush(self._events, (callbacktime, callback))

		return callback

	def time_to_next_event(self):
		"""Return the time, in seconds, until the next scheduled event."""
		if len(self._events) > 0:
			next_event_time = self._events[0][0]
			return max(next_event_time - time.time(), 0)
		else:
			return None


class SelectLooper(Looper):
	"""Looper using select()"""

	def __init__(self):
		Looper.__init__(self)
		self._read_sockets = {}
		self._write_sockets = {}

	def wait_for_readable(self, sock, callback):
		"""Register callback to be called next time sock is readable."""
		assert sock not in self._read_sockets
		self._read_sockets[sock] = callback

	def wait_for_writeable(self, sock, callback):
		"""Register callback to be called next time sock is writeable."""
		assert sock not in self._write_sockets
		self._write_sockets[sock] = callback

	def _run_once(self):
		"""Run one iteration of the event handler."""

		stats.increment("chiral.inet.netcore.select_calls")

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
			For each item in items: call the callback in event_list whose
			key is that item.
			"""
			for key in items:
				callback = event_list[key]
				del event_list[key]

				# Yes, we really do want to catch /all/ Exceptions
				# pylint: disable-msg=W0703

				try:
					callback()
				except Exception:
					print "Unhandled exception in TCP event %s:" % (callback, )
					traceback.print_exc() 


		_handle_events(rlist, self._read_sockets)
		_handle_events(wlist, self._write_sockets)

		self._handle_scheduled_events()

		return True


class EpollLooper(Looper):
	"""
	Looper using epoll()
	"""

	def __init__(self, default_size = 10):
		Looper.__init__(self)

		self.epoll_fd = epoll.epoll_create(default_size)

		self._sockets = {}

	def wait_for_readable(self, sock, callback):
		"""Register callback to be called next time sock is readable."""
		assert sock.fileno() not in self._sockets
		self._sockets[sock.fileno()] = sock, callback, epoll.EPOLLIN
		epoll.epoll_ctl(self.epoll_fd, epoll.EPOLL_CTL_ADD, sock.fileno(), epoll.EPOLLIN)

	def wait_for_writeable(self, sock, callback):
		"""Register callback to be called next time sock is writeable."""
		assert sock.fileno() not in self._sockets
		self._sockets[sock.fileno()] = sock, callback, epoll.EPOLLOUT
		epoll.epoll_ctl(self.epoll_fd, epoll.EPOLL_CTL_ADD, sock.fileno(), epoll.EPOLLOUT)

	def _run_once(self):
		"""Run one iteration of the event handler."""

		delay = self.time_to_next_event()

		if delay is None:
			delay = -1
		else:
			delay *= 1000

		if delay == -1 and len(self._sockets) == 0:
			return False

		try:
			events = epoll.epoll_wait(self.epoll_fd, 50, delay)
		except KeyboardInterrupt:
			# Just return.
			return False

		for event_op, event_fd in events:
			sock, callback, desired_events = self._sockets[event_fd]
			assert desired_events & event_op
			del self._sockets[event_fd]

			epoll.epoll_ctl(self.epoll_fd, epoll.EPOLL_CTL_DEL, sock.fileno(), 0)

			# Yes, we really do want to catch /all/ Exceptions
			# pylint: disable-msg=W0703

			try:
				callback()
			except Exception:
				print "Unhandled exception in TCP event %s:" % (callback, )
				traceback.print_exc() 

		self._handle_scheduled_events()

		return True

# Attempt to import epoll. If it's not available, forget about EpollLooper.
try:
	import epoll
except ImportError:
	del EpollLooper
