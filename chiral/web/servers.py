"""Web servers for use with chiral.web.httpd"""

from paste.httpexceptions import *
from paste.httpheaders import *

from rfc822 import formatdate
import mimetypes
import os
import errno
import stat

_CHIRAL_RELOADABLE = True

class StaticFileServer(object):

	def __init__(self, path):
		self.path = path

	def __call__(self, environ, start_response):

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
