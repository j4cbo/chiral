"""Abstract base classes (interfaces) for messaging"""

from chiral.core.tasklet import returns_waitcondition

class MessagingUser(object):
	"""
	Identifies a user of a messaging service.

	Some messaging protocols have multiple different ways of representing
	a user. For example, a Jabber JID is of the form user@host/resource, but only
	the user@host portions are necessary to uniquely identify that user. The
	same is true of IRC's nick!user@host, where a user is generally fully
	identified by nick alone.

	Different messaging protocol implementations will provide a subclass of
	MessagingUser with whatever internal fields are necessary, and provide
	properties to read out certain useful variations on those fields. The
	properties specified in the base MessagingUser class will be implemented
	meaningfully in all subclasses.
	"""

	@property
	def response(self):
		"""The name to which replies to messages from this user should be sent."""
		raise NotImplementedError

	@property
	def identifier(self):
		"""The portions of the user' identity information which uniquely identify that user."""
		raise NotImplementedError

	@property
	def full(self):
		"""The full identity of the user."""
		raise NotImplementedError

	def __str__(self):
		"""Equivalent to self.full"""
		return self.full

	def __repr__(self):
		return "<%s %s>" % (self.__class__.__name__, self.full)

	

class MessagingConnection(object):
	"""
	A connection to a messaging service.
	"""

	def handle_message(self, sender, message, message_rich=None):
		"""
		Called when the connection receives a message from a user.

		@param sender: The user that sent the message.
		@type sender: MessagingUser
		@param message: The message as plain text.
		@type message: str
		@param message_rich: The message in a protocol-specific rich format, if available.
		"""
		pass

	def handle_room_message(self, room, sender, message, message_rich=None):
		"""
		Called when a message is received in a multiuser room or channel.

		@param room: The name of the room from which the message was received.
		@param sender: The user that sent the message.
		@type sender: MessagingUser
		@param message: The message as plain text.
		@type message: str
		@param message_rich: The message in a protocol-specific rich format, if available.
		"""
		pass

	@returns_waitcondition
	def send_message(self, user, message, message_rich=None):
		"""
		Send a message to a given user.

		@param user: The user to send to. Passing a MessagingUser is equivalent to user.response.
		@type user: str, or MessagingUser
		@param message: The message to send, as plain text.
		@type message: str
		@param message_rich: A protocol-specific rich version of message; will be ignored if rich text is not supported.
		"""
		raise NotImplementedError	

	@returns_waitcondition
	def send_room_message(self, room, message, message_rich=None):
		"""
		Send a message to a given room/channel.

		@param room: The name of the room to send to.
		@param message: The message to send, as plain text.
		@type message: str
		@param message_rich: A protocol-specific rich version of message; will be ignored if rich text is not supported.
		"""
		raise NotImplementedError	

	@returns_waitcondition
	def join_room(self, room):
		"""
		Join room/channel.

		@param room: The name of the room/channel to join.
		"""
		raise NotImplementedError

	@returns_waitcondition
	def leave_room(self, room):
		"""
		Leave room/channel.

		@param room: The name of the room/channel to leave.
		"""
		raise NotImplementedError
