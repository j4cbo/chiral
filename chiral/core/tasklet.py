# Chiral Framework
#
# Copyright (c) 2007 Jacob Potter
#
# This library is free software; you can redistribute it and/or
# modify it under the terms of the GNU Lesser General Public
# License as published by the Free Software Foundation; either
# version 2.1 of the License, or (at your option) any later version.
#
# This library is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public
# License along with this library; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307
# USA
#
# This file is based (heavily) on tasklets.py from Kiwi, with the following
# copyright message:
# Kiwi: a Framework and Enhanced Widgets for Python
# Copyright (C) 2005 Gustavo J. A. M. Carneiro
#
# Author(s): Gustavo J. A. M. Carneiro <gjc@inescporto.pt>
#

import types
import warnings
import weakref

import sys
import traceback

from chiral.core import stats

if sys.version_info[:2] < (2, 5):
	raise RuntimeError("chiral.core.callbacks requires Python 2.5 for generator expressions.")

class task(object):
	'''
	A decorator that modifies a tasklet function to avoid the need
	to call C{tasklet.Tasklet(func())}.
	'''

	def __init__(self, func):
		self._func = func
		self.__name__ = func.__name__
		self.__doc__ = func.__doc__

	def __call__(self, *args, **kwargs):
		return Tasklet(self._func(*args, **kwargs))


class WaitCondition(object):
	'''
	Base class for all wait-able condition objects.

	WaitConditions are yielded from within the body of a tasklet, to specify what event(s) it
	should wait for in order to receive control again.
	'''

	def __init__(self):
		'''Abstract base class; do not call directly.'''

	def arm(self, tasklet):
		'''Prepare the wait condition to receive events.

		If the event the WaitCondition is waiting for has already occurred, arm()
		should return a 2-tuple (event, exception) of its result; if an exception
		was raised, the event should be None, otherwise the exception should be None.

		Otherwise, arm() should return None, and once the event it is waiting for
		has happened, it should call the
		L{wait_condition_fired<Tasklet.wait_condition_fired>} of the tasklet, with
		the WaitCondition object (i.e. self) as argument. 

		@parameter tasklet: the tasklet instance the wait condition is
		  to be associated with.

		@attention: this method should not normally be called directly
		  by the programmer.
		'''
		raise NotImplementedError

	def disarm(self):
		'''Stop the wait condition from receiving events.

		@attention: this method should not normally be called by the
		programmer.'''
		raise NotImplementedError



class WaitForCallback(WaitCondition):
	'''
	An object that waits until it is called.

	Returns the value that it is called with, or None.
	'''

	__slots__ = '_callback'

	def __init__(self):
		'''
		Creates a wait condition that is actually a callable object, and waits for a call to be made on it.
		If a parameter is passed to the callable, it will be returned to the tasklet.
		'''
		WaitCondition.__init__(self)
		self._callback = None

	def arm(self, tasklet):
		'''Overrides WaitCondition.arm'''
		self._callback = tasklet.wait_condition_fired

	def disarm(self):
		'''Overrides WaitCondition.disarm'''
		self._callback = None

	def __call__(self, return_value=None):

		retval = self._callback(self, return_value)
		return retval

	def throw(self, exc=None):
		if not exc:
			exc = sys.exc_info()
		elif isinstance(exc, Exception):
			exc = (type(exc), exc, None)

		retval = self._callback(self, None, exc)

		return retval

	def __repr__(self):
		return "<WaitForCallback>"


class WaitForNothing(WaitCondition):
	'''
	An object that causes the tasklet yielding it to resume immediately with the given value.
	'''

	__slots__ = 'value'

	def __init__(self, value):
		'''
		Creates a wait condition that returns immediately.
		'''
		WaitCondition.__init__(self)
		self.value = value

	def arm(self, tasklet):
		'''Overrides WaitCondition.arm'''
		return (self.value, None)

	def disarm(self):
		'''Overrides WaitCondition.disarm'''
		pass

	def __repr__(self):
		return "<WaitForNothing %s>" % (repr(self.value, ))


