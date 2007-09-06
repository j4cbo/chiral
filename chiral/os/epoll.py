"""
epoll() wrapper using ctypes
"""

# Chiral, copyright (c) 2007 Jacob Potter
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 2.

import ctypes
from ctypes.util import find_library
import os

libc = ctypes.CDLL(find_library("c"))

try:
	getattr(libc, "epoll_create")
except AttributeError:
	raise ImportError("epoll not available on this system")

class _epoll_data(ctypes.Union):
	"""union epoll_data"""
	_fields_ = [
		("ptr", ctypes.c_void_p),
		("fd", ctypes.c_ulong),
		("u32", ctypes.c_ulong),
		("u64", ctypes.c_ulonglong)
	]

class _epoll_event(ctypes.Structure):
	"""struct epoll_event"""
	_fields_ = [
		("events", ctypes.c_ulong),
		("data", _epoll_data)
	]

class Epoll(object):
	"""Wrapper around Linux's epoll() system calls."""

	def __init__(self, size=10):
		"""Constructor."""

		self.epoll_fd = libc.epoll_create(size)

	def ctl(self, op, fd, events):
		"""Modify the given event.

		op, fd, and events are as per the epoll_ctl(2) man page. The epoll_data union
		stores the file descriptor number; it is not available for user data.
		"""

		event = _epoll_event(
			int(events),
			_epoll_data(fd = int(fd))
		)

		libc.epoll_ctl(self.epoll_fd, int(op), int(fd), ctypes.byref(event))


	def wait(self, return_count = 10, timeout = None):
		"""
		Calls epoll_wait().

		Returns a list of up to return_count tuples of (events, fd).
		"""

		if timeout is None:
			# Indefinite timeout
			timeout = -1
		else:
			# Convert to milliseconds
			if timeout < 0:
				timeout = 0
			timeout = int(timeout * 1000)

		event_buffer = (_epoll_event * return_count)()

		ret = libc.epoll_wait(
			self.epoll_fd,
			event_buffer,
			return_count,
			timeout
		)

		if ret < 0:
			err = ctypes.c_int.in_dll(libc, "errno").value
			raise OSError(err, os.strerror(err))

		output = []

		for index in xrange(ret):
			event_struct = event_buffer[index]
			output.append((
				event_struct.events,
				event_struct.data.fd
			))

		return output

__all__ = [
	"Epoll"
]

# From sys/epoll.h
EVENTS = {
	"EPOLLIN": 0x001,
	"EPOLLPRI": 0x002,
	"EPOLLOUT": 0x004,
	"EPOLLRDNORM": 0x040,
	"EPOLLRDBAND": 0x080,
	"EPOLLWRNORM": 0x100,
	"EPOLLWRBAND": 0x200,
	"EPOLLMSG": 0x400,
	"EPOLLERR": 0x008,
	"EPOLLHUP": 0x010,
	"EPOLLONESHOT": (1 << 30),
	"EPOLLET": (1 << 31)
}

OPS = {
	"EPOLL_CTL_ADD": 1,
	"EPOLL_CTL_DEL": 2,
	"EPOLL_CTL_MOD": 3
}

for key, value in EVENTS.items() + OPS.items():
	locals()[key] = value
	__all__.append(key)

del key, value
