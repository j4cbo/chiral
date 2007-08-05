#!/usr/bin/env python

"""
Client module for memcached (memory cache daemon)

Overview
========

See U{the MemCached homepage<http://www.danga.com/memcached>} for more about memcached.

This is the Chiral version of the memcached client, modified to make use of coroutines.

Usage summary
=============

This should give you a feel for how this module operates::

	import memcache
	mc = memcache.Client(['127.0.0.1:11211'], debug=0)

	mc.set("some_key", "Some value")
	value = yield mc.get("some_key")

	yield mc.set("another_key", 3)
	yield mc.delete("another_key")

	yield mc.set("key", "1")   # note that the key used for incr/decr must be a string.
	yield mc.incr("key")
	yield mc.decr("key")

The standard way to use memcache with a database is like this::

	key = derive_key(obj)
	obj = yield mc.get(key)
	if not obj:
		obj = yield backend_api.get(...)
		yield mc.set(obj)

	# we now have obj, and future passes through this code
	# will use the object from the cache.

Detailed Documentation
======================

More detailed documentation is available in the L{Client} class.
"""

from chiral.core.coroutine import Coroutine, returns_waitcondition, as_coro_waitcondition
from chiral.net import tcp, reactor

from decorator import decorator

import sys
import socket
import time
try:
	import cPickle as pickle
except ImportError:
	import pickle

try:
	from zlib import compress, decompress
	_SUPPORTS_COMPRESS = True
except ImportError:
	_SUPPORTS_COMPRESS = False
	# quickly define a decompress just in case we recv compressed data.
	def decompress(val):
		"""Bogus decompress routine"""
		raise ValueError("received compressed data but I don't support compession (import error)")

from binascii import crc32   # zlib version is not cross-platform

__author__	= "Evan Martin <martine@danga.com>"
__version__ = "1.00"
__copyright__ = "Copyright (C) 2003 Danga Interactive"
__license__   = "Python"

SERVER_MAX_KEY_LENGTH = 250

# Storing values larger than 1MB requires recompiling memcached.  If you do,
# this value can be changed by doing "memcache.SERVER_MAX_VALUE_LENGTH = N"
# after importing this module.
SERVER_MAX_VALUE_LENGTH = 1024*1024

from threading import local

