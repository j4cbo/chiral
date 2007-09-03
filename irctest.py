#/usr/bin/env python2.5

print "Loading modules..."

from chiral.core import stats, coroutine
from chiral.net import reactor

from chiral.messaging.irc import IRCConnection

from chiral.web.httpd import HTTPServer
from paste.urlmap import URLMap
from paste.httpexceptions import HTTPNotFound
from paste.pony import PonyMiddleware
from chiral.web.introspector import Introspector
from chiral.web.comet import CometPage
from chiral.web.servers import StaticFileServer
from chiral.web.framework import *

class CometIRCReaderPage(CometPage):
	def __init__(self, environ, start_resp, irc_connection):
		"""Initialize the CometIRCReader"""
		self.irc_connection = irc_connection
		CometPage.__init__(
			self, environ, start_resp,
			content_type="text/plain", method=self.METHOD_JS
		)
		irc_connection.add_line_handler(self.line)

	def run(self):
		"""Main loop"""
		yield coroutine.WaitForCallback()
		pass

	def line(self, data):
		self.send_chunk(repr(data))
			

class CometIRCReader(object):
	def __init__(self, irc_connection):
		self.irc_connection = irc_connection

	def __call__(self, environ, start_response):
		path_info = environ.get('PATH_INFO', '')

		if path_info == '/':
			return CometIRCReaderPage(environ, start_response, self.irc_connection)
		else:
			start_response('404 Not Found', [('Content-Type', 'text/html')])
			return [ "404 Not Found" ]

last_messages = []

class IRCBot(IRCConnection):

	def add_line_handler(self, handler):
		self.line_handlers.append(handler)

	def display_line(self, msg):
		# Add to last_messages
		last_messages.append(msg)
		if len(last_messages) > 10: last_messages.pop(0)

		for lh in self.line_handlers:
			try:
				lh(msg)
			except Exception:
				pass

	def handle_message(self, sender, message):
		print "Message from %s: %s" % (sender, message)

		if sender.identifier == "j4cbo" and message.startswith("!") and " " in message:
			command, target = message.split(" ", 1)
			if command == "!join":
				print "joining %s" % target
				self.join_room(target)
			elif command == "!part":
				print "leaving %s" % target
				self.leave_room(target)
		else:
			self.display_line("<%s> %s" % (sender.identifier, message))


	def handle_room_message(self, room, sender, message):
		print "Message from %s in %s: %s" % (sender, room, message)

		self.display_line("%s: <%s> %s" % (room, sender.identifier, message))

        def __init__(self, server, nick, username, realname):
		IRCConnection.__init__(self, server, nick, username, realname)
		self.line_handlers = []

irc_conn = IRCBot(
	server=("irc.freenode.net", 6667),
	nick="j4bot",
	username="j4cbo",
	realname="jacob"
)

@coroutine_page()
def last_page():
        yield
	print "returning: %s" % repr("\n".join(last_messages))
        raise StopIteration("\n".join(last_messages))


application = URLMap()
application.update({
	"/pony": PonyMiddleware(HTTPNotFound()),
	"/introspector": Introspector(),
	"/irc": CometIRCReader(irc_conn),
	"/last": last_page,
	"/static": StaticFileServer("/home/jacob/code/chiral/irc-static")
})

HTTPServer(
	bind_addr = ('', 8081),
	application = application
).start()

print "Running..."

reactor.run()

stats.dump()
