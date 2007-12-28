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
	getattr(libc, "sendfile")
except AttributeError:
	raise ImportError("sendfile not available on this system")

def _sendfile4(out_file, in_file, offset, count):
	"""
	Wrapper for Linux sendfile(2) system call.

	out_file must be a socket; in_file must be a regular file object.
	Returns the number of bytes actually written.
	"""

	offset = ctypes.c_int(offset)

	ret = libc.sendfile(out_file.fileno(), in_file.fileno(), ctypes.byref(offset), int(count))

	if ret < 0:
		err = ctypes.c_int.in_dll(libc, "errno").value
		raise OSError(err, os.strerror(err))

	return ret

def _sendfile6(out_file, in_file, offset, count):
	"""
	Wrapper for FreeBSD sendfile(2) system call.

	out_file must be a socket; in_file must be a regular file object.
	Returns the number of bytes actually written.
	"""

	count = int(count)
	if count == 0:
		return 0

	sbytes = ctypes.c_int()

	ret = libc.sendfile(
		out_file.fileno(),
		in_file.fileno(),
		int(offset),
		int(count),
		None,
		ctypes.byref(sbytes),
		0
	)

	if ret < 0:
		err = ctypes.c_int.in_dll(libc, "errno").value
		raise OSError(err, os.strerror(err))

	return sbytes.value

if os.uname()[0] == "Linux":
	sendfile = _sendfile4
elif os.uname()[0] == "FreeBSD":
	sendfile = _sendfile6
else:
	raise ImportError("Unknown OS.")

__all__ = [ "sendfile" ]