class Client(local):
	"""
	Object representing a pool of memcache servers.

	See L{memcache} for an overview.

	In all cases where a key is used, the key can be either:

	1. A simple hashable type (string, integer, etc.).
	2. A tuple of C{(hashvalue, key)}.  This is useful if you want to avoid
		   making this module calculate a hash value.  You may prefer, for
		   example, to keep all of a given user's objects on the same memcache
		   server, so you could use the user's unique id as the hash value.

	@group Setup: __init__, set_servers, forget_dead_hosts, disconnect_all
	@group Insertion: set, add, replace, set_multi
	@group Retrieval: get, get_multi
	@group Integers: incr, decr
	@group Removal: delete, delete_multi
	@sort: __init__, set_servers, forget_dead_hosts, disconnect_all, \
		set, set_multi, add, replace, get, get_multi, incr, decr, delete, delete_multi
	"""

	_FLAG_PICKLE = 1 << 0
	_FLAG_INTEGER = 1 << 1
	_FLAG_LONG = 1 << 2
	_FLAG_COMPRESSED = 1 << 3

	_SERVER_RETRIES = 10  # how many times to try finding a free server.

	# exceptions for Client
	class MemcachedKeyError(Exception):
		"""Invalid key."""
		pass

	class MemcachedKeyLengthError(MemcachedKeyError):
		"""Key too long."""
		pass

	class MemcachedKeyCharacterError(MemcachedKeyError):
		"""Key contains invalid characters."""
		pass

	def __init__(self, servers, debug=False):
		"""
		Create a new Client object with the given list of servers.

		@param servers: C{servers} is passed to L{set_servers}.
		@param debug: whether to display error messages when a server is
		unavailable.
		"""

		local.__init__(self)

		self.debug = debug

		self.servers = []
		self.buckets = []

		self.set_servers(servers)
		self.stats = {}

	def set_servers(self, servers):
		"""
		Set the pool of servers used by this client.

		@param servers: an array of servers.

		Servers can be passed in two forms:

		1. Strings of the form C{"host:port"}, which implies a default weight of 1.
		2. Tuples of the form C{("host:port", weight)}, where C{weight} is
		an integer weight value.
		"""

		self.servers = []
		self.buckets = []

		for server_desc in servers:
			if type(server_desc) == tuple:
				server_addr, weight = server_desc
			else:
				server_addr, weight = server_desc, 1

			server = _ServerConnection(server_addr, weight, self._debuglog)

			self.servers.append(server)

			for _index in range(weight):
				self.buckets.append(server)

	@as_coro_waitcondition
	def get_stats(self):
		"""
		Get statistics from each of the servers.

		@return: A list of tuples ( server_identifier, stats_dictionary ).
		The dictionary contains a number of name/value pairs specifying
		the name of the status field and the string value associated with
		it.  The values are not converted from strings.
		"""

		data = []
		for server in self.servers:
			stats = yield server.get_stats()
			data.append(stats)

		raise StopIteration(data)

	def flush_all(self):
		"Expire all data currently in the memcache servers."

		for server in self.servers:
			yield server.sendeall("flush_all")
			yield server.read_line()

	def _debuglog(self, string):
		"""Log string to debugging output, if enabled."""
		if self.debug:
			sys.stderr.write("MemCached: %s\n" % string)

	def forget_dead_hosts(self):
		"""Reset every host in the pool to an "alive" state."""
		for server in self.servers:
			server.dead_until = 0

	def _get_server_for(self, key):
		"""Given a key, return the _ServerConnection to which that key should be mapped."""

		if type(key) == tuple:
			serverhash, key = key
		else:
			serverhash = crc32(key)

		for i in range(Client._SERVER_RETRIES):
			server = self.buckets[serverhash % len(self.buckets)]

			#if server.connect():
				#print "(using server %s)" % server,
			return server, key

			serverhash = crc32(str(serverhash) + str(i))

		return None, None

	def disconnect_all(self):
		"""Disconnect all servers."""
		for server in self.servers:
			server.close()

	@as_coro_waitcondition
	def delete(self, key, dead_time=0):
		"""
		Deletes a key from the memcache.

		@return: Nonzero on success.
		@param dead_time: number of seconds any subsequent set / update commands should fail. Defaults to 0 for no delay.
		@rtype: bool
		"""

		check_key(key)
		server, key = self._get_server_for(key)

		if not server:
			raise StopIteration(False)

		if dead_time is None:
			dead_time = 0

		try:
			yield server.sendall("delete %s %d\r\n" % (key, dead_time))

			res = yield server.read_line()
			if res != "DELETED":
				self._debuglog("expected 'DELETED', got %r" % (res, ))
				raise StopIteration(False)

			raise StopIteration(True)

		except socket.error, exc:
			server.mark_dead(exc[1])
			raise StopIteration(False)

	@returns_waitcondition
	def incr(self, key, delta=1):
		"""
		Sends a command to the server to atomically increment the value for C{key} by
		C{delta}, or by 1 if C{delta} is unspecified.  Returns None if C{key} doesn't
		exist on server, otherwise it returns the new value after incrementing.

		Note that the value for C{key} must already exist in the memcache, and it
		must be the string representation of an integer.

		>>> mc.set("counter", "20")  # returns True, indicating success
		1
		>>> mc.incr("counter")
		21
		>>> mc.incr("counter")
		22

		Overflow on server is not checked.  Be aware of values approaching
		2**32.  See L{decr}.

		@param delta: Integer amount to increment by (should be zero or greater).
		@return: New value after incrementing.
		@rtype: int
		"""
		return self._incrdecr("incr", key, delta)

	@returns_waitcondition
	def decr(self, key, delta=1):
		"""
		Like L{incr}, but decrements.  Unlike L{incr}, underflow is checked and
		new values are capped at 0.  If server value is 1, a decrement of 2
		returns 0, not -1.

		@param delta: Integer amount to decrement by (should be zero or greater).
		@return: New value after decrementing.
		@rtype: int
		"""
		return self._incrdecr("decr", key, delta)

	@as_coro_waitcondition
	def _incrdecr(self, cmd, key, delta):
		"""Helper function for handling incr and decr."""
		check_key(key)
		server, key = self._get_server_for(key)
		if not server:
			return

		cmd = "%s %s %d\r\n" % (cmd, key, delta)

		try:
			yield server.sendall(cmd)
			line = yield server.read_line()
			raise StopIteration(int(line))
		except socket.error, exc:
			server.mark_dead(exc[1])
			return

	@returns_waitcondition
	def add(self, key, val, expiry_time=0, min_compress_len=0):
		"""
		Add new key with value.

		Like L{set}, but only stores in memcache if the key doesn't already exist.

		@return: True on success.
		"""
		return self._set("add", key, val, expiry_time, min_compress_len)

	@returns_waitcondition
	def replace(self, key, val, expiry_time=0, min_compress_len=0):
		"""
		Replace existing key with value.

		Like L{set}, but only stores in memcache if the key already exists.
		The opposite of L{add}.

		@return: True on success.
		"""
		return self._set("replace", key, val, expiry_time, min_compress_len)

	def set(self, key, val, expiry_time=0, min_compress_len=0):
		"""
		Unconditionally sets a key to a given value in the memcache.

		The C{key} can optionally be an tuple, with the first element being the
		hash value, if you want to avoid making this module calculate a hash value.
		You may prefer, for example, to keep all of a given user's objects on the
		same memcache server, so you could use the user's unique id as the hash
		value.

		@return: Nonzero on success.
		@rtype: int

		@param expiry_time: Tells memcached the time which this value should expire, either
		as a delta number of seconds, or an absolute unix time-since-the-epoch
		value. See the memcached protocol docs section "Storage Commands"
		for more info on <exptime>. We default to 0 == cache forever.

		@param min_compress_len: The threshold length to kick in auto-compression
		of the value using the zlib.compress() routine. If the value being cached is
		a string, then the length of the string is measured, else if the value is an
		object, then the length of the pickle result is measured. If the resulting
		attempt at compression yields a larger string than the input, then it is
		discarded. For backwards compatability, this parameter defaults to 0,
		indicating don't ever try to compress.
		"""
		return self._set("set", key, val, expiry_time, min_compress_len)

	@as_coro_waitcondition
	def _set(self, cmd, key, val, expiry_time, min_compress_len = 0):
		"""Helper function for add, set, replace"""
		check_key(key)
		server, key = self._get_server_for(key)
		if not server:
			raise StopIteration(False)

		stored_info = self._value_to_stored(val, min_compress_len)
		if stored_info is None:
			# If it's not storable due to length, just return.
			raise StopIteration(True)
		flags, stored = stored_info
		

		full_cmd = "%s %s %d %d %d\r\n%s\r\n" % (cmd, key, flags, expiry_time, len(stored), stored)

		try:
			yield server.sendall(full_cmd)
			res = yield server.read_line()
			raise StopIteration(res == "STORED")

		except socket.error, exc:
			server.mark_dead(exc[1])

		raise StopIteration(False)


	def _map_keys_to_servers(self, key_iterable, key_prefix):
		"""
		For each key in data, determine which server that key should be mapped to.

		Returns a dict. Keys are _ServerConnection instances; for each server, the value is a
		list of (prefixed_key, original_key) tuples for all values which belong on that server.
		"""

		# Only check the prefix once
		key_extra_len = len(key_prefix)
		if key_prefix:
			check_key(key_prefix)

		# server -> list of (prefixed_key, value)
		server_keys = {}
		deprefix = {}

		# build up a list for each server of all the keys we want.
		for orig_key in key_iterable:
			if type(orig_key) is tuple:
				# Tuple of hashvalue, key ala _get_server_for(). The caller is essentially
				# telling us what server to stuff this on.
				str_orig_key = str(orig_key[1])
				# Ensure call to _get_server_for gets a Tuple as well.
				# Gotta pre-mangle key before hashing to a server. Returns the mangled key.
				server, key = self._get_server_for((orig_key[0], key_prefix + str_orig_key))
			else:
				str_orig_key = str(orig_key) # set_multi supports int / long keys.
				server, key = self._get_server_for(key_prefix + str_orig_key)

			# Now check to make sure key length is proper ...
			check_key(str_orig_key, key_extra_len=key_extra_len)

			if not server:
				continue

			if server not in server_keys:
				server_keys[server] = []

			server_keys[server].append((key, orig_key))
			deprefix[key] = orig_key
			

		return server_keys, deprefix

	def get_multi(self, keys, key_prefix=''):
		"""
		Retrieves multiple keys from the memcache doing just one query.

		>>> success = mc.set("foo", "bar")
		>>> success = mc.set("baz", 42)
		>>> mc.get_multi(["foo", "baz", "foobar"]) == {"foo": "bar", "baz": 42}
		True
		>>> mc.set_multi({'k1' : 1, 'k2' : 2}, key_prefix='pfx_') == []
		True

		This looks up keys 'pfx_k1', 'pfx_k2', ... . Returned dict will just have unprefixed keys 'k1', 'k2'.
		>>> mc.get_multi(['k1', 'k2', 'nonexist'], key_prefix='pfx_') == {'k1' : 1, 'k2' : 2}
		True

		get_multi (and `set_multi`) can take str()-ables like ints / longs as keys too. Such as your db pri key fields.
		They're passed through str() before being sent to memcache, with or without the use of a key_prefix.
		In this mode, the key_prefix could be a table name, and the key itself a db primary key number.

		>>> mc.set_multi({42: 'douglass adams', 46 : 'and 2 just ahead of me'}, key_prefix='numkeys_') == []
		1
		>>> mc.get_multi([46, 42], key_prefix='numkeys_') == {42: 'douglass adams', 46 : 'and 2 just ahead of me'}
		1

		This method is recommended over regular L{get} as it lowers the number of
		total packets flying around your network, reducing total latency, since
		your app doesn't have to wait for each round-trip of L{get} before sending
		the next one.

		See also L{set_multi}.

		@param keys: An array of keys.
		@param key_prefix: A string to prefix each key when we communicate with memcache.
			Facilitates pseudo-namespaces within memcache. Returned dictionary keys will not have this prefix.
		@return:  A dictionary of key/value pairs that were available. If key_prefix was provided, the keys in the retured dictionary will not have it present.

		"""

		server_keys, deprefix = self._map_keys_to_servers(keys, key_prefix)

		# send out all requests on each server before reading anything
		dead_servers = []

		for server in server_keys.iterkeys():
			try:
				server.sendall("get %s\r\n" % " ".join(
					prefixed_key for prefixed_key, _original_key in server_keys[server]
				))
			except socket.error, exc:
				server.mark_dead(exc[1])
				dead_servers.append(server)

		# if any servers died on the way, don't expect them to respond.
		for server in dead_servers:
			del server_keys[server]

		retvals = {}
		for server in server_keys.iterkeys():
			try:
				line = yield server.read_line()
				while line and line != 'END':
					if line[:5] == "VALUE":
						_resp, rkey, flags, data_len = line.split()
						value = self._parse_value(
							(yield server.read_exactly(int(data_len) + 2))[:-2],
							int(flags)
						)

						retvals[deprefix[rkey]] = value

					line = yield server.read_line()

			except socket.error, exc:
				server.mark_dead(exc)

		raise StopIteration(retvals)


	def set_multi(self, mapping, expiry_time=0, key_prefix='', min_compress_len=0):
		"""
		Sets multiple keys in the memcache doing just one query.

		>>> notset_keys = mc.set_multi({'key1' : 'val1', 'key2' : 'val2'})
		>>> mc.get_multi(['key1', 'key2']) == {'key1' : 'val1', 'key2' : 'val2'}
		True

		This method is recommended over regular L{set} as it lowers the number of
		total packets flying around your network, reducing total latency, since
		your app doesn't have to wait for each round-trip of L{set} before sending
		the next one.

		@param mapping: A dict of key/value pairs to set.
		@param time: Tells memcached the time when this value should expire, either
		as a delta number of seconds, or an absolute unix time-since-the-epoch
		value. See the memcached protocol docs section "Storage Commands"
		for more info on <exptime>. We default to 0 == cache forever.
		@param key_prefix:  Optional string to prepend to each key when sending to
		memcache. Allows you to efficiently stuff these keys into a pseudo-namespace in memcache:

			>>> notset_keys = mc.set_multi({'key1' : 'val1', 'key2' : 'val2'}, key_prefix='subspace_')
			>>> len(notset_keys) == 0
			True
			>>> mc.get_multi(['subspace_key1', 'subspace_key2']) == {'subspace_key1' : 'val1', 'subspace_key2' : 'val2'}
			True

		Causes key 'subspace_key1' and 'subspace_key2' to be set. Useful in conjunction with a higher-level layer which applies namespaces to data in memcache.
		In this case, the return result would be the list of notset original keys, prefix not applied.

		@param min_compress_len: The threshold length to kick in auto-compression
		of the value using the zlib.compress() routine. If the value being cached is
		a string, then the length of the string is measured, else if the value is an
		object, then the length of the pickle result is measured. If the resulting
		attempt at compression yeilds a larger string than the input, then it is
		discarded. For backwards compatability, this parameter defaults to 0,
		indicating don't ever try to compress.
		@return: List of keys which failed to be stored [ memcache out of memory, etc. ].
		@rtype: list
		"""

		server_keys, deprefix = self._map_keys_to_servers(mapping.iterkeys(), key_prefix)

		# send out all requests on each server before reading anything
		dead_servers = []

		for server in server_keys.iterkeys():
			commands = []
			for prefixed_key, original_key in server_keys[server]:
				stored_info = self._value_to_stored(mapping[original_key], min_compress_len)
				if stored_info is None:
					# If it's not storable due to length, just ignore it
					continue

				flags, stored = stored_info
				commands.append("set %s %d %d %d\r\n%s\r\n" % (
					prefixed_key,
					flags,
					expiry_time,
					len(stored),
					stored
				))

			try:
				server.send_cmds(''.join(commands))
			except socket.error, exc:
				server.mark_dead(exc[1])
				dead_servers.append(server)

		# if any servers died on the way, don't expect them to respond.
		for server in dead_servers:
			del server_keys[server]

		notstored = [] # original keys.
		for server, keys in server_keys.iteritems():
			try:
				for key in keys:
					line = server.read_line()
					if line == 'STORED':
						continue
					else:
						notstored.append(deprefix[key]) #un-mangle.
			except socket.error, exc:
				server.mark_dead(exc)

		raise StopIteration(notstored)

	def delete_multi(self, keys, dead_time=0, key_prefix=''):
		"""
		Delete multiple keys in the memcache doing just one query.

		>>> notset_keys = mc.set_multi({'key1' : 'val1', 'key2' : 'val2'})
		>>> mc.get_multi(['key1', 'key2']) == {'key1' : 'val1', 'key2' : 'val2'}
		True
		>>> mc.delete_multi(['key1', 'key2'])
		True
		>>> mc.get_multi(['key1', 'key2']) == {}
		True

		This method is recommended over iterated regular L{delete}s as it reduces
		total latency, since your app does not have to wait for each round-trip of
		L{delete} before sending the next one.

		@param keys: An iterable of keys to clear
		@param dead_time: number of seconds any subsequent set / update commands should fail. Defaults to 0 for no delay.
		@param key_prefix:  Optional string to prepend to each key when sending to memcache.
		See docs for L{get_multi} and L{set_multi}.

		@return: True if no failure in communication with any memcacheds.
		@rtype: bool
		"""

		server_keys, _deprefix = self._map_keys_to_servers(keys, key_prefix)

		# send out all requests on each server before reading anything
		dead_servers = []

		if dead_time is None:
			dead_time = 0

		ret = True

		for server in server_keys.iterkeys():
			commands = []
			for prefixed_key, _original_key in server_keys[server]:
				commands.append("delete %s %d\r\n" % (prefixed_key, dead_time))

			try:
				server.send_cmds(''.join(commands))
			except socket.error, exc:
				server.mark_dead(exc[1])
				dead_servers.append(server)

		# if any servers died on the way, don't expect them to respond.
		for server in dead_servers:
			del server_keys[server]

		for server, keys in server_keys.iteritems():
			try:
				for _key in keys:
					res = yield server.read_line()
					if res != "DELETED":
						self._debuglog("expected 'DELETED', got %r" % (res, ))
			except socket.error, exc:
				server.mark_dead(exc)
				ret = False

		raise StopIteration(ret)

	@staticmethod
	def _value_to_stored(value, min_compress_len):
		"""
		Transform value to a storable representation, returning a tuple of the flags and the new value.
		"""

		flags = 0
		if isinstance(value, str):
			pass
		elif isinstance(value, int):
			flags |= Client._FLAG_INTEGER
			value = "%d" % value
			# Don't try to compress it
			min_compress_len = 0
		elif isinstance(value, long):
			flags |= Client._FLAG_LONG
			value = "%d" % value
			# Don't try to compress it
			min_compress_len = 0
		else:
			flags |= Client._FLAG_PICKLE
			value = pickle.dumps(value, 0)

		# silently do not store if value length exceeds maximum
		if len(value) >= SERVER_MAX_VALUE_LENGTH:
			return None

		if min_compress_len and _SUPPORTS_COMPRESS and len(value) > min_compress_len:
			# Try compressing
			compressed_value = compress(value)

			#Only retain the result if the compression result is smaller than the original.
			if len(compressed_value) < len(value):
				flags |= Client._FLAG_COMPRESSED
				value = compressed_value

		return flags, value

	@as_coro_waitcondition
	def get(self, key):
		'''Retrieves a key from the memcache.

		@return: The value or None.
		'''
		check_key(key)
		server, key = self._get_server_for(key)
		if not server:
			raise StopIteration(None)

		try:
			yield server.sendall("get %s\r\n" % key)
			line = yield server.read_line()
			if line[:5] == "VALUE":
				_resp, _rkey, flags, data_len = line.split()
				value = self._parse_value(
					(yield server.read_exactly(int(data_len) + 2))[:-2],
					int(flags)
				)

				res = yield server.read_line()
				if res != "END":
					self._debuglog("expected 'END', got %r" % (res, ))
			else:
				value = None

		except socket.error, msg:
			if type(msg) is tuple:
				msg = msg[1]
			server.mark_dead(msg)
			raise StopIteration(None)

		raise StopIteration(value)


	def _parse_value(self, data, flags):
		"""Return the object that was orignally stored based on its data and flags."""

		if flags & Client._FLAG_COMPRESSED:
			data = decompress(data)

		if  flags == 0 or flags == Client._FLAG_COMPRESSED:
			# Either a bare string or a compressed string now decompressed...
			value = data
		elif flags & Client._FLAG_INTEGER:
			value = int(data)
		elif flags & Client._FLAG_LONG:
			value = long(data)
		elif flags & Client._FLAG_PICKLE:
			try:
				value = pickle.loads(data)
			except Exception:
				self._debuglog('Pickle error...\n')
				value = None
		else:
			self._debuglog("unknown flags on get: %x\n" % flags)

		return value