class WaitForTasklet(WaitCondition):
	'''
	An object that waits for another tasklet to complete.

	Returns the final return value, if any, of the other tasklet. If the other tasklet
	raised an exception, the exception will be propagated into the caller.
	'''

	__slots__ = 'tasklet', '_id', '_callback'

	def __init__(self, tasklet):
		'''An object that waits for another tasklet to complete'''

		WaitCondition.__init__(self)
		self.tasklet = tasklet
		self._id = None

	def arm(self, tasklet):
		'''See L{WaitCondition.arm}'''

		# If the tasklet has already finished, return its value now.
		if self.tasklet.state in (Tasklet.STATE_COMPLETED, Tasklet.STATE_FAILED):
			return self.tasklet.return_value, self.tasklet.exc_info

		self._callback = tasklet.wait_condition_fired
#		print "ARMING: %s after %s" % (tasklet, self.tasklet)

		if self._id is None:
			self._id = self.tasklet.add_completion_callback(self._completion_cb)

	def disarm(self):
		'''See L{WaitCondition.disarm}'''
		if self._id is not None:
			self.tasklet.remove_completion_callback(self._id)
			self._id = None

	def _completion_cb(self, tasklet, retval, exc_info):
		assert tasklet is self.tasklet

		self._id = None

		self._callback(self, retval, exc_info)

		self.tasklet = None
		self._callback = None
		return False

	def __repr__(self):
		return "<WaitForTasklet: for %s>" % self.tasklet


class Message(object):
	'''A message that can be received by or sent to a tasklet.'''

	__slots__ = 'name', 'dest', 'value', 'sender'

	ACCEPT, DEFER, DISCARD = range(3)

	def __init__(self, name, dest=None, value=None, sender=None):
		'''
		@param name: name of message
		@type name: str
		@param dest: destination tasklet for this message
		@type dest: L{Tasklet}
		@param value: value associated with the message
		@param sender: sender tasklet for this message
		@type sender: L{Tasklet}
		'''

		assert isinstance(sender, (Tasklet, type(None)))
		assert isinstance(dest, (Tasklet, type(None)))
		assert isinstance(name, basestring)
		self.name = name
		self.value = value
		self.sender = sender
		self.dest = dest


def _normalize_list_argument(arg, name):
	"""returns a list of strings from an argument that can be either a
	list of strings, None (returns []), or a single string returns
	([arg])"""

	if arg is None:
		return []
	elif isinstance(arg, basestring):
		return [arg]
	elif isinstance(arg, (list, tuple)):
		return arg
	raise TypeError("Argument '%s' must be None, a string, or "
					"a sequence of strings, not %r" % (name, type(arg)))


class WaitForMessages(WaitCondition):
	'''
	An object that waits for messages to arrive.

	Returns the Message object that was sent to the tasklet.
	'''

	__slots__ = 'actions', '_tasklet'

	def __init__(self, accept=None, defer=None, discard=None):
		'''
		Creates an object that waits for any of a set of messages to arrive.

		@param accept: message name or names to accept (receive) in the current state
		@type accept: string or sequence of strings
		@param defer: message name or names to defer (queue) in the current state
		@type defer: string or sequence of strings
		@param discard: message name or names to discard (drop) in the current state
		@type discard: string or sequence of strings
		'''
		WaitCondition.__init__(self)
		self._tasklet = None
		accept = _normalize_list_argument(accept, 'accept')
		defer = _normalize_list_argument(defer, 'defer')
		discard = _normalize_list_argument(discard, 'discard')

		self.actions = dict()
		for name in accept:
			self.actions[name] = Message.ACCEPT
		for name in defer:
			self.actions[name] = Message.DEFER
		for name in discard:
			self.actions[name] = Message.DISCARD

	def arm(self, tasklet):
		'''Overrides WaitCondition.arm'''
		self._tasklet = tasklet
		tasklet.message_actions.update(self.actions)

	def disarm(self):
		'''Overrides WaitCondition.disarm'''
		assert self._tasklet is not None
		for name in self.actions:
			del self._tasklet.message_actions[name]


# Store a global list of all current tasklets. The try/except block
# ensures that even if this module is reloaded, only one list of
# tasklets will ever exist.
try:
	tasklets
except NameError:
	tasklets = weakref.WeakValueDictionary()

def dump():
	print ""
	print "Tasklets:"
	for t in tasklets.values():
		print "%s (rc %d)" % (t, sys.getrefcount(t))
	print ""

