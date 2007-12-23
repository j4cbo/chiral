"""
Thread pool.

This provides a pool of threads to which any callable may be passed from a coroutine.
Example::

	@as_coro
	def threadpool_test():
	        yield threadpool.run_in_thread(time.sleep, 1)
"""

from __future__ import with_statement

import gc
import collections
import threading
import socket
import sys
import signal

from chiral.core.coroutine import returns_waitcondition, WaitForCallback
from chiral.net import tcp

_CHIRAL_RELOADABLE = True

class ThreadPoolWatcher(tcp.TCPConnection):
	"""
	Helper coroutine that waits for thread pool results to come in, then dispatches them
	to wherever they were called from.
	"""

	def __init__(self, pool):
		"""Constructor."""

		# Prevent replacement on reload.
		setattr(self, "__reload_update__", lambda oldobj: oldobj)

		self.pool = pool
		# Don't actually initialize until restart() is called the first time.

	def restart(self):
		"""Restart the ThreadPoolWatcher.

		After an operation is added to the thread pool's input queue, the adder should
		see if watcher.restart is null; if not, it should call restart(), which will do
		whatever is necessary to ensure that the watcher is functional.
		"""

		# At first, restart() does the actual TCP socket setup, etc. Later calls to restart()
		# will trigger a WaitForCallback on which connection_handler is blocking.

		read_socket, self.write_socket = socket.socketpair()
		self.restart = None

		tcp.TCPConnection.__init__(self, None, read_socket)
		self.start()

	def connection_handler(self):
		while True:
			incoming = yield self.recv(128)
			for _index in incoming:
				# Each received byte indicates one result in the output queue.
				result, exc, recipient = self.pool.output_queue.popleft()
				if exc is not None:
					recipient.throw(exc)
				else:
					recipient(result)

			# If there are any operations still in process, continue the loop.
			with self.pool.queue_lock:
				ops = len(self.pool.input_queue) + self.pool.active_workers \
				      + len(self.pool.output_queue)

			if not ops:
				self.restart = WaitForCallback("ThreadPoolWatcher restart")
				yield self.restart
				self.restart = None


class ThreadPool(object):
	"""Thread pool singleton ."""

	# The thread pool system has enough state that it makes sense to store
	# it all in an object rather than a bunch of module globals. Using an
	# object also simplifies reload handling.

	def __init__(self):
		"""Initialize the thread pool."""

		# queue_lock regulates access to all the queues, and must be
		# acquired before doing any manipulation on them. The lock is
		# held only for the duration of the manipulation.
		self.queue_lock = threading.Lock()

		# input_queue_sem holds the number of items in the input queue.
		# A Queue cannot be used here, because moving an operation from
		# input_queue to the active state (active_workers) must be atomic.
		self.input_queue = collections.deque()
		self.input_queue_sem = threading.Semaphore(0)

		self.active_workers = 0
		self.output_queue = collections.deque()

		self.worker_threads = {}

		self.watcher = ThreadPoolWatcher(self)


class ThreadPoolContainer(object):
	pool = ThreadPool()
	@staticmethod
	def __reload_update__(newobj):
		return newobj


class WorkerThread(threading.Thread):

	__states = ( "waiting", "active", "returning" )

	def __init__(self):
		"""Constructor."""
		threading.Thread.__init__(self)
		self.setDaemon(True)
		self.state = 0
		self.operation_info = None, None, None

	def run(self):
		"""Main loop for worker threads."""

		# The pool object itself is never replaced (though it may be modified by xreload()),
		# so it's safe to store a reference here.

		pool = ThreadPoolContainer.pool

		while True:
			# Retrieve an operation from the queue, run it, and put the result in
			# the output queue
			pool.input_queue_sem.acquire()
			with pool.queue_lock:
				operation, args, kwargs, recipient = pool.input_queue.popleft()
				self.operation_info = operation, args, kwargs
				pool.active_workers += 1

			self.state = 1

			try:
				result, exc = operation(*args, **kwargs), None
			except:
				result, exc = None, sys.exc_info()

			self.state = 2

			with pool.queue_lock:
				pool.output_queue.append((result, exc, recipient))
				pool.active_workers -= 1
				pool.watcher.write_socket.send("x")

			self.state = 0

	def __repr__(self):
		if self.state == 0:
			opinfo = ""
		else:
			opinfo = "; running %s" % (self.operation_info[0], ) 

		return "<WorkerThread: state %s%s>" % (self.__states[self.state], opinfo)

	def _chiral_introspect(self):
		return "thread", id(self)

	def introspection_info(self):
		if self.state == 0:
			return (self, )
		else:
			return (
				self,
				"Operation: %s" % self.operation_info[0],
				"Args:", list(self.operation_info[1]),
				"Kwargs: ", self.operation_info[2]
			)

@returns_waitcondition
def run_in_thread(operation, *args, **kwargs):
	"""Run operation(*args, **kwargs) in a thread, returning the result."""

	pool = ThreadPoolContainer.pool

	# Restart the ThreadPoolWatcher if it wasn't listening already
	if pool.watcher.restart:
		pool.watcher.restart()

	# XXX need a good algorithm for managing thread pool size
	if not pool.worker_threads or (pool.active_workers == len(pool.worker_threads) and pool.active_workers < 4):
		worker = WorkerThread()
		worker.start()
		pool.worker_threads[id(worker)] = worker

	recipient = WaitForCallback("run_in_thread %r" % (operation, ))

	with pool.queue_lock:
		pool.input_queue.append((operation, args, kwargs, recipient))
		pool.input_queue_sem.release()

	return recipient


class _chiral_introspection(object):
	def main(self):

		pool = ThreadPoolContainer.pool
		out = [
			"Worker threads: %d" % len(pool.worker_threads),
			"Input queue: %d jobs" % (len(pool.input_queue)),
		]
		out.extend(pool.worker_threads.itervalues())
		return out

        def thread(self, thread_id):
                try:
                        thread = ThreadPoolContainer.pool.worker_threads[int(thread_id)]
                except KeyError:
                        return None

                return thread.introspection_info()

__all__ = [ "run_in_thread" ]

