"""Tests for chiral.net.netcore and chiral.net.tcp"""

# Chiral, copyright (c) 2007 Jacob Potter
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 2.

from __future__ import with_statement
import unittest
from decorator import decorator
import gc

from chiral.core import coroutine
from chiral.net import tcp, reactor

from chiral.web.httpd import HTTPServer
from chiral.web.introspector import Introspector

class EchoConnection(tcp.TCPConnection):
	"""Echo server."""

	def connection_handler(self):
		"""Read and echo lines"""
		line = yield self.read_line()
		yield self.sendall(line + "\r\n")

class EchoServer(tcp.TCPServer):
	"""Echo server."""
        connection_class = EchoConnection

@decorator
def reactor_test(coro, self):
	cr = coro(self)
	cr.start()
	reactor.run()
	self.assertEqual(cr.state, cr.STATE_COMPLETED)

class TCPTests(unittest.TestCase):

	@reactor_test
	@coroutine.as_coro
	def test_echo(self):
		"""EchoServer echo line"""

		with EchoServer(bind_addr = ('', 12122)):
			client = tcp.TCPConnection(remote_addr = ('localhost', 12122))
			yield client.connect()
			yield client.send("hello world\r\n")

			resp = yield client.read_line()
			self.assertEqual(resp, "hello world")

			client.close()

	@reactor_test
	@coroutine.as_coro
	def test_disconnect(self):
		"""EchoServer disconnect with no lines"""

		with EchoServer(bind_addr = ('', 12122)):
			yield reactor.schedule()
			client = tcp.TCPConnection(remote_addr = ('localhost', 12122))
			yield client.connect()

			client.close()

#HTTPServer(bind_addr = ('', 8081), application = Introspector()).start()

if __name__ == "__main__":

	suite = unittest.TestLoader().loadTestsFromTestCase(TCPTests)

	for i in xrange(1000):
		gc.collect()
		res = unittest.TextTestRunner(verbosity=2).run(suite)
		if not res.wasSuccessful(): break
