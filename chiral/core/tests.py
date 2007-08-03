"""Tests for chiral.core.coroutine"""

# Chiral, copyright (c) 2007 Jacob Potter
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 2.

from chiral.core import coroutine

import unittest

class TestException(Exception):
	"""Subclass of Exception for test purposes"""
	pass

def coroutine_gen_returning(value):
	"""Generator for coroutine that returns immediately"""
	yield
	raise StopIteration(value)

def coroutine_gen_raising(exc):
	"""Generator for coroutine that raises an exception"""
	yield
	raise exc

@coroutine.as_coro
def coroutine_with_decorator(value):
	"""Test of coroutine.task"""
	yield
	raise StopIteration(value)

def coroutine_gen_yielding(callback):
	"""Generator for coroutine that yields cb, then returns the result"""
	res = yield callback
	raise StopIteration(res)

# Yes, CoroutineTests will have a lot of public methods.
#pylint: disable-msg=R0904

class CoroutineTests(unittest.TestCase):
	"""Main test case"""

	def check_suspended(self, coro, wait_condition):
		"""Verify that coro is in the SUSPENDED state, waiting for wait_condition"""
		self.assertEqual(coro.state, coroutine.Coroutine.STATE_SUSPENDED)
		self.assertEqual(coro.result, None)
		self.assertEqual(coro.wait_condition, wait_condition)

	def check_completed(self, coro, value):
		"""Verify that coro has completed with value"""
		self.assertEqual(coro.state, coroutine.Coroutine.STATE_COMPLETED)
		self.assertEqual(coro.result, (value, None))
		self.assertEqual(coro.wait_condition, None)

	def check_failed(self, coro, exc):
		"""Verify that coro has failed with exc"""
		return_value = coro.result[0]
		exc_type, exc_value = coro.result[1][:2]
		self.assertEqual(coro.state, coroutine.Coroutine.STATE_FAILED)
		self.assertEqual((return_value, exc_type, exc_value), (None, type(exc), exc))
		self.assertEqual(coro.wait_condition, None)

	def test_immediate_return(self):
		"""Check a simple, immediately-returning Coroutine."""

		coro = coroutine.Coroutine(coroutine_gen_returning(42))

		self.assertEqual(coro.state, coroutine.Coroutine.STATE_COMPLETED)
		self.assertEqual(coro.result, (42, None))

	def test_failed_coroutine(self):
		"""Check that Coroutines handle exceptions properly."""

		exc = TestException(42)

		dummy_callback_called = []

		def dummy_callback(retval, exc_info):
			"""Check that exceptions are passed to the completion callback"""
			exc_type, exc_value = exc_info[:2]

			self.failIf(dummy_callback_called)
			self.assertEqual((retval, exc_type, exc_value), (None, TestException, exc))

			dummy_callback_called[:] = [True]

		coro = coroutine.Coroutine(
			coroutine_gen_raising(exc),
			default_callback = dummy_callback,
			is_watched = True
		)
		self.check_failed(coro, exc)

	def test_decorator(self):
		"""Check that the @coroutine.task decorator functions properly"""

		coro = coroutine_with_decorator(42)
		self.check_completed(coro, 42)

	def test_wc_not_implemented(self):
		"""Check that attempting to instantiate the WaitCondition base class will fail."""

		self.assertRaises(NotImplementedError, coroutine.WaitCondition)

	def test_callback(self):
		"""Check the WaitForCallback class."""

		callback = coroutine.WaitForCallback()
		coro = coroutine.Coroutine(coroutine_gen_yielding(callback))

		self.check_suspended(coro, callback)

		self.assert_(repr(callback) in repr(coro))
		self.assert_("suspended" in repr(coro))
		self.assert_("coroutine_gen_yielding" in repr(coro))

		callback(42)

		self.check_completed(coro, 42)

	def test_callback_throw(self):
		"""Check the WaitForCallback class's throw method."""

		exc = TestException(42)
		callback = coroutine.WaitForCallback()
		coro = coroutine.Coroutine(coroutine_gen_yielding(callback), is_watched=True)

		self.check_suspended(coro, callback)

		callback.throw(exc)

		self.check_failed(coro, exc)

	def test_wait_for_nothing(self):
		"""Check the WaitForNothing class."""

		callback = coroutine.WaitForNothing(42)
		self.assert_("42" in repr(callback))

		coro = coroutine.Coroutine(coroutine_gen_yielding(callback))

		self.check_completed(coro, 42)

	def test_wait_for_coroutine(self):
		"""Check handling of a Coroutine yielding a WaitForCoroutine."""

		inner_coro = coroutine.Coroutine(coroutine_gen_returning(42))
		coro = coroutine.Coroutine(coroutine_gen_yielding(coroutine.WaitForCoroutine(inner_coro)))

		self.check_completed(coro, 42)

	def test_wait_for_unfinished(self):
		"""Check that WaitForCoroutine will call back a parent coroutine properly."""

		inner_cb = coroutine.WaitForCallback()
		inner_coro = coroutine.Coroutine(coroutine_gen_yielding(inner_cb))

		waitcondition = coroutine.WaitForCoroutine(inner_coro)
		coro = coroutine.Coroutine(coroutine_gen_yielding(waitcondition))

		self.assert_(repr(inner_coro) in repr(waitcondition))
		self.assert_(repr(waitcondition) in repr(coro))

		self.check_suspended(inner_coro, inner_cb)
		self.check_suspended(coro, waitcondition)

		inner_cb(42)

		self.check_completed(inner_coro, 42)
		self.check_completed(coro, 42)

if __name__ == '__main__':
	unittest.main()
