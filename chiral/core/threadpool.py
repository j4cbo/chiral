"""
Thread pool.

This provides a pool of threads to which any callable may be passed from a coroutine.
Example::

	@as_coro
	def threadpool_test():
	        yield threadpool.run_in_thread(time.sleep, 1)

The thread pool is initialized automatically when the module is loaded; none of the classes
defined here should be instantiated by the user.

For implementation details, see the documentation for `ThreadPool`.
"""

from __future__ import with_statement

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
	Helper coroutine that dispatches thread pool results to wherever they were called from.

	See `ThreadPool` for more information.
	"""

	def __init__(self):
		"""Constructor."""

		# Prevent replacement on reload.
		setattr(self, "__reload_update__", lambda oldobj: oldobj)

		read_socket, self.write_socket = socket.socketpair()

		tcp.TCPConnection.__init__(self, None, read_socket)

		# Don't actually start until restart() is called the first time.

	def restart(self):
		"""Restart the ThreadPoolWatcher.

		After an operation is added to the thread pool's input queue, the adder (generally
		`run_in_thread`) should see if watcher.restart is null; if not, it should call
		restart(), which will ensure that the watcher is functional.
		"""

		self.restart = None
		self.start()

	def connection_handler(self):
		"""Main event processing loop."""
		while True:
			incoming = yield self.recv(128)
			for _index in incoming:
				# Each received byte indicates one result in the output queue.
				result, exc, recipient = ThreadPool.output_queue.popleft()
				if exc is not None:
					recipient.throw(exc)
				else:
					recipient(result)

			# If there are any operations still in process, continue the loop.
			with ThreadPool.queue_lock:
				ops = len(ThreadPool.input_queue) + ThreadPool.active_workers \
				      + len(ThreadPool.output_queue)

			if not ops:
				self.restart = WaitForCallback("ThreadPoolWatcher restart")
				yield self.restart
				self.restart = None


class ThreadPool(object):
	"""Thread pool singleton object.

	This is essentially a dummy class; it exists as a way to store all the thread pool
	state such that it won't be wiped out by `xreload`.

	When `run_in_thread` is called, it creates a `WaitForCallback` which will later be
	called with the result, and then appends the function, arguments, and callback to the
	ThreadPool's input queue. If there are no worker threads, or more are necessary,
	`run_in_thread` spawns a new one. It then increments ("releases") a ``threading.Semaphore``
	associated with the input queue, and returns the `WaitForCallback` to the caller.

	The worker threads, when not active, block on the input queue semaphore. The semaphore
	ensures that each time an item is added to the input queue, exactly one worker thread
	will be woken up. It then retrieves the operation from the queue, runs it with the
	args and kwargs that were passed to `run_in_thread`, and pushes the result onto the
	`ThreadPool`'s output queue.

	A separate helper coroutine, the `ThreadPoolWatcher`, is responsible for returning results
	to the main thread. The watcher is initialized from `run_in_thread` the first time the thread
	pool is used. It creates a socketpair, and uses the reactor's socket event handling to wait on
	the read end. Whenever a worker thread pushes a result onto the output queue, it writes a single
	byte to the watcher's write socket. This will awaken the `ThreadPoolWatcher` coroutine in the
	main thread, which ``recv``-s as many bytes as are available, and pops and dispatches that many
	results from the output queue.
	"""
 
	# queue_lock regulates access to all the queues, and must be
	# acquired before doing any manipulation on them. The lock is
	# held only for the duration of the manipulation.
	queue_lock = threading.Lock()

	# input_queue_sem holds the number of items in the input queue.
	# A Queue cannot be used here, because moving an operation from
	# input_queue to the active state (active_workers) must be atomic.
	input_queue = collections.deque()
	input_queue_sem = threading.Semaphore(0)

	active_workers = 0
	output_queue = collections.deque()

	worker_threads = {}

	watcher = ThreadPoolWatcher()

	@staticmethod
	def __reload_update__(newobj):
		"""Prevent the class from being reinitialized by `xreload`."""
		return newobj


class WorkerThread(threading.Thread):
	"""
	Worker thread.

	See `ThreadPool` for more documentation.
	"""

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

		pool = ThreadPool

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
		"""Returns the introspection information for this thread."""
		return "thread", id(self)


@returns_waitcondition
def run_in_thread(operation, *args, **kwargs):
	"""Run operation in a thread, returning the result."""

	# Restart the ThreadPoolWatcher if it wasn't listening already
	if ThreadPool.watcher.restart:
		ThreadPool.watcher.restart()

	# XXX need a good algorithm for managing thread pool size
	if not ThreadPool.worker_threads or \
	   (ThreadPool.active_workers == len(ThreadPool.worker_threads) and ThreadPool.active_workers < 4):
		worker = WorkerThread()
		worker.start()
		ThreadPool.worker_threads[id(worker)] = worker

	recipient = WaitForCallback("run_in_thread %r" % (operation, ))

	with ThreadPool.queue_lock:
		ThreadPool.input_queue.append((operation, args, kwargs, recipient))
		ThreadPool.input_queue_sem.release()

	return recipient


class _chiral_introspection(object):
	"""Module-level introspection routines."""
	def main(self):

		out = [
			"Worker threads: %d" % len(ThreadPool.worker_threads),
			"Input queue: %d jobs" % (len(ThreadPool.input_queue)),
		]
		out.extend(ThreadPool.worker_threads.itervalues())
		return out

	def thread(self, thread_id):
		try:
			thread = ThreadPool.worker_threads[int(thread_id)]
		except KeyError:
			return None
	
		if thread.state == 0:
			return (thread, )
		else:
			return (
				thread,
				"Operation: %s" % thread.operation_info[0],
				"Args:", list(thread.operation_info[1]),
				"Kwargs: ", thread.operation_info[2]
			)

__all__ = [ "run_in_thread" ]

