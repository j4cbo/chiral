"""Tests for chiral.core.tasklet"""

from chiral.core import tasklet

import unittest

class TestException(Exception):
	"""Subclass of Exception for test purposes"""
	pass

def tasklet_gen_returning(value):
	"""Generator for tasklet that returns immediately"""
	raise StopIteration(value)
	yield

def tasklet_gen_raising(exc):
	"""Generator for tasklet that raises an exception"""
	raise exc
	yield

@tasklet.task
def tasklet_with_decorator(value):
	"""Test of tasklet.task"""
	raise StopIteration(value)
	yield

def tasklet_gen_yielding(callback):
	"""Generator for tasklet that yields cb, then returns the result"""
	res = yield callback
	raise StopIteration(res)


class TaskletTests(unittest.TestCase):
	"""Main test case"""

	def check_suspended(self, tlet, wait_condition):
		"""Verify that tlet is in the SUSPENDED state, waiting for wait_condition"""
		self.assertEqual(tlet.state, tasklet.Tasklet.STATE_SUSPENDED)
		self.assertEqual(tlet.result, None)
		self.assertEqual(tlet.wait_condition, wait_condition)

	def check_completed(self, tlet, value):
		"""Verify that tlet has completed with value"""
		self.assertEqual(tlet.state, tasklet.Tasklet.STATE_COMPLETED)
		self.assertEqual(tlet.result, (value, None))
		self.assertEqual(tlet.wait_condition, None)

	def check_failed(self, tlet, exc):
		"""Verify that tlet has failed with exc"""
		return_value = tlet.result[0]
		exc_type, exc_value = tlet.result[1][:2]
		self.assertEqual(tlet.state, tasklet.Tasklet.STATE_FAILED)
		self.assertEqual((return_value, exc_type, exc_value), (None, type(exc), exc))
		self.assertEqual(tlet.wait_condition, None)

	def test_immediate_return(self):
		"""Check a simple, immediately-returning Tasklet."""

		tlet = tasklet.Tasklet(tasklet_gen_returning(42))

		self.assertEqual(tlet.state, tasklet.Tasklet.STATE_COMPLETED)
		self.assertEqual(tlet.result, (42, None))

	def test_failed_tasklet(self):
		"""Check that Tasklets handle exceptions properly."""

		exc = TestException(42)

		dummy_callback_called = []

		def dummy_callback(cb_tlet, retval, exc_info):
			"""Check that exceptions are passed to the completion callback"""
			exc_type, exc_value = exc_info[:2]

			self.failIf(dummy_callback_called)
			self.assertEqual((retval, exc_type, exc_value), (None, TestException, exc))

			dummy_callback_called[:] = [cb_tlet]

		tlet = tasklet.Tasklet(tasklet_gen_raising(exc), default_callback = dummy_callback)

		self.check_failed(tlet, exc)

	def test_decorator(self):
		"""Check that the @tasklet.task decorator functions properly"""

		tlet = tasklet_with_decorator(42)
		self.check_completed(tlet, 42)

	def test_wait_condition_NotImplemented(self):
		"""Check that attempting to instantiate the WaitCondition base class will fail."""

		self.assertRaises(NotImplementedError, tasklet.WaitCondition)

	def test_callback(self):
		"""Check the WaitForCallback class."""

		callback = tasklet.WaitForCallback()
		tlet = tasklet.Tasklet(tasklet_gen_yielding(callback))

		self.check_suspended(tlet, callback)

		self.assert_(repr(callback) in repr(tlet))
		self.assert_("suspended" in repr(tlet))
		self.assert_("tasklet_gen_yielding" in repr(tlet))

		callback(42)

		self.check_completed(tlet, 42)

	def test_callback_throw(self):
		"""Check the WaitForCallback class's throw method."""

		def dummy_callback(cb_tlet, retval, exc_info):
			pass

		exc = TestException(42)
		callback = tasklet.WaitForCallback()
		tlet = tasklet.Tasklet(tasklet_gen_yielding(callback), dummy_callback)

		self.check_suspended(tlet, callback)

		callback.throw(exc)

		self.check_failed(tlet, exc)

	def test_wait_for_nothing(self):
		"""Check the WaitForNothing class."""

		callback = tasklet.WaitForNothing(42)
		self.assert_("42" in repr(callback))

		tlet = tasklet.Tasklet(tasklet_gen_yielding(callback))

		self.check_completed(tlet, 42)

	def test_wait_for_tasklet(self):
		"""Check handling of a Tasklet yielding a WaitForTasklet."""

		inner_tlet = tasklet.Tasklet(tasklet_gen_returning(42))
		tlet = tasklet.Tasklet(tasklet_gen_yielding(tasklet.WaitForTasklet(inner_tlet)))

		self.check_completed(tlet, 42)

	def test_wait_for_unfinished(self):
		"""Check that WaitForTasklet will call back a parent tasklet properly."""

		inner_cb = tasklet.WaitForCallback()
		inner_tlet = tasklet.Tasklet(tasklet_gen_yielding(inner_cb))

		waitcondition = tasklet.WaitForTasklet(inner_tlet)
		tlet = tasklet.Tasklet(tasklet_gen_yielding(waitcondition))

		self.assert_(repr(inner_tlet) in repr(waitcondition))
		self.assert_(repr(waitcondition) in repr(tlet))

		self.check_suspended(inner_tlet, inner_cb)
		self.check_suspended(tlet, waitcondition)

		inner_cb(42)

		self.check_completed(inner_tlet, 42)
		self.check_completed(tlet, 42)

		pass

if __name__ == '__main__':
	unittest.main()
