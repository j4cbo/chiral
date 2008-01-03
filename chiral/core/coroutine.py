"""
Chiral coroutine system.

The chiral.coroutine module differs from most coroutine systems in that it does not provide a
single central scheduler. Each coroutine is an independent `Coroutine` instance, which runs as
long as possible until an external event (a WaitCondition) is required before it can
continue. In Chiral, the Reactor (see `chiral.net.netcore`) acts as a scheduler, but it is not
tied directly into the workings of the Coroutine class. 

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

If a coroutine yields any value other than a ``WaitCondition``, it behaves as though it had yielded
a `WaitForNothing`.
"""

# Chiral, copyright (c) 2007 Jacob Potter
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 2.

_CHIRAL_RELOADABLE = True

from decorator import decorator
from collections import deque

import gc
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


def swallow_kill(_res, exc):
	"""
	Helper function that swallows CoroutineKilledExceptions.

	Usage::

		some_coro.add_completion_callback(coroutine.swallow_kill)

	If some_coro is killed with `Coroutine.kill`, it acts as though it had raised a CoroutineKilledException.
	If that exception is not handled somewhere, the affected coroutine will be considered an orphan,
	and its untimely failure will be logged. Adding ``swallow_kill`` will suppress the warning.
	"""

	if exc is not None and exc[0] is CoroutineKilledException:
		return (None, None)


class CoroutineKilledException(Exception):
	"""Indicates that the raising coroutine was terminated by calling kill() on it."""
	pass


class WaitCondition(object):
	"""
	Represents a condition for which a Coroutine may need to suspend execution.
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
		not considered bound; attempting to call `unbind` later will fail.

		Otherwise, return None. Once the value is available, `Coroutine.resume` will be
		called, and the WaitCondition will be considered unbound again.

		This should not generally be called except by `Coroutine.resume`.
		"""
		raise NotImplementedError

	def unbind(self, coro):
		"""
		Remove the binding that was established with self.bind(coro).

		Raises an AssertionError if the WaitCondition is not currently bound.

		This should not generally be called except by `Coroutine.kill`.
		"""
		raise NotImplementedError

class WaitForNothing(WaitCondition):
	"""
	A "false" WaitCondition, which will cause execution to resume immediately.

	This class is generally used only to ensure type-safety. Yielding anything that is not a
	WaitCondition from a coroutine causes that object to be passed back in as the result of
	the yield expression; ``yield value`` is generally the same as ``yield WaitForNothing(value)``.
	However, a WaitForNothing may carry an exception instead of a value; yielding it will
	cause that exception to be raised.

	The `returns_waitcondition` decorator wraps all non-WaitConditions that a function returns
	in WaitForNothing objects, unless Python is running in optimized mode. This helps ensure
	that one does not accidentally use its return values directly without yielding them from
	inside a Coroutine.
	"""

	def __init__(self, value=None, exc=None):
		"""
		Constructor.

		The bound Coroutine will be given 'value' as the result of the WaitCondition.
		"""
		# Don't call WaitCondition.__init__; it raises NotImplementedError to prevent
		# it from being instantiated directly.
		#pylint: disable-msg=W0231
		self.data = (value, exc)

	def bind(self, _coro):
		"""Bind to a given coroutine."""
		return self.data

	def unbind(self, _coro):
		"""Unbind from a given coroutine."""
		raise AssertionError("WaitForNothing instances cannot be bound.")

	def __repr__(self):
		return "<WaitForNothing %r>" % (self.data, )

class WaitForCallback(WaitCondition):
	"""
	A callable WaitCondition which resumes the bound coroutine once it is called.

	WaitForCallback instances expect a single argument, which will be passed back to their
	bound coroutine as the result. For a version which takes positional arguments and returns
	a tuple, use `WaitForCallbackArgs`.
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
		"""Bind to a given coroutine."""
		self.bound_coro = coro

	def unbind(self, coro):
		"""Unbind from a given coroutine."""
		assert self.bound_coro is coro
		self.bound_coro = None

	def __call__(self, value=None):
		"""Cause `value` to be the return value of the WaitCondition."""
		assert self.bound_coro
		self.bound_coro.resume(value)
		self.bound_coro = None

	def throw(self, exc=None):
		"""Raise an Exception in the bound coroutine.

		If `exc` is None, ``sys.exc_info()`` will be raised instead.
		"""

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

		:Parameters:
			- `description`: The purpose of the callback, to be included in ``repr()``.
		"""
		# Don't call WaitCondition.__init__; it raises NotImplementedError to prevent
		# it from being instantiated directly.
		#pylint: disable-msg=W0231

		self.description = description
		self.bound_coro = None

	def bind(self, coro):
		"""Bind to a given coroutine."""
		self.bound_coro = coro

	def unbind(self, coro):
		"""Unbind from a given coroutine."""
		assert self.bound_coro is coro
		self.bound_coro = None

	def __call__(self, *args):
		"""Cause `args` to be the return value of the WaitCondition."""
		assert self.bound_coro
		self.bound_coro.resume(args)
		self.bound_coro = None

	def throw(self, exc=None):
		"""Raise an Exception in the bound coroutine.

		If `exc` is None, ``sys.exc_info()`` will be raised instead.
		"""

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


