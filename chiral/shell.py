"""Python shell server."""

# Chiral, copyright (c) 2007 Jacob Potter
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 2.

from chiral.net import tcp
from StringIO import StringIO
import sys
import code

_CHIRAL_RELOADABLE = True

class ChiralShellConnection(tcp.TCPConnection, code.InteractiveInterpreter):
	"""A connection to the Chiral shell."""

	def _displayhook(self, result):
		"""Add result to self.outputbuffer"""
		if result is not None:
			self.outputbuffer.write(repr(result) + "\n")

	def runcode(self, codeobj):
		"""Execute a code object.

		When an exception occurs, self.showtraceback() is called to
		display a traceback.  All exceptions are caught except
		SystemExit, which is reraised.

		A note about KeyboardInterrupt: this exception may occur
		elsewhere in this code, and may not always be caught.  The
		caller should be prepared to deal with it.
		"""

		try:
			old_display_hook, sys.displayhook = sys.displayhook, self._displayhook
			old_stdout, sys.stdout = sys.stdout, self.outputbuffer
			exec codeobj in self.locals #pylint: disable-msg=W0122
		except SystemExit:
			raise
		except:
			self.showtraceback()
		else:
			if code.softspace(self.outputbuffer, 0):
				self.outputbuffer.write("\n")
		finally:
			sys.displayhook = old_display_hook
			sys.stdout = old_stdout

	def connection_handler(self):
		"""Main connection handler for Chiral shell."""
		self.outputbuffer = StringIO()

		code.InteractiveInterpreter.__init__(self)

		more = False
		inputbuffer = []

		while True:
			# Prompt line
			if more:
				yield self.send("... ")
			else:
				yield self.send(">>> ")


			# Read the next line of code
			try:
				inputbuffer.append((yield self.read_line()))
			except tcp.ConnectionException:
				return

			# See if we have a complete block
			try:
				more = self.runsource("\n".join(inputbuffer), "<console>")
			except SystemExit:
				break
			
			if not more:
				inputbuffer = []

			# Send the response
			output = self.outputbuffer.getvalue()
			if output:
				yield self.send(output)
				self.outputbuffer.truncate(0)

	def write(self, data):
		"""
		Add data to self.outputbuffer.

		Used internally by self.showtraceback().
		"""
		self.outputbuffer.write(data)

	def __init__(self, sock, addr, server):
		tcp.TCPConnection.__init__(self, sock, addr, server)
		code.InteractiveInterpreter.__init__()
		self.outputbuffer = None
		


class ChiralShellServer(tcp.TCPServer):
	"""Python shell.

	The shell interface provided by ChiralShellServer is similar to the Python interactive
	interpreter. It supports single- and multi-line statements, the 'import' statement, etc.,
	and runs in the same interpreter context as the rest of the Chiral process. Be careful;
	infinite loops will not be interruptible, although sending a Control-C directly to the
	process will cause a KeyboardInterrupt to be raised in the inner loop.

	WARNING: This has the potential to be extremely insecure. ChiralShellServer should only ever be
	bound to the local network interface on a trusted machine; under no circumstances is it safe to
	expose to the Internet.
	"""

	connection_class = ChiralShellConnection