class Tasklet(object):
	'''
	An object that launches and manages one tasklet.

	@ivar state: current execution state of the tasklet: one of the STATE_* contants.

	@ivar return_value: the value returned by the tasklet function, or None.

	@cvar STATE_RUNNING: the tasklet function is currently executing code
	@cvar STATE_SUSPENDED: the tasklet function is currently waiting for an event
	@cvar STATE_MSGSEND: the tasklet function is currently sending a message
	@cvar STATE_COMPLETED: the tasklet function has ended
	'''

	STATE_RUNNING, STATE_SUSPENDED, STATE_MSGSEND, STATE_COMPLETED, STATE_FAILED = range(5)

	state_names = "running", "suspended", "msgsend", "completed", "failed"

	__slots__ = "_event", "_exception", "_completion_callbacks", "wait_condition", "_message_queue", "_message_actions", "state", "return_value", "exc_info", "gen", "_gen_name", "__weakref__"

	def __init__(self, gen=None):
		'''
		Launch a generator tasklet.

		@param gen: a generator object that implements the tasklet main body

		If `gen` is omitted or None, L{run} should be overridden in a
		subclass by a suitable generator function.

		'''

		self._event = None
		self._exception = None

		self._completion_callbacks = {}
		self.wait_condition = None
		self._message_queue = []
		self._message_actions = {}
		self.state = Tasklet.STATE_SUSPENDED
		self.return_value = None
		self.exc_info = None

		tasklets[id(self)] = self

		if gen is None:
			self.gen = self.run()
		else:
			assert isinstance(gen, types.GeneratorType)
			self.gen = gen

		self._gen_name = self.gen.gi_frame.f_code.co_name

		stats.increment("chiral.core.tasklet.Tasklet.tasklets_started")
		stats.increment("chiral.core.tasklet.%s.instances" % self._gen_name)

		# Start the generator
		self._next_round()

	def get_message_actions(self):
		"""Dictionary mapping message names to actions ('accept' or
		'discard' or 'defer').  Should normally not be accessed
		directly by the programmer.
		"""
		return self._message_actions

	message_actions = property(get_message_actions)

	def run(self):
		"""
		Method that executes the task.

		Should be overridden in a subclass if no generator is passed
		into the constructor.
		"""

		raise ValueError(
			"run() should be overridden in a subclass if"
			" no generator is passed into the constructor."
		)

	def _invoke(self):
		self.state = Tasklet.STATE_RUNNING
#		dump()
		try:
			if self._exception:
#				print "throwing: %s" % (self._exception, )
				gen_value = self.gen.throw(*self._exception)
			if self._event:
				gen_value = self.gen.send(self._event)
			else:
				gen_value = self.gen.next()

		except StopIteration, ex:
			#print "%s: got StopIteration; zombifying." % (self, )
			self.state = Tasklet.STATE_COMPLETED
			if ex.args:
				retval, = ex.args
			else:
				retval = None
			self._completed(retval)
			return None
		except:
			exc_info = sys.exc_info()
#			print "%s: got exception %s: %s; zombifying." % (self, exc_info[0], exc_info[1])
#			print traceback.format_exc()
			self.state = Tasklet.STATE_FAILED
			self._completed(None, sys.exc_info())
			return None
		else:
			self.state = Tasklet.STATE_SUSPENDED
			assert gen_value is not None			

		self._event = None
		return gen_value

	def _next_round(self):
		assert self.state == Tasklet.STATE_SUSPENDED

		while True:
			# Loop as long as possible.
			gen_value = self._invoke()
			if gen_value is None:
				return

			# If the generator yielded a Message, send it, then loop again.
			if isinstance(gen_value, Message):
				msg = gen_value
				self.state = Tasklet.STATE_MSGSEND
				msg.sender = self
				msg.dest.send_message(msg)
				# Run again, now that the message has been sent.
				continue

			# Make sure each yielded value is a WaitCondition
			if isinstance(gen_value, WaitCondition):
				pass
			elif isinstance(gen_value, Tasklet):
				gen_value = WaitForTasklet(gen_value)
			elif isinstance(gen_value, types.GeneratorType):
				gen_value = WaitForTasklet(Tasklet(gen_value))
			else:
				raise TypeError(
					"yielded values must be WaitConditions,"
					" generators, or a single Message, not %s" % (repr(gen_value),)
				)


			arm_result = gen_value.arm(self)
			if arm_result:
				# If the WaitCondition was already ready, use its value
				# and loop around.
				self._event, self._exception = arm_result
				continue
			else:
				stats.increment("chiral.core.tasklet.%s.waits" % self._gen_name)

			
			self.wait_condition = gen_value

			# Do we have a message to receive? If so, that's our event.
			#msg = self._dispatch_message()
			#if msg is not None:
			#	self._event = msg
			#	# Once we've handled it, loop through again.
			#	continue

			break

	def _dispatch_message(self):
		'''get next message that a tasklet wants to receive; discard
		messages that should be discarded'''
		## while sending out messages, the tasklet implicitly queues
		## all incoming messages
		if self.state == Tasklet.STATE_MSGSEND:
			return None

		## filter out messages with discard action
		def _get_action(msg):
			try:
				return self._message_actions[msg.name]
			except KeyError:
				warnings.warn("Implicitly discarding message %s"
							  " directed to tasklet %s" % (msg, self))
				return Message.DISCARD
		if __debug__:
			self._message_queue = [msg
								   for msg in self._message_queue
									   if _get_action(msg) != Message.DISCARD]
		else:
			## slightly more efficient version of the above
			self._message_queue = [msg for msg in self._message_queue
				if (self._message_actions.getdefault(msg.name, Message.DISCARD)
					!= Message.DISCARD)]

		## find next ACCEPT-able message from queue, and pop it out
		for idx, msg in enumerate(self._message_queue):
			if self._message_actions[msg.name] == Message.ACCEPT:
				break
		else:
			return None
		return self._message_queue.pop(idx)


	def wait_condition_fired(self, triggered_cond, return_value, exc_info = None):
		"""Method that should be called when a wait condition fires"""