class _CoroutineMutexManager(object):
	"""Context manager for `CoroutineMutex` objects."""

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
	A Coroutint-based mutex object.

	One CoroutineMutex represents one controlled-access resource. For example, a TCPConnection
	giving access to a server may be protected by a CoroutineMutex to ensure that multiple
	transactions are not started at once.

	CoroutineMutex objects have one important method, `acquire()`. This returns a `WaitCondition`,
	which will resume the coroutine once the mutex is available. The WaitCondition will result
	in a context manager, as specified in PEP 342, which should immediately be passed to a ``with``
	statement::

		with (yield connection.mutex.acquire()):
			connection.sendall("command\\r\\n")
			result = connection.read_line()

	Note that in Python 2.5, the ``with`` statement requres ``from future import with_statement``.

	Alternately, the context manager that results from yielding `acquire` may be ignored, and
	`release` called to release the mutex. However, doing so reduces exception safety compared
	to the ``with`` statement method (an unhandled exception could cause the mutex to never be
	released), so it is not reccomended.
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
		Attempt to acquire the mutex.

		Returns a WaitCondition which resumes the coroutine once the mutex is claimed. The
		result of the WaitCondition is a context manager which should immediately be invoked.
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
		Force the mutex to be released.

		Use with caution; the context manager produced by `acquire` is safer.
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



class CoroutineRestart(Exception):
	"""
	Raise from within a generator to indicate that the coroutine should be restarted with a new generator.

	Where "raise StopIteration(value)" is like a return statement, CoroutineRestart
	is analogous to an optimized tail-call or tail-recursion. A generator, or a Coroutine
	that has not been `start`-ed yet, should be passed to __init__.

	For example, these two functions act almost equivalently::

		def handle_connection(self):
			while True:

				[ code to handle one request ]

				if not self.more_requests:
					break

	And::

		def handle_connection(self):

			[ code to handle one request ]

			if self.more_requests:
				raise CoroutineRestart(self.handle_connection())

	There are a few differences; for example, code reloads will not take effect during the
	lifetime of the former handle_connection(), but they will for the latter. One could also
	put the request handling code in its own coroutine; choose which method to use based on
	semantic correctness and readability.
	"""

	def __init__(self, gen):
		"""Constructor.

		:Parameters:
			- `gen`: The new generator or unstarted `Coroutine` to jump to.
		"""
		Exception.__init__(self)
		self.gen = gen


# Store a global list of all current coroutines. The __reload_update__
# magic prevents xreload.xreload() from wiping out the list.
_COROUTINES = weakref.WeakValueDictionary()
setattr(_COROUTINES, "__reload_update__", lambda oldobj: oldobj)


