"""
Chiral coroutine system.

The chiral.coroutine module differs from most coroutine systems in that there is no single
scheduler. Each coroutine is an independent `Coroutine` instance, which runs as long as possible
until an external event (generally a callback) is required before it can continue.

A coroutine can be defined as a standalone generator function, or by making a subclass of
`Coroutine` with a generator function as a member. In either case, the generator is created by
invoking the function, then passed to `Coroutine.__init__`.

Like regular functions, coroutines have a final return value. The coroutine may be ended with a
``return`` statement, which causes its return value to be None, but Python does not allow
``return`` with an argument inside a generator. Instead, to specify its return value, the
coroutine should raise a `StopIteration` with its return value as an argument::

	raise StopIteration(value)

When a coroutine needs to wait for a value, be it from another coroutine or elsewhere, it does
so by ``yield``-ing a `WaitCondition` object. WaitConditions have a return value, which is passed
as the result of the yield expression. The core coroutine module defines four WaitCondition
classes; they should be sufficient for all uses. See the `chiral.net.tcp` module for examples
of code that works with WaitConditions.

If a coroutine yields any value other than a WaitCondition, it behaves as though it had yielded
a `WaitForNothing`.
"""

# Chiral, copyright (c) 2007 Jacob Potter
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 2.

_CHIRAL_RELOADABLE = True

from decorator import decorator
from collections import deque

import sys
import traceback
import warnings
import weakref

def trim(docstring):
	"""Docstring indentation removal, from PEP 257"""
	if not docstring:
		return ''

	# Convert tabs to spaces (following the normal Python rules)
	# and split into a list of lines:
	lines = docstring.expandtabs().splitlines()
	# Determine minimum indentation (first line doesn't count):
	indent = sys.maxint
	for line in lines[1:]:
		stripped = line.lstrip()
		if stripped:
			indent = min(indent, len(line) - len(stripped))
	# Remove indentation (first line is special):
	trimmed = [lines[0].strip()]
	if indent < sys.maxint:
		for line in lines[1:]:
			trimmed.append(line[indent:].rstrip())
	# Strip off trailing and leading blank lines:
	while trimmed and not trimmed[-1]:
		trimmed.pop()
	while trimmed and not trimmed[0]:
		trimmed.pop(0)
	# Return a single string:
	return '\n'.join(trimmed)

@decorator
def as_coro(gen, *args, **kwargs):
	"""
	Create a new Coroutine with each call to the wrapped function.
	"""

	return Coroutine(gen(*args, **kwargs)) #pylint: disable-msg=W0142


@decorator
def _as_coro_waitcondition_dec(gen, *args, **kwargs):
	"""Implementation of as_coro_waitcondition."""
	# This is a separate function because the decorator module (@decorator) does not provide
	# for modification of the decorated function docstring.
	return WaitForCoroutine(Coroutine(gen(*args, **kwargs), autostart = False)) #pylint: disable-msg=W0142

def as_coro_waitcondition(func):
	"""
	Create a new Coroutine with each call to the wrapped function, and return a WaitCondition for its result.

	The docstring will also be amended to indicate that it returns a WaitCondition.
	"""

	func.__doc__ = trim(func.__doc__) + "\n\nReturns a WaitCondition."

	return _as_coro_waitcondition_dec(func)

@decorator
def _returns_waitcondition_dec(func, *args, **kwargs):
	"""Implementation of returns_waitcondition."""
	# This is a separate function because the decorator module (@decorator) does not provide
	# for modification of the decorated function docstring.

	ret = func(*args, **kwargs) #pylint: disable-msg=W0142

	if ret is not None and not isinstance(ret, WaitCondition):
		raise TypeError("%s should return a WaitCondition instance; got %s" % (func, ret))

	return ret


