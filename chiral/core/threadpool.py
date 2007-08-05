"""Thread pool."""

from __future__ import with_statement

import Queue
import collections
import threading
import socket
import sys

from chiral.core.coroutine import returns_waitcondition, WaitForCallback
from chiral.net import tcp

_QUEUE_LOCK = threading.Lock()

_INPUT_QUEUE = Queue.Queue()
_ACTIVE_WORKERS = 0
_OUTPUT_QUEUE = collections.deque()

class ThreadPoolWatcher(tcp.TCPConnection):
	"""
	Helper coroutine that waits for thread pool results to come in, then dispatches them
	to wherever they were called from.
	"""

	def __init__(self):
		"""Constructor."""
		read_socket, self.write_socket = socket.socketpair()

		self.restart = None

		tcp.TCPConnection.__init__(self, read_socket, None)
		self.start()

	def connection_handler(self):
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

	def run(self):
		"""Main loop for worker threads."""
		
		global _ACTIVE_WORKERS
		while True:
			# Retrieve an operation from the queue, run it, and put the result in
			# the output queue
			with _QUEUE_LOCK:
				operation, args, kwargs, recipient = _INPUT_QUEUE.get()
				_ACTIVE_WORKERS += 1

			try:
				result, exc = operation(*args, **kwargs), None
			except:
				result, exc = None, sys.exc_info()

			with _QUEUE_LOCK:
				_OUTPUT_QUEUE.append((result, exc, recipient))
				_ACTIVE_WORKERS -= 1
				_WATCHER.write_socket.write("x")

@returns_waitcondition
def run_in_thread(operation, *args, **kwargs):
	"""Run operation(*args, **kwargs) in a thread; return the result."""

	# XXX decide whether or not we should spawn a new thread here

	recipient = WaitForCallback("run_in_thread %r" % (operation, ))

	with _QUEUE_LOCK:
		_INPUT_QUEUE.put((operation, args, kwargs, recipient))

	# Restart the ThreadPoolWatcher if it wasn't listening already
	if _WATCHER.restart:
		_WATCHER.restart()

	return recipient

__all__ = [ "run_in_thread" ]
