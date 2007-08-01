"""IRC"""

from chiral.messaging import base
from chiral.net import tcp
from chiral.core.tasklet import returns_waitcondition

_CHIRAL_RELOADABLE = True

from datetime import datetime
import socket
import re

class IRCUser(base.MessagingUser):
	"""An IRC user"""

	def __init__(self, name):
		"""Parse an IRC user from its combined string"""

		base.MessagingUser.__init__(self)

		if "@" in name:
			nickuser, self.host = name.split("@", 1)
			if "!" in nickuser:
				self.nick, self.user = nickuser.split("!", 1)
			else:
				self.nick, self.user = nickuser, None
		else:
			self.host = None
			if "!" in name:
				self.nick, self.user = name.split("!", 1)
			else:
				self.nick, self.user = name, None

	@property
	def response(self):
		"""The name to which replies to messages from this user should be sent."""
		return self.nick

	@property
	def identifier(self):
		"""The portions of the user' identity information which uniquely identify that user."""
		return self.nick

	@property
	def full(self):
		"""The full identity of the user."""
		out = self.nick

		if self.user:
			out += "!" + self.user

		if self.host:
			out += "@" + self.host

		return out


class IRCConnection(base.MessagingConnection, tcp.TCPConnection):
	"""An IRC connection"""

	@returns_waitcondition
	def _send_line(self, prefix, command, *params):
		"""Send a command via the IRC connection."""

		if prefix:
			if " " in prefix:
				raise ValueError
			line = ":%s %s" % (prefix, command)
		else:
			line = command

		if params:
			params = list(params)
			# All but the last parameter must be a single word
			for param in params[:-1]:
				if " " in param:
					raise ValueError
				if param.startswith(":"):
					raise ValueError

			# Prefix the last entry with a :
			params[-1] = ":" + params[-1]

			line += " " + " ".join(params)

		# Send it
		return self.sendall(line + "\r\n")

	@returns_waitcondition
	def send_message(self, user, message, message_rich=None):
		"""Send a message to a given user."""

		# If we've been passed a MessagingUser object, send to its response address
		if not isinstance(user, basestring):
			user = user.response

		return self._send_line(None, "PRIVMSG", user, message)

	@returns_waitcondition
	def send_room_message(self, room, message, message_rich=None):
		"""Send a message to a given room."""

		return self._send_line(None, "PRIVMSG", room, message)

	@returns_waitcondition
	def _ctcp_send_message(self, user, message, chunks):
		"""Send a CTCP message to a user.

		@param chunks: A list of (tag, data) tuples; data may be None.
		"""

		# Mid-level CTCP quoting on the message itself
		if message:
			line = message.replace("\\", "\\\\").replace("\001", "\\a")
		else:
			line = ""

		for tag, data in chunks:
			if data:
				chunk = "%s %s" % (tag, data)
			else:
				chunk = tag

			# Mid-level CTCP quoting on each chunk, as well
			line += "\001%s\001" % (chunk.replace("\\", "\\\\").replace("\001", "\\a"))

		# Low-level CTCP quoting now
		line = line.replace("\020", "\020\020")
		line = line.replace("\0", "\0200")
		line = line.replace("\r", "\020r")
		line = line.replace("\n", "\020n")

		# If we've been passed a MessagingUser object, send to its response address
		if not isinstance(user, basestring):
			user = user.response

		return self._send_line(None, "NOTICE", user, line)

	@returns_waitcondition
	def join_room(self, room):
		"""Join an IRC channel."""
		return self._send_line(None, "JOIN", room)

	@returns_waitcondition
	def leave_room(self, room):
		"""Join an IRC channel."""
		return self._send_line(None, "PART", room)

	@staticmethod
	def _parse_line(line):
		"""Parse an IRC message line into its prefix, command, and parameter list."""

		if line.startswith(":"):
			prefix, line = line.split(" ", 1)
			prefix = prefix[1:]
		else:
			prefix = None

		command, param_string = line.split(" ", 1)

		params = []
		while param_string:
			if param_string.startswith(":"):
				params.append(param_string[1:])
				break

			next_params = param_string.split(" ", 1)
			params.append(next_params[0])

			if len(next_params) > 1:
				param_string = next_params[1]
			else:
				break

		return prefix, command, params

	@staticmethod
	def _ctcp_parse_message(line):
		"""
		Separate all CTCP extended data out of line.
		
		Returns a tuple (line, chunks). Normal message data is returned as line; 
		chunks is a list of the extended messages, each itself a tuple (tag, data).
		\\001-delimited units.
		"""

		# Perform low-level dequoting
		def ll_replace(match):
			"""Undo low-level quoting."""
			dequote_table = {
				"\020\020" : "\020",
				"\0200" : "\0",
				"\020r" : "\r",
				"\020n" : "\n"
			}
			try:
				return dequote_table[match.group()]
			except KeyError:
				return ''

		line = re.sub("\020.", ll_replace, line)

		# Pull out chunks within pairs of \001
		chunks = []
		parts = line.split("\001")
		line = ''
		for index, part in enumerate(parts):
			# Perform CTCP dequoting
			part = part.replace("\\a", "\001")
			part = part.replace("\\\\", "\\")
			if index % 2 == 0:
				# Even index: regular data
				line += part
			else:
				# Odd index: CTCP chunk
				if not part:
					continue
				elif " " not in part:
					chunks.append((part, None))
				else:
					chunks.append(part.split(" ", 1))

		return line, chunks

	def connection_handler(self):
		"""Main connection event handler loop."""

		yield self.sendall(
			"NICK %s\r\n"
			"USER %s localhost %s %s\r\n"
			% (self.nick, self.username, self.remote_addr[0], self.realname)
		)

		while True:
			line = yield self.read_line()
			print line
			prefix, command, params = self._parse_line(line)

			if command == "PING":
				# Respond to PINGs automatically
				yield self._send_line(None, "PONG", params[-1])

			elif prefix and command in ("PRIVMSG", "NOTICE"):
				# Handle NOTICE from a user as though it were a PRIVMSG. Note that
				# some NOTICEs will be sent with no prefix (during connect, for example),
				# so leave those alone.
				user = IRCUser(prefix)

				room, message = params
				message, chunks = self._ctcp_parse_message(message)

				# Normal data
				if message:
					if room[0] in ("#", "&"):
						self.handle_room_message(room, user, message)
					else:
						self.handle_message(user, message)


				# CTCP commands
				for tag, data in chunks:
					if tag == "FINGER":
						yield self._ctcp_send_message(user, None,
							[("FINGER", ":%s" % (self.username, ))]
						)
					elif tag == "VERSION":
						yield self._ctcp_send_message(user, None,
							[("VERSION", "chiral.messaging.irc:0.0.1:chiral")]
						)
					elif tag == "PING":
						yield self._ctcp_send_message(user, None,
							[("PING", data)]
						)
					elif tag == "TIME":
						yield self._ctcp_send_message(user, None,
							[("TIME", str(datetime.now()))]
						)
					
			else:
				print "Got: %s %s %s" % (prefix, command, params)

	def __init__(self, server, nick, username, realname):
		self.nick = nick
		self.username = username
		self.realname = realname

		sock = socket.socket()
		sock.connect(server)

		base.MessagingConnection.__init__(self)
		tcp.TCPConnection.__init__(self, sock, server)

		self.start()