def returns_waitcondition(func):
	"""
	Mark a function as returning a WaitCondition.

	When not running in optimized mode, the return value is checked, and a TypeError is
	thrown if it is not a WaitCondition. In optimized mode, the function is not modified.

	Additionally, the function docstring will be amended to indicate that its return value
	should be expected to be a WaitCondition.
	"""

	func.__doc__ = trim(func.__doc__) + "\n\nReturns a WaitCondition."

	if __debug__:
		return _returns_waitcondition_dec(func)
	else:
		return func


class WaitCondition(object):
	"""
	Represents a condition which a Coroutine may need to suspend execution for.
	"""

	def __init__(self):
		"""Constructor.

		WaitCondition is used only as a base class; it should not be instantiated directly.
		"""
		raise NotImplementedError("WaitCondition should not be instantiated directly.")

	def bind(self, coro):
		"""
		Bind to a given coroutine.

		If the WaitConditon is already ready, this will return a tuple (value, exc_info)
		of the value or exception that was returned. In this case, the WaitCondition is
		not considered bound; attempting to call unbind() later will fail.

		Otherwise, return None. Once the value is available,
		coro.delayed_value_available(value, exc_info) will be called, and the
		WaitCondition will be considered unbound again.

		This should not generally be called except by `Coroutine.resume()`.
		"""
		raise NotImplementedError

	def unbind(self):
		"""
		Remove the binding that was last established with self.bind().

		Raises an AssertionError if the WaitCondition is not currently bound.

		This should not generally be called except by `Coroutine.resume()`.
		"""
		raise NotImplementedError

class WaitForNothing(WaitCondition):
	"""
	A "false" WaitCondition, which will cause execution to resume immediately.

	This class is generally used only to ensure type-safety. Yielding anything that is not a
	WaitCondition from a coroutine causes that object to be passed back in as the result of
	the yield expression; "yield value" is generally the same as "yield WaitForNothing(value)".
	However, a WaitForNothing may carry an exception instead of a value; yielding it will
	cause that exception to be raised.

	The "returns_waitcondition" decorator wraps all non-WaitConditions that a function returns
	in WaitForNothing objects, unless Python is running in opitimized mode. This helps ensure
	that one does not accidentally use its return values directly without yielding them from
	inside a Coroutine.
	"""

	__slots__ = "data", 

	def __init__(self, value=None, exc=None):
		"""
		Constructor.

		The bound Coroutine will be given 'value' as the result of the WaitCondition.
		"""
		# Don't call WaitCondition.__init__; it raises NotImplementedError to prevent
		# it from being instantiated directly.
		#pylint: disable-msg=W0231
		self.data = (value, exc)

	def bind(self, coro): #pylint: disable-msg=W0613
		"""Bind to a given coroutine; see `WaitCondition.bind()`."""

		# WaitForNothing doesn't do anything with coro, although the WaitCondition
		# interface requires it. The above disable-msg line prevents pylint from
		# complaining about that.

		return self.data

	def unbind(self):
		"""Unbind from a given coroutine; see `WaitCondition.unbind()`."""

		raise AssertionError("WaitForNothing instances cannot be bound.")

	def __repr__(self):
		return "<WaitForNothing %r>" % (self.data, )

class WaitForCallback(WaitCondition):
	"""
	A callable WaitCondition which resumes the bound WaitCondition once it is called.

	WaitForCallback instances expect a single argument, which will be passed back to their
	bound coroutine as the result. For a version which takes positional arguments and returns
	a tuple, use WaitForCallbackArgs.
	"""

	def __init__(self, description=None):
		"""
		Constructor.

		:Parameters:
		  - `description`: The purpose of the callback, to be included in ``repr()``.

		"""
		# Don't call WaitCondition.__init__; it raises NotImplementedError to prevent
		# it from being instantiated directly.
		#pylint: disable-msg=W0231

		self.description = description
		self.bound_coro = None

	def bind(self, coro):
		"""Bind to a given coroutine; see `WaitCondition.bind()`."""
		self.bound_coro = coro

	def unbind(self):
		"""Unbind from a given coroutine; see `WaitCondition.unbind()`."""
		assert self.bound_coro
		self.bound_coro = None

	def __call__(self, value=None):
		"""Cause value to be the return value of the WaitCondition."""
		assert self.bound_coro
		self.bound_coro.resume(value)
		self.bound_coro = None

	def throw(self, exc=None):
		"""Cause the given exception (or sys.exc_info()) to be raised in the bound coroutine."""
		if exc is None:
			exc = sys.exc_info()
		elif isinstance(exc, Exception):
			exc = (type(exc), exc, None)

		assert self.bound_coro
		self.bound_coro.resume(None, exc)
		self.bound_coro = None

	def __repr__(self):
		if self.description:
			return "<WaitForCallback %s>" % (self.description, )
		else:
			return "<WaitForCallback>"


