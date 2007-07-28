from chiral.core import tasklet

import unittest

class TestException(Exception):
	pass

def tasklet_gen_returning(value):
	raise StopIteration(value)
	yield

def tasklet_gen_raising(exc):
	raise exc
	yield

@tasklet.task
def tasklet_with_decorator(value):
	raise StopIteration(value)
	yield

def tasklet_gen_yielding(cb):
	res = yield cb
	raise StopIteration(res)


class TaskletTests(unittest.TestCase):

	def check_suspended(self, tlet, cb):
		self.assertEqual(tlet.state, tasklet.Tasklet.STATE_SUSPENDED)
		self.assertEqual(tlet.result, None)
		self.assertEqual(tlet.wait_condition, cb)

	def check_completed(self, tlet, value):
		self.assertEqual(tlet.state, tasklet.Tasklet.STATE_COMPLETED)
		self.assertEqual(tlet.result, (value, None))
		self.assertEqual(tlet.wait_condition, None)

	def check_failed(self, tlet, exc):
		return_value, (exc_type, exc_value, exc_traceback) = tlet.result
		self.assertEqual(tlet.state, tasklet.Tasklet.STATE_FAILED)
		self.assertEqual((return_value, exc_type, exc_value), (None, type(exc), exc))
		self.assertEqual(tlet.wait_condition, None)

	def testImmediateReturn(self):
		"""Check a simple, immediately-returning Tasklet."""

		tlet = tasklet.Tasklet(tasklet_gen_returning(42))

		self.assertEqual(tlet.state, tasklet.Tasklet.STATE_COMPLETED)
		self.assertEqual(tlet.result, (42, None))

	def testFailedTasklet(self):
		"""Check that Tasklets handle exceptions properly."""

		exc = TestException(42)

		dummy_callback_called = []

		def dummy_callback(cb_tlet, retval, exc_info):
			exc_type, exc_value, exc_traceback = exc_info

			self.failIf(dummy_callback_called)
			self.assertEqual((retval, exc_type, exc_value), (None, TestException, exc))

			dummy_callback_called[:] = [cb_tlet]

		tlet = tasklet.Tasklet(tasklet_gen_raising(exc), default_callback = dummy_callback)

		self.check_failed(tlet, exc)

	def testDecorator(self):
		"""Check that the @tasklet.task decorator functions properly"""

		tlet = tasklet_with_decorator(42)
		self.check_completed(tlet, 42)

	def testWaitConditionNotImplemented(self):
		"""Check that attempting to instantiate the WaitCondition base class will fail."""

		self.assertRaises(NotImplementedError, tasklet.WaitCondition)

	def testCallback(self):
		"""Check the WaitForCallback class."""

		cb = tasklet.WaitForCallback()
		tlet = tasklet.Tasklet(tasklet_gen_yielding(cb))

		self.check_suspended(tlet, cb)

		self.assert_(repr(cb) in repr(tlet))
		self.assert_("suspended" in repr(tlet))
		self.assert_("tasklet_gen_yielding" in repr(tlet))

		cb(42)

		self.check_completed(tlet, 42)

	def testCallbackThrow(self):
		"""Check the WaitForCallback class's throw method."""

		def dummy_callback(cb_tlet, retval, exc_info):
			pass

		exc = TestException(42)
		cb = tasklet.WaitForCallback()
		tlet = tasklet.Tasklet(tasklet_gen_yielding(cb), dummy_callback)

		self.check_suspended(tlet, cb)

		cb.throw(exc)

		self.check_failed(tlet, exc)

	def testWaitForNothing(self):
		"""Check the WaitForNothing class."""

		cb = tasklet.WaitForNothing(42)
		self.assert_("42" in repr(cb))

		tlet = tasklet.Tasklet(tasklet_gen_yielding(cb))

		self.check_completed(tlet, 42)

	def testWaitForTasklet(self):
		"""Check handling of a Tasklet yielding a WaitForTasklet."""

		inner_tlet = tasklet.Tasklet(tasklet_gen_returning(42))
		tlet = tasklet.Tasklet(tasklet_gen_yielding(tasklet.WaitForTasklet(inner_tlet)))

		self.check_completed(tlet, 42)

	def testWaitForUnfinished(self):
		"""Check that WaitForTasklet will call back a parent tasklet properly."""

		inner_cb = tasklet.WaitForCallback()
		inner_tlet = tasklet.Tasklet(tasklet_gen_yielding(inner_cb))

		wc = tasklet.WaitForTasklet(inner_tlet)
		tlet = tasklet.Tasklet(tasklet_gen_yielding(wc))

		self.assert_(repr(inner_tlet) in repr(wc))
		self.assert_(repr(wc) in repr(tlet))

		self.check_suspended(inner_tlet, inner_cb)
		self.check_suspended(tlet, wc)

		inner_cb(42)

		self.check_completed(inner_tlet, 42)
		self.check_completed(tlet, 42)

		pass

if __name__ == '__main__':
	unittest.main()
