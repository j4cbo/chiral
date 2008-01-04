"""
Web servers for use with chiral.web.httpd

Currently this implements `StaticFileServer`, a lightweight, high-performance file server.
"""

# Chiral, copyright (c) 2007 Jacob Potter
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 2.

from paste.httpheaders import IF_MODIFIED_SINCE

from rfc822 import formatdate
import mimetypes
import os
import errno
import stat

_CHIRAL_RELOADABLE = True

class StaticFileServer(object):
	"""
	A static file server.

	`StaticFileServer` provides simple file serving. It serves Last-Modified information,
	respects ``If-Modified-Since``, and uses ``wsgi.file_wrapper`` for high performance; it is
	intended that Chiral applications will not require a separate HTTP daemon to serve static
	content.

	A ``StaticFileServer`` instance is itself a WSGI callable, suitable for use directly with
	HTTPServer or with other WSGI middleware.
	"""

	def __init__(self, path):
		"""
		Constructor.

		:param path: The filesystem path to serve files out of.
		"""
		self.path = path

	def __call__(self, environ, start_response):
		"""WSGI callable."""

		# Split the path into components, make sure there aren't any .., and rejoin
		path_components = environ.get('PATH_INFO', '/').split("/")
		for item in path_components:
			if item == "..":
				start_response("403 Forbidden", [])
				return [
					"<html><head><title>403 Forbidden</title></head>"
					"<body><h1>403 Forbidden</h1></body></html>"
				]

		filename = os.path.join(self.path, *path_components)

		try:
			file_stats = os.stat(filename)
		except OSError, exc:
			if exc.errno == errno.ENOENT:
				start_response("404 Not Found", [])
				return [
					"<html><head><title>404 Not Found</title></head>"
					"<body><h1>404 Not Found</h1></body></html>"
				]
			else:
				raise exc

		if IF_MODIFIED_SINCE.parse(environ) >= file_stats.st_mtime:
			start_response("304 Not Modified", [])
			return [ "" ]

		try:
			rfile = file(filename)
		except IOError, exc:
			# We don't support directory listings.
			if exc.errno in (errno.EACCES, errno.EISDIR):
				start_response("403 Forbidden", [])
				return [
					"<html><head><title>403 Forbidden</title></head>"
					"<body><h1>403 Forbidden</h1></body></html>"
				]
			else:
				raise exc

		response_tuples = [
			('Content-Length', file_stats[stat.ST_SIZE]),
			('Last-Modified', formatdate(file_stats[stat.ST_MTIME]))
		]

		# Try to guess the type and encoding based on the filename
		file_type, file_encoding = mimetypes.guess_type(path_components[-1])
		if file_type:
			response_tuples.append(("Content-Type", file_type))
		if file_encoding:
			response_tuples.append(("Content-Encoding", file_encoding))

		# Read the file and send chunks out.
		start_response('200 OK', response_tuples)

		# Use file_wrapper if possible.
		if 'wsgi.file_wrapper' in environ:
			return environ['wsgi.file_wrapper'](rfile)
		else:
			return iter(lambda: rfile.read(4096), '')