class WaitForCallbackArgs(WaitCondition):
	"""
	A callable WaitCondition which resumes the bound coroutine once it is called.

	The positional arguments given when the instance is called will be returned as a tuple
	in the waiting coroutine.
	"""

	def __init__(self, description=None):
		"""
		Constructor.

		The "description" parameter will be included in repr(); it is not otherwise used.
		"""
		# Don't call WaitCondition.__init__; it raises NotImplementedError to prevent
		# it from being instantiated directly.
		#pylint: disable-msg=W0231

		self.description = description
		self.bound_coro = None

	def bind(self, coro):
		"""Bind to a given coroutine; see `WaitCondition.bind()`."""
		self.bound_coro = coro

	def unbind(self):
		"""Unbind from a given coroutine; see `WaitCondition.unbind()`."""
		assert self.bound_coro
		self.bound_coro = None

	def __call__(self, *args):
		"""Cause args to be the return value of the WaitCondition."""
		assert self.bound_coro
		self.bound_coro.resume(args)
		self.bound_coro = None

	def __repr__(self):
		if self.description:
			return "<WaitForCallback %s>" % (self.description, )
		else:
			return "<WaitForCallback>"


class WaitForCoroutine(WaitCondition):
	"""
	A WaitCondition that results in the value returned by another coroutine.
	"""

	def __init__(self, waiting_coro):
		"""Constructor. Wait for waiting_coro to complete."""
		# Don't call WaitCondition.__init__; it raises NotImplementedError to prevent
		# it from being instantiated directly.
		#pylint: disable-msg=W0231
		self.waiting_coro = waiting_coro
		self.bound_coro = None

	def bind(self, coro):
		"""Bind to a given coroutine; see `WaitCondition.bind()`."""

		self.waiting_coro.is_watched = True
		self.waiting_coro.start(force = False)

		if self.waiting_coro.state in (Coroutine.STATE_COMPLETED, Coroutine.STATE_FAILED):
			# If waiting_coro has already returned, just return its state now.
			return self.waiting_coro.result
		else:
			# Hasn't started yet se:
			self.bound_coro = coro
			self.waiting_coro.add_completion_callback(coro.resume)
			return None
		
	def unbind(self):
		"""Unbind from a given coroutine; see `WaitCondition.unbind()`."""
		assert self.bound_coro
		self.waiting_coro.remove_completion_callback(self.bound_coro.resume)
		self.bound_coro = None

	def __repr__(self):
		return "<WaitForCoroutine: for %s>" % self.waiting_coro

class _CoroutineMutexManager(object):
	"""Context manager for CoroutineMutex objects."""

	# Context managers are opaque objects; they should not have any public methods.
	#pylint: disable-msg=R0903

	def __init__(self, mutex):
		"""Constructor."""
		self.mutex = mutex

	def __enter__(self):
		"""Called when the context manager is passed to the with statement."""
		assert self.mutex.current_owner is self

	def __exit__(self, _exc_type, _exc_value, _exc_tb):
		"""Called when the with statement completes."""
		self.mutex.current_owner = None
		if len(self.mutex.queue) > 0:
			# Start the next item in the queue: create a ContextManager and call it.
			next_manager = _CoroutineMutexManager(self.mutex)
			self.mutex.current_owner = next_manager
			self.mutex.queue.popleft()(next_manager)