class _ServerConnection(tcp.TCPConnection):
	"""A connection to a single memcached server."""

	_DEAD_RETRY = 30  # number of seconds before retrying a dead server.

	def __init__(self, host, weight, debugfunc):
		"""Initialize the _ServerConnection to the specified host."""

		self.weight = weight

		if ":" in host:
			host = host.split(":")
			self.addr = (host[0], int(host[1]))
		else:
			self.addr = (host, 11211)

		self.debuglog = debugfunc

		self.deaduntil = 0

		sock = socket.socket()
		sock.connect(self.addr)

		tcp.TCPConnection.__init__(self, sock, None)

	def _check_dead(self):
		if self.deaduntil and self.deaduntil > time.time():
			return True

		self.deaduntil = None
		return False

	def mark_dead(self, reason):
		self.debuglog("MemCache: %s: %s.  Marking dead." % (self, reason))
		self.deaduntil = time.time() + self._DEAD_RETRY
		self.close()

	@as_coro_waitcondition
	def get_stats(self):
		"""
		Get statistics from the server.

		@return: A tuple (server_identifier, stats_dictionary).
		The dictionary contains a number of name/value pairs specifying
		the name of the status field and the string value associated with
		it.  The values are not converted from strings.
		"""

		server_data = {}

		yield self.sendall("stats\r\n")

		while True:
			line = yield self.read_line()

			if not line or line.strip() == "END":
				break

			_stat, name, value = line.split(' ', 2)
			server_data[name] = value

		raise StopIteration(server_data)

	def __str__(self):
		dead = ''
		if self.deaduntil:
			dead = " (dead until %d)" % self.deaduntil

		return "<memcached._ServerConnection %s:%d%s>" % (self.addr[0], self.addr[1], dead)

	def connection_handler(self):
		"""
		Main connection loop.

		We don't need to do aything here; all events are triggered from externally.
		"""
		yield

