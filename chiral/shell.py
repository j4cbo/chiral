"""Python shell server."""

from chiral.net import tcp
from StringIO import StringIO
import socket
import sys
import traceback
import code

_CHIRAL_RELOADABLE = True

class ChiralShellConnection(tcp.TCPConnection, code.InteractiveInterpreter):
	"""A connection to the Chiral shell."""

	def displayhook(self, result):
		print >>self.outputbuffer, result

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
			sys.displayhook = self.displayhook
			exec codeobj in self.locals
		except SystemExit:
			raise
		except:
			self.showtraceback()
		else:
			if code.softspace(self.outputbuffer, 0):
				self.outputbuffer.write("\n")
		finally:
			sys.displayhook = sys.__displayhook__


	def handler(self):
		self.outputbuffer = StringIO()

		code.InteractiveInterpreter.__init__(self)

		more = False
		buffer = []

		while True:
			# Prompt line
			if more:
				yield self.send("... ")
			else:
				yield self.send(">>> ")


			# Read the next line of code
			try:
				buffer.append((yield self.read_line()).strip())
			except (tcp.ConnectionClosedException, socket.error):
				return

			try:
				more = self.runsource("\n".join(buffer), "<console>")
			except SystemExit:
				break
			
			if not more:
				buffer = []

			output = self.outputbuffer.getvalue()
			if output:
				yield self.send(output)
				self.outputbuffer.truncate(0)


	def write(self, data):
		self.outputbuffer.write(data)

class ChiralShellServer(tcp.TCPServer):
	"""Python shell"""
	connection_class = ChiralShellConnection

