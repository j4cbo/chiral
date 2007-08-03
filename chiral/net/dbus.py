"""D-Bus interface"""

from chiral.net import tcp

from urllib import unquote
import socket
import struct
import os

class BusConnection(tcp.TCPConnection):

	MSG_INVALID = 0
	MSG_METHOD_CALL = 1
	MSG_METHOD_RETURN = 2
	MSG_ERROR = 3
	MSG_SIGNAL = 4

	OPT_INVALID, OPT_PATH, OPT_INTERFACE, OPT_MEMBER, OPT_ERROR_NAME, OPT_REPLY_SERIAL, \
	OPT_DESTINATION, OPT_SENDER, OPT_SIGNATURE = range(9)

	_option_types = [
		None, "o", "s", "s", "s", "u", "s", "s", "g"
	]

	_dbus_type_info = {
		# D-Bus format: (struct.unpack() format, length, alignment unit)
		"y": ("b", 1, 1), # BYTE
		"b": (None, None, 4), # BOOLEAN
		"n": ("h", 2, 2), # INT16
		"q": ("H", 2, 2), # UINT16
		"i": ("i", 4, 4), # INT32
		"u": ("I", 4, 4), # UINT32
		"x": ("q", 8, 8), # INT64
		"t": ("Q", 8, 8), # UINT64
		"d": ("d", 8, 8), # DOUBLE
		"s": (None, None, 4), # STRING
		"o": (None, None, 4), # OBJECT_PATH
		"g": (None, None, 1), # SIGNATURE
		"a": (None, None, 4), # ARRAY
		"(": (None, None, 8), # STRUCT
		"v": (None, None, 1), # VARIANT
		"{": (None, None, 8)  # DICT_ENTRY
	}

	def _dbus_first_type(self, signature):
		"""Return the first single complete type from signature, and the remainder."""

		if signature[0] == "a":
			# The array's type follows the "a"
			array_type, remainder = self._dbus_first_type(signature[1:])
			return ("a" + array_type), remainder
		elif signature[0] == "(":
			# Struct: marked by {}
			offset = signature.index(")")
			return signature[:offset + 1], signature[offset + 1:]
		elif signature[0] == "{":
			# DICT_ENTRY: marked by {}
			offset = signature.index(")")
			return signature[:offset + 1], signature[offset + 1:]
		else:
			# Something else: just a simple type
			return signature[0], signature[1:]

	def _dbus_parse(self, data, signature, current_offset=0):
		"""
		Parse D-Bus serialized data according to signature, which must be a single complete type.

		Returns a tuple (value, bytes_consumed)
		"""

		if signature[0] not in self._dbus_type_info:
			raise Exception("Invalid type signature: %r" % signature)

		struct_format, length, alignment = self._dbus_type_info[signature[0]]

		# Remove alignment padding from data
		if current_offset % alignment != 0:
			padding_length = (alignment - (current_offset % alignment))
			data = data[padding_length:]
			current_offset += padding_length
		else:
			padding_length = 0

		if struct_format:
			# Handle easily-concverted values: INT* / UINT*, DOUBLE
			return struct.unpack(struct_format, data[:length]), padding_length + length

		elif signature[0] == "b":
			# BOOLEAN: only 0 and 1 are valid values.
			value = struct.unpack(self.order + "I", data[:length])
			if value in (0, 1):
				return bool(value), padding_length + 4
			else:
				raise ValueError("invalid value %d in bool" % (value, ))
		elif signature[0] == "s":
			# STRING
			length = struct.unpack(self.order + "I", data[:4])
			value = data[4:length+4]
			# add 5 to take out the terminating NULL as well
			return value.decode("utf-8"), padding_length + length + 5
		elif signature[0] == "o":
			# OBJECT_PATH
			length = struct.unpack(self.order + "I", data[:4])
			value = data[4:length+4]
			# add 5 to take out the terminating NULL as well
			return value, padding_length + length + 5
		elif signature[0] == "g":
			# SIGNATURE
			length = struct.unpack(self.order + "B", data[0:1])
			value = data[1:length+1]
			# len+2 to take out the terminating NULL as well
			return value, padding_length + length + 2
		elif signature[0] == "a":
			# ARRAY
			length = struct.unpack(self.order + "I", data[0:4])

			# The array has padding from the end of the length to the beginning of the
			# first element. Remove it if necessary
			element_alignment = self._dbus_type_info[signature[1]][2]
			if current_offset % element_alignment != 0:
				epadding_length = element_alignment - (current_offset % element_alignment)
				data = data[epadding_length:]
				current_offset += epadding_length

			# Parse out each item
			items = []
			total_consumed = 0
			while current_offset < length:
				value, consumed = self._dbus_parse(data[current_offset:], signature[1:], current_offset + total_consumed)
				items.append(value)
				total_consumed += consumed

	def _dbus_send_message(self, message_type, options, data_format, data):

		option_data = ""

		# Build up the message header
		message_header = { 


	def connection_handler(self):
		"""Main processing loop."""

		# Get the list of accepted authentication methods
		yield self.sendall("\0AUTH\r\n")
		methods = (yield self.read_line()).split(" ")[1:]

		# Try all available methods in order, breaking once we find one that works
		for method in methods:
			if method == "EXTERNAL":
				# Just pass our UID
				yield self.sendall("AUTH EXTERNAL %s\r\n" % str(os.getuid()).encode("hex"))
				result, data = (yield self.read_line()).split(" ", 1)

				if result == "OK":
					self.server_guid = data
					break

			# Ignore unknown methods.

		else:
			raise Exception("No successful authentication methods (tried %s)" % methods)

		yield self.sendall("BEGIN\r\n")

		while True:
			# Read the message header
			header = (yield self.read_exactly(16))

			order, message_type, flags, version = struct.unpack("cBBB", header[:4])

			# Convert the order field to the struct module's format
			order = ">" if (order == "B") else "<"

			if message_type == self.MSG_INVALID:
				raise Exception("Message type INVALID specified")

			if message_type not in (self.MSG_METHOD_CALL, self.MSG_METHOD_RETURN, self.MSG_ERROR, self.MSG_SIGNAL):
				# "Unknown types must be ignored."
				continue

			if version != 1:
				raise Exception("invalid DBus version: %d" % (version, ))

			# Now that we know the byte order, parse the rest of the header
			length, serial, header_length = struct.unpack(order + "III", header[4:16])

			header_field_data = (yield self.read_exactly(header_length + (header_length % 8)))
			pos = 0

			path = None
			interface = None
			member = None
			error_name = None
			reply_serial = None
			destination = None
			sender = None
			signature = None

			# Parse out the header fields
			while True:
				code = header_field_data[pos]
				pos += 1
				if code == 0:
					raise Exception("Header field type INVALID specified")
				elif code == 1:
					bytes, path = self._dbus_parse(header_field_data[pos:], "o")
				elif code == 2:
					bytes, interface = self._dbus_parse(header_field_data[pos:], "s")
				elif code == 3:
					bytes, member = self._dbus_parse(header_field_data[pos:], "s")
				elif code == 4:
					bytes, member = self._dbus_parse(header_field_data[pos:], "s")
				elif code == 5:
					bytes, member = self._dbus_parse(header_field_data[pos:], "s")
				elif code == 6:
					bytes, member = self._dbus_parse(header_field_data[pos:], "s")
				elif code == 7:
					bytes, member = self._dbus_parse(header_field_data[pos:], "s")
				elif code == 8:
					bytes, member = self._dbus_parse(header_field_data[pos:], "s")
					
				
			
	def __init__(self, address):
		"""Initialize a BusConnection to the specified address, in D-Bus format."""

		self.server_guid = True
		self.order = None

		# D-Bus allows multiple addresses to be specified; each will be attempted in order.
		for addr in address.split(";"):

			# Parse out transport and options
			transport, options = addr.split(":", 1)
			options_kv = [ kv.split("=") for kv in options.split(",") ]
			options = dict((key, unquote(value)) for key, value in options_kv)

			# Pick appropriate transport
			if transport == "unix":

				# UNIX Domain socket
				sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
				if "path" in options:
					sock.connect(options["path"])
				elif "abstract" in options:
					sock.connect(options["abstract"])
				else:
					# No recognized option. Continue; maybe we'll see something
					# we know.
					continue

				# Successs
				break

			elif transport == "tcp":
				# XXX tcp should be supported!
				pass

			else:
				# Maybe something else will be specified
				pass

		else:
			raise ValueError("Invalid address: %s" % address)

		# OK, we have our socket. The next step is authentication, which is done inside
		# connection_handler, so go ahead and start the connection now. 
		tcp.TCPConnection.__init__(self, sock, None)
		self.start()

			
	def get_object(self, name, path):
		pass

class SystemBusConnection(BusConnection):
	def __init__(self):
		"""Initialize the BusConnection, automatically connecting to the system bus."""

		BusConnection.__init__(self, os.environ.get(
			"DBUS_SYSTEM_BUS_ADDRESS",
			"unix:path=/var/run/dbus/system_bus_socket"
		))


class ObjectProxy(object):
	pass
