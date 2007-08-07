"""
Thread pool.

This provides a pool of threads to which any callable may be passed from a coroutine.
Example::

	@as_coro
	def threadpool_test():
	        yield threadpool.run_in_thread(time.sleep, 1)
"""

from __future__ import with_statement

import collections
import threading
import socket
import sys
import signal

from chiral.core.coroutine import returns_waitcondition, WaitForCallback
from chiral.net import tcp

# _QUEUE_LOCK regulates access to all the queues, and must be acquired before doing
# any manipulation on them. The lock is held only for the duration of the manipulation.
_QUEUE_LOCK = threading.Lock()

# _INPUT_QUEUE_SEM holds the number of items in the input queue. A Queue cannot be used
# here, because moving an operation from the input queue to the active state
# (_ACTIVE_WORKERS) must be atomic.
_INPUT_QUEUE = collections.deque()
_INPUT_QUEUE_SEM = threading.Semaphore(0)

_ACTIVE_WORKERS = 0
_OUTPUT_QUEUE = collections.deque()

_WORKER_THREADS = []

class ThreadPoolWatcher(tcp.TCPConnection):
	"""
	Helper coroutine that waits for thread pool results to come in, then dispatches them
	to wherever they were called from.
	"""

	def __init__(self):
		"""Constructor."""
		read_socket, self.write_socket = socket.socketpair()

		self.restart = WaitForCallback("ThreadPoolWatcher restart")

		tcp.TCPConnection.__init__(self, None, read_socket)
		self.start()

	def connection_handler(self):
		# Wait until we're needed the first time
		yield self.restart
		self.restart = None

		while True:
			incoming = yield self.recv(128)
			for _index in incoming:
				# Each received byte indicates one result in the output queue.
				result, exc, recipient = _OUTPUT_QUEUE.popleft()
				if exc is not None:
					recipient.throw(exc)
				else:
					recipient(result)

			# If there are any operations still in process, continue the loop.
			with _QUEUE_LOCK:
				ops = len(_INPUT_QUEUE) + _ACTIVE_WORKERS + len(_OUTPUT_QUEUE)

			if not ops:
				self.restart = WaitForCallback("ThreadPoolWatcher restart")
				yield self.restart
				self.restart = None

_WATCHER = ThreadPoolWatcher()

class WorkerThread(threading.Thread):
	def __init__(self):
		"""Constructor."""
		threading.Thread.__init__(self)
		self.setDaemon(True)

	def run(self):
		"""Main loop for worker threads."""
		
		global _ACTIVE_WORKERS
		while True:
			# Retrieve an operation from the queue, run it, and put the result in
			# the output queue
			_INPUT_QUEUE_SEM.acquire()
			with _QUEUE_LOCK:
				operation, args, kwargs, recipient = _INPUT_QUEUE.popleft()
				_ACTIVE_WORKERS += 1

			try:
				result, exc = operation(*args, **kwargs), None
			except:
				result, exc = None, sys.exc_info()

			with _QUEUE_LOCK:
				_OUTPUT_QUEUE.append((result, exc, recipient))
				_ACTIVE_WORKERS -= 1
				_WATCHER.write_socket.send("x")


@returns_waitcondition
def run_in_thread(operation, *args, **kwargs):
	"""Run operation(*args, **kwargs) in a thread, returning the result."""

	# XXX spawn additional threads?
	if not _WORKER_THREADS:
		worker = WorkerThread()
		worker.start()
		_WORKER_THREADS.append(worker)

	recipient = WaitForCallback("run_in_thread %r" % (operation, ))

	with _QUEUE_LOCK:
		_INPUT_QUEUE.append((operation, args, kwargs, recipient))
		_INPUT_QUEUE_SEM.release()

	# Restart the ThreadPoolWatcher if it wasn't listening already
	if _WATCHER.restart:
		_WATCHER.restart()

	return recipient

__all__ = [ "run_in_thread" ]