class Coroutine(WaitCondition):
	"""
	A coroutine.

	Any coroutine instance is, at any given time, in one of the following states:

	STATE_STOPPED
		Created and ready to run, but has not been started.

	STATE_RUNNING
		Currently executing code.

	STATE_SUSPENDED
		Waiting on a `WaitCondition`, which will be assigned to ``self.wait_condition``.

	STATE_COMPLETED
		Completed successfully. ``self.result`` will be a tuple ``(result, None)``.

	STATE_FAILED
		Failed with an exception. ``self.result`` will be a tuple ``(None, (type, value, traceback))``.

	A coroutine is itself a `WaitCondition`, which other coroutines can ``yield`` to wait
	for completion. Coroutine objects also implement the context manager protocol from PEP 342;
	one can use the ``with`` statement as such::

		with coro:
			do_stuff()

	This is equivalent to::

		if coro.state == coro.STATE_STOPPED:
			coro.start()

		try:
			do_stuff()
		finally:
			coro.kill()
	"""

	STATE_STOPPED, STATE_RUNNING, STATE_SUSPENDED, STATE_COMPLETED, STATE_FAILED = range(5)

	__state_names = "stopped", "running", "suspended", "completed", "failed"

	def __init__(self, generator, default_callback=None, autostart=False, is_watched=False):
		"""
		Constructor.

		:Parameters:
			- `generator`: The function or method containing the body of the coroutine's code.
			- `default_callback`: An initial completion callback; see `add_completion_callback`.
			- `autostart`: Set to ``True`` to `start` the coroutine immediately.
		"""

		# Don't call WaitCondition.__init__; it raises NotImplementedError to prevent
		# it from being instantiated directly.
		#pylint: disable-msg=W0231

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

	def _terminate(self, result, failure):
		"""
		Clean up and call completion callbacks after the coroutine has terminated.
		"""

		self.result = (result, failure)

		# Remove reference for GC
		self.gen = None

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

		callback = None
		del self.completion_callbacks[:]

		if self.result[1] is not None and not self.is_watched:
			# The exception was not handled, so log a warning.
			exc_type, exc_obj, exc_traceback = self.result[1]
			warnings.warn("Orphan coro %s failed: %s" % (
				self, ''.join(traceback.format_exception(
					exc_type, exc_obj, exc_traceback
				))
			))


	def resume(self, next_value, next_exception=None):
		"""
		Run the coroutine as long as possible.

		This may only be called when the coroutine is in ``STATE_SUSPENDED``.
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
					del exc_type, exc_value, exc_tb
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

				self._terminate(result, None)
				break

			except CoroutineRestart, exc:
				# Restart with a new Coroutine or generator

				if isinstance(exc.gen, Coroutine):
					assert exc.gen.state == self.STATE_STOPPED
					self.gen = exc.gen.gen
					self.completion_callbacks.extend(exc.gen.completion_callbacks)
				else:
					self.gen = exc.gen

				next_value, next_exception = None, None
				continue

			except Exception: #pylint: disable-msg=W0703
				# An (unexpected) exception was thrown; terminate the coroutine.
				self.state = self.STATE_FAILED
				self._terminate(None, sys.exc_info())
				break

			if gen_result is None:
				# Optimize handling None
				next_value, next_exception = None, None
				continue

			if not isinstance(gen_result, WaitCondition):
				# The generator yielded a value that was not a WaitCondition
				# instance. Treat it as another coroutine.
				gen_result = Coroutine(gen_result, autostart=True)

			bind_result = gen_result.bind(self)

			if bind_result is not None:	

				# Delete the reference to gen_result here, to ensure prompt GC
				del gen_result

				# The WaitCondition was already ready; use whatever value
				# or exception it gave, and loop around.
				next_value, next_exception = bind_result
				continue
			else:
				# There's nothing else we can do now.
				self.state = self.STATE_SUSPENDED
				self.wait_condition = gen_result
				break

		del self

	def start(self):
		"""Begin running the coroutine.

		This may only be called if the coroutine is not already running (STATE_STOPPED).
		"""

		assert self.state == self.STATE_STOPPED
		self.state = self.STATE_SUSPENDED
		self.resume(None)

	def bind(self, coro):
		"""Bind to another coroutine (`WaitCondition`).

		This should not be called from outside the Coroutine class.
		"""

		self.is_watched = True
		if self.state == self.STATE_STOPPED:
			self.start()

		if self.state in (self.STATE_COMPLETED, self.STATE_FAILED):
			# If we've already returned, just return our state now.
			return self.result
		else:
			# We haven't finished yet, so resume the other coroutine when we do.
			self.add_completion_callback(coro.resume)
			return None
		
	def unbind(self, coro):
		"""Unbind from another coroutine (`WaitCondition`).

		This should not be called from outside the Coroutine class.
		"""
		self.remove_completion_callback(coro.resume)

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
		`remove_completion_callback`.
		"""

		assert self.state not in (self.STATE_COMPLETED, self.STATE_FAILED)
		self.completion_callbacks.append(callback)

	def remove_completion_callback(self, callback):
		"""
		Remove a completion callback that was set with `add_completion_callback`.
		"""

		self.completion_callbacks.remove(callback)

	def kill(self):
		"""
		Forcefully stop running this coroutine.

		If the coroutine is not in `STATE_RUNNING` or `STATE_SUSPENDED`, this does nothing.
		A suspended coroutine will have its current wait condition unbound; its completion
		callback will then be called with a CoroutineKilledException.
		"""

		if self.state == self.STATE_SUSPENDED:
			self.wait_condition.unbind(self)
			self.wait_condition = None
			self.state = self.STATE_FAILED

			# XXX: Dirty hack to get a traceback to the current point. 
			try:
				raise CoroutineKilledException()
			except CoroutineKilledException:
				pass

			self._terminate(None, sys.exc_info())

		elif self.state == self.STATE_RUNNING:
			# XXX kill self while running?
			raise NotImplementedError

	def __enter__(self):
		"""Context manager entrance function: start this coroutine."""
		self.start()
		return self

	def __exit__(self, _exc_type, _exc_value, _exc_info):
		"""Context manager exit function: kill this coroutine no matter what."""
		self.add_completion_callback(swallow_kill)
		self.kill()

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

	def _chiral_introspect(self):
		"""Returns access information for _chiral_introspection for this object."""

		return "coroutine", id(self)

	def introspection_info(self):
		"""Returns the introspection information for this coroutine.

		This should only be called by _chiral_introspection.coroutine().
		"""

		return (
			self,
			( "Attributes:", self.__dict__ ),
			( "Referrers: ", [ repr(x) for x in gc.get_referrers(self) ] ),
			( "dir():", dir(self) ),
		)

class _chiral_introspection(object):
	"""Module-level introspection routines."""
	def main(self):
		"""main info: return a list of all current coroutines."""
		coro_list = _COROUTINES.values()
		coro_list.sort(key = id)
		return coro_list

	def coroutine(self, coro_id):
		"""Look up the coroutine with the given id and return its introspection_info()."""
		try:
			coro = _COROUTINES[int(coro_id)]
		except KeyError:
			return None

		return coro.introspection_info()

__all__ = [
	"as_coro",
	"returns_waitcondition",
	"swallow_kill",
	"CoroutineKilledException",
	"WaitCondition",
	"WaitForNothing",
	"WaitForCallback",
	"WaitForCallbackArgs",
	"CoroutineMutex",
	"CoroutineRestart",
	"Coroutine"
]
