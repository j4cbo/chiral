"""
kqueue() wrapper using ctypes
"""

# Chiral, copyright (c) 2007 Jacob Potter
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 2.

import ctypes
from ctypes.util import find_library
import os

try:
	libc = ctypes.CDLL(find_library("c"))
	getattr(libc, "kqueue")
except AttributeError, TypeError:
	raise ImportError("kqueue not available on this system")

class _kevent(ctypes.Structure):
	"""struct kevent"""
	_fields_ = [
		("ident", ctypes.c_ulong),
		("filter", ctypes.c_short),
		("flags", ctypes.c_ushort),
		("fflags", ctypes.c_uint),
		("data", ctypes.c_long),
		("udata", ctypes.c_void_p)
	]

class Timespec(ctypes.Structure):
	"""struct timespec"""
	_fields_ = [
		("tv_sec", ctypes.c_long),
		("tv_nsec", ctypes.c_long),
	]

class Kqueue(object):
	"""Wrapper around BSD's kqueue() system calls."""

	def __init__(self):
		"""Constructor."""

		self.queue_fd = libc.kqueue()

	def change_events(self, *events):
		"""Modify the given events.

		Each parameter should be a tuple, as such:
		(ident, filter, flags, fflags, data, udata) 
		"""

		self.kevent(events, 0)


	def kevent(self, events, return_count = 10, timeout = 0):
		"""
		Lightweight wrapper for kevent().

		Each item in events should be a tuple, as such:
		(ident, filter, flags, fflags, data, udata) 
		"""

		if events is None:
			events = ()

		event_buffer = (_kevent * max(return_count, len(events)))()

		for index, event_tuple in enumerate(events):
			ident, efilter, flags, fflags, data, udata = event_tuple

			assert efilter in FILTERS.values()

			if udata is None:
				udata = 0

			if data is None:
				data = 0

			event_buffer[index] = _kevent(
				ident = ident,
				filter = efilter,
				flags = flags,
				fflags = fflags,
				data = data,
				udata = ctypes.c_void_p(udata)
			)

		if timeout is not None:
			timeout = Timespec(
				int(timeout),
				int((timeout - int(timeout)) * 10**9)
			)

		ret = libc.kevent(
			self.queue_fd,
			event_buffer,
			len(events),
			event_buffer,
			return_count,
			ctypes.byref(timeout) if timeout is not None else timeout
		)

		if ret < 0:
			err = ctypes.c_int.in_dll(libc, "errno").value
			raise OSError(err, os.strerror(err))

		output = []

		for index in xrange(ret):
			event_struct = event_buffer[index]
			output.append((
				event_struct.ident,
				event_struct.filter,
				event_struct.flags,
				event_struct.fflags,
				event_struct.data,
				event_struct.udata
			))

		return output

__all__ = [
	"Kqueue"
]

# From sys/event.h
FILTERS = {
	# Filters
	"EVFILT_READ": (-1),
	"EVFILT_WRITE": (-2),
	"EVFILT_AIO": (-3),
	"EVFILT_VNODE": (-4),
	"EVFILT_PROC": (-5),
	"EVFILT_SIGNAL": (-6),
	"EVFILT_TIMER": (-7),
	"EVFILT_MACHPORT": (-8),
	"EVFILT_FS": (-9)
}

FLAGS = {
	"EV_ADD": 0x0001,	# add event to kq (implies enable)
	"EV_DELETE": 0x0002,	# delete event from kq
	"EV_ENABLE": 0x0004,	# enable event
	"EV_DISABLE": 0x0008,	# disable event (not reported)
	"EV_ONESHOT": 0x0010,	# only report one occurrence
	"EV_CLEAR": 0x0020,	# clear event state after reporting
	"EV_SYSFLAGS": 0xF000,	# reserved by system
	"EV_FLAG0": 0x1000,	# filter-specific flag
	"EV_FLAG1": 0x2000,	# filter-specific flag
	"EV_EOF": 0x8000,	# EOF detected
	"EV_ERROR": 0x4000,	# error, data contains errno
}

for key, value in FILTERS.items() + FLAGS.items():
	locals()[key] = value
	__all__.append(key)

del key, value
