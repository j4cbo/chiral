"""Python shell server."""

# Chiral, copyright (c) 2007 Jacob Potter
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 2.

from StringIO import StringIO
import sys
import code
import pydoc

import chiral
from chiral.net import tcp
from chiral.core import xreload

_CHIRAL_RELOADABLE = True

class ShellHelpModeCookie(object):
	"""Magic token indicating that the shell should enter help() mode."""

def reload_replacement(module = None):
	print "reload() does not function correctly with Chiral. Use xreload() instead."

class Helper(pydoc.Helper):
	"""Chiral replacement for site._Helper and pydoc.Helper."""

	def __repr__(self):
		return "Type help() for interactive help, or help(object) for help about object."

	def __call__(self, request = None):
		if request is not None:
			self.help(request)
		else:
			return ShellHelpModeCookie()

	def __init__(self, outputbuffer):
		pydoc.Helper.__init__(self, None, outputbuffer)

class ChiralShellConnection(tcp.TCPConnection, code.InteractiveInterpreter):
	"""A connection to the Chiral shell."""

	def _displayhook(self, result):
		"""Add result to self.outputbuffer"""
		if isinstance(result, ShellHelpModeCookie):
			self.helpmode = True
			return

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
			old_stdin, sys.stdin = sys.stdin, None
			exec codeobj in self.globals, self.locals #pylint: disable-msg=W0122
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
			sys.stdin = old_stdin

	def connection_handler(self):
		"""Main connection handler for Chiral shell."""

		cprt = 'Type "help", "copyright", "credits" or "license" for more information.'
		yield self.send("Python %s on %s\n%s\n(%s)\n" % (
			sys.version,
			sys.platform,
			cprt,
			self.__class__.__name__
		))

		more = False
		inputbuffer = []

		while True:
			# Prompt line
			if self.helpmode:
				yield self.send("help> ")
			elif more:
				yield self.send("... ")
			else:
				yield self.send(">>> ")

			try:
				next_line = yield self.read_line()
			except tcp.ConnectionException:
				return

			if self.helpmode:
				request = next_line.replace('"', '').replace("'", '').strip()
				if request.lower() in ('q', 'quit', '\x04'):
					self.helpmode = False
					continue

				try:
					old_stdout, sys.stdout = sys.stdout, self.outputbuffer
					self.helper(request)
				finally:
					sys.stdout = old_stdout
			else:
				# See if we have a complete block
				inputbuffer.append(next_line)
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
		code.InteractiveInterpreter.__init__(self, locals = {})

		self.outputbuffer = StringIO()

		self.helper = Helper(self.outputbuffer)
		self.helpmode = False

		shell_builtins = dict(__builtins__)
		shell_builtins.update({
			"help":	self.helper,
			"reload": reload_replacement,
			"xreload": xreload.xreload,
			"chiral": chiral
		})

		del shell_builtins["input"]
		del shell_builtins["raw_input"]

		self.globals = {
			"__name__": "__console__",
			"__doc__": None,
			"__builtins__": shell_builtins
		}



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