class CoroutineMutex(object):
	"""
	An object that regulates access to a resource.

	One CoroutineMutex represents one controlled-access resource. For example, a TCPConnection
	giving access to a server may be protected by a WaitForMutex to ensure that multiple transactions
	are not opened at once.

	CoroutineMutex objects have one important method, `acquire()`. This returns a WaitCondition, which
	will resule the coroutine once the mutex is available. The WaitCondition will result in a context
	manager, as specified in PEP 342. It should immediately be passed to a ``with`` statement, like so::

		with (yield connection.mutex.acquire()):
			connection.sendall("command\r\n")
			result = connection.read_line()

	Note that in Python 2.5, the ``with`` statement requres ``from future import with_statement``.

	Alternately, the context manager that results from yielding `acquire()` may be ignored, and `release()`
	called to release the mutex. However, doing so reduces exception safety compared to the with statement
	options (an unhandled exception could cause the mutex to never be released), so it is not reccomended.
	"""

	def __init__(self, description=None):
		"""
		Constructor.

		The "description" parameter will be included in repr(); it is not otherwise used.
		"""
		self.description = description
		self.current_owner = None
		self.queue = deque()

	@returns_waitcondition
	def acquire(self):
		"""
		Attempt to acquire the mutex. Yields a WaitCondition which returns once the mutex is claimed.
		"""

		if self.current_owner is not None:
			manager = _CoroutineMutexManager(self)
			self.current_owner = manager
			return WaitForNothing(manager)
		else:
			callback = WaitForCallback(description = repr(self))
			self.queue.append(callback)
			return callback

	def release(self):
		"""
		Force the mutex to be released. Use with caution; the context manager is preferable.
		"""
		self.current_owner = None
		if len(self.queue) > 0:
			# Start the next item in the queue: create a ContextManager and call it.
			next_manager = _CoroutineMutexManager(self)
			self.current_owner = next_manager
			self.queue.popleft()(next_manager)
	
	def __repr__(self):
		if self.description:
			return "<WaitForCallback %s>" % (self.description, )
		else:
			return "<WaitForCallback>"


# Store a global list of all current coroutines. The try/except block
# ensures that even if this module is reloaded, only one list of
# coroutines will ever exist.
try:
	_COROUTINES # pylint: disable-msg=W0104
except NameError:
	_COROUTINES = weakref.WeakValueDictionary()

