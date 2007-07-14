"""Network event handling."""

from chiral.core import tasklet, stats

import time
import heapq
import select
import traceback
import sys
import gc

class Looper(object):

	def __init__(self):
		self._events = []

		self._close_list = []

	def close_on_exit(self, sock):
		self._close_list.append(sock)

	def _handle_scheduled_events(self):
		"""
		Handle any internally scheduled events.
		"""

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
		raise NotImplementedError()

	def run(self):
		"""Run the main event processing loop."""

		# Because this is the main event processing loop, it cannot
		# be replaced when the module is reloaded. Therefore, as
		# little logic as possible should happen here.
		while True:
			res = self._run_once()
			if not res: break

		print "Done."
		for s in self._close_list:
			s.close()
			

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
			except TypeError, e:
				raise TypeError("delay must be a number or timedelta")

		elif callbacktime:
			# Convert to timestamp if necessary
			if hasattr(callbacktime, "timetuple"):
				callbacktime = time.mktime(callbacktime.timetuple()) + \
					(callbacktime.microseconds / 1e6)

			else:
				try:
					callbacktime = float(callbacktime)
				except TypeError, e:
					raise TypeError("callbacktime must be a number or datetime")

		else:
			raise ValueError("must specify either callbacktime or delay")

		callback = callbacks.Callback(source="Looper schedule: %d" % callbacktime)

		# Now the time is normalized; just add it to the queue.
		heapq.heappush(self._events, (callbacktime, callback))

		return callback

class SelectLooper(Looper):
	def __init__(self):
		Looper.__init__(self)

		self._read_interest = {}
		self._write_interest = {}
		self._exc_interest = {}

	def wait_for_readable(self, sock, callback):
		"""Register callback to be called next time sock is readable."""
		if sock in self._read_interest:
			self._read_interest[sock].append(callback)
		else:
			self._read_interest[sock] = [ callback ]

	def wait_for_writeable(self, sock, callback):
		"""Register callback to be called next time sock is writeable."""
		if sock in self._write_interest:
			self._write_interest[sock].append(callback)
		else:
			self._write_interest[sock] = [ callback ]

	def _run_once(self):
		print "--- MAIN LOOP ---"
		now = time.time()
		delay = None

		stats.increment("chiral.inet.netcore.select_calls")

		gc.collect()
		tasklet.dump()

		if len(self._events) > 0:
			next_event_time, next_event_cb = self._events[0]
			delay = next_event_time - now
			if delay < 0: delay = 0

		print "Looper events:"
		for s, cbs in list(self._read_interest.iteritems()) + list(self._write_interest.iteritems()):
			print "fd %s: %s" % (s.fileno(), cbs)

#		print "selecting on %d read, %d write, %d exception, %s timeout" % (
#			len(self._read_interest),
#			len(self._write_interest),
#			len(self._exc_interest),
#			delay
#		)

		if delay is None and len(self._read_interest) == 0 and \
		   len(self._write_interest) == 0 and len(self._exc_interest) == 0:
			return False

		try:
			rlist, wlist, xlist = select.select(
				self._read_interest.keys(),
				self._write_interest.keys(),
				self._exc_interest.keys(),
				delay
			)
		except KeyboardInterrupt:
			# Just return.
			return False

#		print "select() returned: %d read, %d write, %d exception after %s seconds" % (
#			len(rlist),
#			len(wlist),
#			len(xlist),
#			time.time() - now
#		)

		def _handle_events(items, event_lists):
			"""
			Given a list of items, each of which is the key to a list of
			callback functions in event_lists, call all callbacks registered
			on each item.
				"""
			if not len(items): return

			for key in items:
				event_list = list(event_lists[key])
				del event_lists[key]
				for e in event_list:
					try:
						e()
					except:
						print "Unhandled exception in TCP event: %s" % e
						traceback.print_exc() 


		_handle_events(rlist, self._read_interest)
		_handle_events(wlist, self._write_interest)
		_handle_events(xlist, self._exc_interest)

		self._handle_scheduled_events()

		return True


try:
	import epoll
	epoll_available = True
except:
	epoll_available = False


class EpollLooper(Looper):

	def __init__(self, default_size = 10):
		Looper.__init__(self)
		self.epoll_fd = epoll.epoll_create(default_size)

		self._sockets = {}

	def wait_for_readable(self, sock, cb):
		"""Register callback to be called next time sock is readable."""
		assert sock.fileno() not in self._sockets
		self._sockets[sock.fileno()] = sock, cb, epoll.EPOLLIN
		ret = epoll.epoll_ctl(self.epoll_fd, epoll.EPOLL_CTL_ADD, sock.fileno(), epoll.EPOLLIN)

	def wait_for_writeable(self, sock, cb):
		"""Register callback to be called next time sock is writeable."""
		assert sock.fileno() not in self._sockets
		self._sockets[sock.fileno()] = sock, cb, epoll.EPOLLOUT
		epoll.epoll_ctl(self.epoll_fd, epoll.EPOLL_CTL_ADD, sock.fileno(), epoll.EPOLLOUT)

	def _run_once(self):
#		print "--- MAIN LOOP ---"
		now = time.time()
		delay = None
#		gc.collect()
#		tasklet.dump()

		stats.increment("chiral.inet.netcore.epoll_calls")

		if len(self._events) > 0:
			next_event_time, next_event_cb = self._events[0]
			delay = (next_event_time - now) * 1000
			if delay < 0: delay = 0
		else:
			delay = -1

#		print "calling epoll_wait, timeout %s" % (delay, )
#		print "Looper events:"
#		for sock, cb, events in self._sockets.itervalues():
#			print "fd %s: %s, events %s" % (sock.fileno(), cb, events)

		if delay == -1 and len(self._sockets) == 0:
			return False

		try:
			events = epoll.epoll_wait(self.epoll_fd, 50, delay)
		except KeyboardInterrupt:
			# Just return.
			return False

#		print "epoll_wait() returned: %d events after %s seconds" % (
#			len(events),
#			time.time() - now
#		)

		for epop, fd in events:
			sock, cb, desired_epop = self._sockets[fd]
			del self._sockets[fd]

#			print "epoll: handling event on fd %s, cb %s" % (sock.fileno(), cb)
			epoll.epoll_ctl(self.epoll_fd, epoll.EPOLL_CTL_DEL, sock.fileno(), 0)

			try:
				cb()
			except:
				print "Unhandled exception in TCP event: %s" % cb
				traceback.print_exc(file=sys.stdout) 
				sys.exit(1)

		self._handle_scheduled_events()

		return True