#		traceback.print_stack(file=sys.stdout)

		assert triggered_cond is self.wait_condition
		assert self._event is None

		self._event = return_value
		self._exception = exc_info

		self._next_round()
		self._event = None
		self._exception = None

	def add_completion_callback(self, callback):
		'''
		Add a callable to be invoked when the tasklet finishes.
		Return a connection handle that can be used in
		remove_completion_callback()

		The callback will be called like this::
			  callback(tasklet, retval, exc_info)

		where tasklet is the tasklet that finished, and retval its
		return value (or None). If the tasklet terminated by raising
		an exception, exc_info will contain a (type, value, traceback)
		tuple like that returned by sys.exc_info().

		When a completion callback is invoked, it is automatically removed,
		so calling L{remove_callback_callback} afterwards produces a KeyError
		exception.
		'''

		#print "%s: new completion callback %s" % (self, callback)

		if self.state == Tasklet.STATE_COMPLETED:
			callback(self, self.return_value, None)
		elif self.state == Tasklet.STATE_FAILED:
			callback(self, None, self.exc_info)

		handle = id(callback)
		self._completion_callbacks[handle] = callback
		return handle

	def remove_completion_callback(self, handle):
		'''Remove a completion callback previously added with L{add_completion_callback}'''
		del self._completion_callbacks[handle]

	def _completed(self, retval, exc_info=None):
#		if exc_info:
#			print "%s: Completed with exception: %s" % (self, exc_info)
#		else:
#			print "%s: Completed: %s" % (self, retval)

		if self.wait_condition:
			self.wait_condition.disarm()
		self.wait_condition = None

		self.gen = None
		self.return_value = retval
		self.exc_info = exc_info

		callbacks = self._completion_callbacks.values()
		self._completion_callbacks.clear()
		for callback in callbacks:
#			print "- calling: %s" % (callback, )
			callback(self, retval, exc_info)


	def send_message(self, message):
		"""Send a message to be received by the tasklet as an event.

		@warning: Do not call this from another tasklet, only from the
		main loop!  To send a message from another tasklet, yield a
		L{Message} with a correctly set 'dest' parameter.

		"""
		assert isinstance(message, Message)
		assert self._event is None
		if message.dest is None:
			message.dest = self
		self._message_queue.append(message)
		self._event = self._dispatch_message()
		if self._event is not None:
			self._next_round()


	def __repr__(self):
		if self.wait_condition:
			wl = ", waiting on " + repr(self.wait_condition)
		else:
			wl = ""

		cl = ", ".join(str(type(i)) for i in self._completion_callbacks.values())
		if cl: cl = ", completion callbacks " + cl
			
		return "<Tasklet: id %s, \"%s\", %s%s%s>" % (
			id(self),
			self._gen_name,
			self.state_names[self.state],
			wl,
			cl
		)