class Coroutine(object):
	"""
	A coroutine.
	"""

	STATE_STOPPED, STATE_RUNNING, STATE_SUSPENDED, STATE_COMPLETED, STATE_FAILED = range(5)

	__state_names = "stopped", "running", "suspended", "completed", "failed"

	def __init__(self, generator, default_callback=None, autostart=True, is_watched=False):
		"""
		Create a coroutine instance. "generator" is the function or method that contains
		the body of the coroutine code.
		"""

		self.state = self.STATE_STOPPED
		self.result = None

		self.completion_callbacks = [ default_callback ] if default_callback else [ ]

		self.gen = generator
		self._gen_name = self.gen.gi_frame.f_code.co_name

		self.wait_condition = None

		self.is_watched = is_watched

		_COROUTINES[id(self)] = self

		if autostart:
			self.start()

	def resume(self, next_value, next_exception=None):
		"""
		Run the coroutine as long as possible.
		"""

		assert self.state == self.STATE_SUSPENDED

		self.state = self.STATE_RUNNING
		self.wait_condition = None

		while True:
			try:
				# Pass whatever value is available into the exception
				if next_exception:
					exc_type, exc_value, exc_tb = next_exception
					gen_result = self.gen.throw(exc_type, exc_value, exc_tb)
				elif next_value is not None:
					gen_result = self.gen.send(next_value)
				else:
					gen_result = self.gen.next()

			except StopIteration, exc:
				# The coroutine completed successfully
				self.state = self.STATE_COMPLETED

				if exc.args:
					result = exc.args[0]
				else:
					result = None

				self.result = (result, None)

			except Exception: #pylint: disable-msg=W0703
				# An (unexpected) exception was thrown; terminate the coroutine.
				self.state = self.STATE_FAILED
				self.result = (None, sys.exc_info())

			# If either of the two exception handlers fired, handle the completion callbacks.
			if self.result is not None:
				for callback in self.completion_callbacks:
					try:
						callback_result = callback(self.result[0], self.result[1])

						# Completion callbacks may modify the result of the coroutine
						# by returning a tuple (to swallow exceptions, for example, or
						# pass the result through some sort of filter).

						if callback_result is not None:
							self.result = callback_result

					except Exception: #pylint: disable-msg=W0703
						# If the completion callback itself raises an Exception, make
						# it as if the coroutine failed with that exception.
						self.result = (None, sys.exc_info())

					if self.result[1] is None:
						self.state = self.STATE_COMPLETED
					else:
						self.state = self.STATE_FAILED

				if self.result[1] is not None and not self.is_watched:
					# The exception was not handled, so log a warning.
					exc_type, exc_obj, exc_traceback = self.result[1]
					warnings.warn("Orphan coro %s failed: %s" % (
						self, ''.join(traceback.format_exception(
							exc_type, exc_obj, exc_traceback
						))
					))

				break

			if gen_result is None:
				# Optimize handling None
				next_value, next_exception = None, None
				continue

			if not isinstance(gen_result, WaitCondition):
				# The generator yielded a value that was not a WaitCondition
				# instance. Treat it as another coroutine.
				gen_result = WaitForCoroutine(Coroutine(gen_result))

			bind_result = gen_result.bind(self)

			if bind_result is not None:	
				# The WaitCondition was already ready; use whatever value
				# or exception it gave, and loop around.
				next_value, next_exception = bind_result
				continue
			else:
				# There's nothing else we can do now.
				self.state = self.STATE_SUSPENDED
				self.wait_condition = gen_result
				break


	def start(self, force=True):
		"""Begin running the coroutine.

		@param force: If force is True, the default, then the tasklet must have
		been initialized with autostart=False and not have been started yet.
		If force is False, this method will do nothing if the task is already running.
		"""

		if not force and self.state != self.STATE_STOPPED:
			return

		assert self.state == self.STATE_STOPPED
		self.state = self.STATE_SUSPENDED
		self.resume(None)

	def add_completion_callback(self, callback):
		"""
		Set callback as the completion callback for this coroutine.

		When the coroutine returns, callback will be called with two parameters: the
		value that the coroutine returned, and the exception that was raised (if any) as
		a (type, value, traceback) tuple. If an exception was raised, the value will be
		None; if no exception was raised, the second parameter will be None rather than
		a tuple.

		The callback may return a (value, exception) tuple, where value and exception are
		as above, to modify the result of the coroutine.

		Once a completion callback has been set, it may be removed with
		remove_completion_callback.
		"""

		assert self.state not in (self.STATE_COMPLETED, self.STATE_FAILED)
		self.completion_callbacks.append(callback)

	def remove_completion_callback(self, callback):
		"""
		Remove a completion callback after it has been set with set_completion_callback().
		"""

		self.completion_callbacks.remove(callback)

	def __repr__(self):

		if self.state in (Coroutine.STATE_COMPLETED, Coroutine.STATE_FAILED):
			failure_info = " with %r / %r" % self.result
		else:
			failure_info = ""

		name = self.__class__.__name__
		if name == "Coroutine":
			name = "\"%s\"" % (self._gen_name, )

		return "<Coroutine %s: %s, %s%s%s>" % (
			id(self),
			name,
			self.__state_names[self.state],
			failure_info,
			(", waiting on %s" % self.wait_condition) if self.wait_condition else ""
		)
