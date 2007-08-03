"""
Chiral coroutine system.
"""

# Chiral, copyright (c) 2007 Jacob Potter
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 2.

_CHIRAL_RELOADABLE = True

from decorator import decorator

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
	Decorator to create a new Coroutine with each call to the wrapped function.
	"""

	return Coroutine(gen(*args, **kwargs)) #pylint: disable-msg=W0142


@decorator
def as_coro_waitcondition_decorator(gen, *args, **kwargs):
	"""
	Implementation of as_coro_waitcondition.

	This is a separate function because the decorator module (@decorator) does not provide
	for modification of the decorated function docstring.
	"""

	return WaitForCoroutine(Coroutine(gen(*args, **kwargs))) #pylint: disable-msg=W0142

def as_coro_waitcondition(func):
	"""
	Decorator to create a new Coroutine with each call to the wrapped function,
	and return a WaitCondition waiting for its result.
	"""

	func.__doc__ = trim(func.__doc__) + "\n\nReturns a WaitCondition."

	return as_coro_waitcondition_decorator(func)

@decorator
def returns_waitcondition_decorator(func, *args, **kwargs):
	"""
	Implementation of returns_waitcondition.

	This is a separate function because the decorator module (@decorator) does not provide
	for modification of the decorated function docstring.
	"""

	ret = func(*args, **kwargs) #pylint: disable-msg=W0142

	if not isinstance(ret, WaitCondition):
		ret = WaitForNothing(ret)

	return ret


def returns_waitcondition(func):
	"""
	Mark a function as returning a WaitCondition.

	In optimized mode, this does not replace the function itself or modify its
	return value. In debug mode, however, a wrapper function will be applied
	which checks the return value. If it is not a WaitCondition, it will be
	converted to a WaitForNothing object; this ensures that code does not attempt
	to use the returned value directly.

	Additionally, the docstring will be amended to indicate that its return value
	should be expected to be a WaitCondition.
	"""

	func.__doc__ = trim(func.__doc__) + "\n\nReturns a WaitCondition."

	if __debug__:
		return returns_waitcondition_decorator(func)
	else:
		return func


class WaitCondition(object):
	"""
	Represents a condition which a Coroutine may need to suspend execution for.
	"""

	def __init__(self):
		"""Constructor."""
		raise NotImplementedError("WaitCondition should not be instantiated directly.")

	def bind(self, coro):
		"""
		Bind to a given coroutine.

		If the WaitConditon is already ready, this will return a tuple (value, exc_info)
		of the value or exception that was returned. In this case, the WaitCondition is not
		considered bound; attempting to call unbind() later will fail.

		Otherwise, return None. Once the value is available,
		coro.delayed_value_available(value, exc_info) will be called, and the WaitCondition
		will be considered unbound again.
		"""
		raise NotImplementedError

	def unbind(self):
		"""
		Remove the binding that was last established with self.bind().

		This will raise an AssertionError if the WaitCondition is not currently bound.
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
		"""Bind to a given coroutine. See WaitCondition.bind() for documentation."""

		# WaitForNothing doesn't do anything with coro, although the WaitCondition
		# interface requires it. The above disable-msg line prevents pylint from
		# complaining about that.

		return self.data

	def unbind(self):
		"""Unbind from a given coroutine. See WaitCondition.unbind() for documentation."""

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

		The "description" parameter will be included in repr(); it is not otherwise used.
		"""
		# Don't call WaitCondition.__init__; it raises NotImplementedError to prevent
		# it from being instantiated directly.
		#pylint: disable-msg=W0231

		self.description = description
		self.bound_coro = None

	def bind(self, coro):
		"""Bind to a given coroutine. See WaitCondition.bind() for documentation."""
		self.bound_coro = coro

	def unbind(self):
		"""Unbind from a given coroutine. See WaitCondition.unbind() for documentation."""
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
		"""Bind to a given coroutine. See WaitCondition.bind() for documentation."""
		self.bound_coro = coro

	def unbind(self):
		"""Unbind from a given coroutine. See WaitCondition.unbind() for documentation."""
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
		"""Bind to a given coroutine. See WaitCondition.bind() for documentation."""

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
		"""Unbind from a given coroutine. See WaitCondition.unbind() for documentation."""
		assert self.bound_coro
		self.waiting_coro.remove_completion_callback(self.bound_coro.resume)
		self.bound_coro = None

	def __repr__(self):
		return "<WaitForCoroutine: for %s>" % self.waiting_coro

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


			if isinstance(gen_result, WaitCondition):
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

			# The generator yielded a value that was not a WaitCondition instance.
			# Simply pass that back and run it again.
			next_value = gen_result
			next_exception = None

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