def check_key(key, key_extra_len=0):
	"""
	Checks sanity of key.  Fails if:

	- Key length is > SERVER_MAX_KEY_LENGTH (Raises ValueError)
	- Contains control characters  (Raises ValueError)
	- Is not a string (Raises TypeError)
	"""

	if not isinstance(key, str):
		raise TypeError("Keys must be of type str, not unicode.")

	if len(key) + key_extra_len > SERVER_MAX_KEY_LENGTH:
		raise ValueError("Key length is > %s" % SERVER_MAX_KEY_LENGTH)

	for char in key:
		if ord(char) < 33:
			raise ValueError("Control characters not allowed")

def _doctest():
	import doctest, memcache
	servers = ["127.0.0.1:11211"]
	connection = Client(servers, debug=True)
	globs = {"mc": connection}
	return doctest.testmod(memcache, globs=globs)



import unittest

@decorator
def _coro_test_wrapper(func, self):
	"""Run the test to set things up, then start the reactor."""
	coro = Coroutine(func(self), is_watched = True)
	reactor.run()

	if coro.state != coro.STATE_COMPLETED:
		exc_type, exc_value, exc_traceback = coro.result[1]
		raise exc_type, exc_value, exc_traceback

class _MemcachedTests(unittest.TestCase):
	def setUp(self):
		"""Set up the connection"""
		self.conn = Client(["127.0.0.1:11211"], debug = 1)

	@as_coro_waitcondition
	def check_setget(self, key, value):
		"""Check that getting, setting, and deleting an object work as expected."""

		yield self.conn.set(key, value)

		new_value = yield self.conn.get(key)
		self.assertEqual(new_value, value)

		self.assert_((yield self.conn.delete(key)))

		new_value = yield self.conn.get(key)
		self.assertEqual(new_value, None)

	@_coro_test_wrapper
	def test_basic_types(self):
		"""Test set, get, and delete commands on strings, numbers, and objects"""
		yield self.check_setget("a_string", "some random string")
		yield self.check_setget("an_integer", 42)
		yield self.check_setget("a_long", long(1<<30))
		yield self.check_setget("a_dict", { "foo" : "bar", "baz" : "quux" })

	@_coro_test_wrapper
	def test_unknown(self):
		"""Test that getting an undefined value results in None"""
		unknown_value = yield self.conn.get("unknown_value")
		self.assertEqual(unknown_value, None)

	@_coro_test_wrapper
	def test_incrdecr(self):
		"""Test incr and decr functions"""
		yield self.conn.set("an_integer", 42)

		self.assertEqual((yield self.conn.incr("an_integer", 1)), 43)
		self.assertEqual((yield self.conn.decr("an_integer", 1)), 42)

	@_coro_test_wrapper
	def test_invalid_keys(self):
		"""Check that invalid keys raise the appropriate exception"""

		try:
			yield self.conn.set("this has spaces", 1)
		except ValueError:
			pass
		else:
			self.fail("key with spaces did not raise ValueError")

		try:
			yield self.conn.set("\x10control\x02characters\x11", 1)
		except ValueError:
			pass
		else:
			self.fail("key with control characters did not raise ValueError")

		try:
			yield self.conn.set("a" * (SERVER_MAX_KEY_LENGTH + 1), 1)
		except ValueError:
			pass
		else:
			self.fail("long key did not raise ValueError")

		try:
			yield self.conn.set(u"unicode\u4f1a", 1)
		except TypeError:
			pass
		else:
			self.fail("unicode key did not raise ValueError")

	@_coro_test_wrapper
	def test_get_multi(self):
		"""Check that get_multi works as expected"""
		yield self.conn.set("an_integer", 42)
		yield self.conn.set("a_string", "hello")

		res = yield self.conn.get_multi([ "a_string", "an_integer" ])

		self.assertEquals(res, { "a_string": "hello", "an_integer": 42 })

	@_coro_test_wrapper
	def test_large_value(self):
		"""Check that we do not attempt to send values larger than the server maximum"""
		yield self.conn.set("big_string", "a" * SERVER_MAX_VALUE_LENGTH)
		res = yield self.conn.get("big_string")

		self.assertEquals(res, None)

if __name__ == "__main__":
	print "Testing docstrings..."
	#_doctest()
	print "Running tests:"
	unittest.main()
