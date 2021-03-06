"""
Chiral Introspector.

Introduction
------------

Chiral defines an interface-neutral way for classes and modules to allow themselves to be
queried and modified at runtime. The reference implementation of this is `chiral.web.introspector`,
which provides a lightweight Web interface, but it is intended that other mechanisms (such as
command-line) could be included as well.

Modules mark themselves as introspectable by defining a ``_chiral_introspection`` class. When the
introspector's main page is loaded, it searches through ``sys.modules`` for all introspectable
modules; the output of ``_chiral_introspection().main()`` will be rendered and included in the
introspection index.

``chiral.web.introspector`` also provides an interface to `chiral.core.xreload` for reloading modules
on the fly. If a module has a _CHIRAL_RELOADABLE attribute that evaluates to True, a "Reload Now" link
will be provided for the module.

Formatting
----------

``_chiral_introspection`` routines return data in an interface agnostic Python-object format, which
the introspector then formats safely into HTML. Objects are converted recursively, as such:

``tuple``
	Each member of the tuple is processed, and the results are concatenated.
``list``
	Each entry is processed, and the results are formatted as a bulleted list (``<ul>``)
``dict``, ``UserDict``
	Each value is processed; the output is formatted as a bulleted list (``<ul>``) of "key: value" entries.
``basestring``
	Strings are rendered depending on their contents:

	- An empty string becomes a line break (``<br/>``)
	-
	  Strings starting with "@" become buttons. The string should be of the format "@module:command:item:label".
	  Clicking the button will cause the function ``cmd_(command)`` in ``module``'s ``_chiral_introspection``
	  to be executed, with ``item`` as a parameter.
	- Other strings are shown as-is.
``object``
	Other objects will be checked for a ``_chiral_introspect`` attribute. If the attribute exists, it will be
	called, and expected to return a ``(funcname, item)`` tuple. The object is then rendered as a link;
	clicking the link will run ``_chiral_introspection.(funcname)`` in whichever module the object is defined
	in. The object's ``repr()`` appears as the link text.


Object Introspection
--------------------

As mentioned above, objects with a _chiral_introspect attribute will produce a link that maps to a function
in _chiral_introspection. The output of this function will produce its own page; this can be used by modules
that keep track of all instances of some class (such as `chiral.core.coroutine`) to provide detailed introspection
information for each object.
"""

# Chiral, copyright (c) 2007 Jacob Potter
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 2.

import cgi
import sys
import gc
import types
import traceback
import weakref

from chiral.core import xreload, coroutine
from chiral.net import reactor

from paste import request

_CHIRAL_RELOADABLE = True

class Introspector(object):
	"""WSGI application providing the Chiral Introspector."""

	def htmlize(self, obj, rooturl):
		"""Recursively format obj as HTML."""

		if isinstance(obj, basestring):
			if obj.startswith("@"):

				# Button link...
				try:
					mod, ns, objid, value = obj[1:].split(":")
				except ValueError:
					pass
				else:
					target_url = rooturl + mod + "/" + ns + "/" + objid
					fl = "<form method=\"post\" action=\"" + cgi.escape(target_url, True)
					fl += "\"><input type=\"submit\" name=\"cmd\" value=\""
					fl += cgi.escape(value, True) + "\"/></form>"
					return fl

			if obj == '':
				return "<br/>"

			return cgi.escape(obj)

		if isinstance(obj, list):
			out = "</li><li>".join(self.htmlize(item, rooturl) for item in obj)
			return "<ul><li>" + out + "</li></ul>"

		if isinstance(obj, tuple):
			out = "".join(self.htmlize(item, rooturl) for item in obj)
			return out
			#return "<p>" + out + "</p>"

		if isinstance(obj, dict) or isinstance(obj, weakref.WeakValueDictionary):
			out = "</li><li>".join(
				"<b>" + cgi.escape(repr(k)) + "</b>: " + self.htmlize(obj[k], rooturl)
				for k
				in sorted(obj.iterkeys())
			)
			return "<ul><li>" + out + "</li></ul>"

		if hasattr(obj, '_chiral_introspect') \
		   and hasattr(obj._chiral_introspect, 'im_self') \
		   and obj._chiral_introspect.im_self is not None:
			return "<a href=\"" + cgi.escape(
				rooturl + obj._chiral_introspect.__module__ + ("/%s/%s" % obj._chiral_introspect())
			) + "\">" + cgi.escape(repr(obj)) + "</a>"

		return "<i>" + cgi.escape(repr(obj)) + "</i>"

	def introspect(self, base_url, module, namespace, item=None):
		try:
			ifunc = getattr(sys.modules[module]._chiral_introspection(), namespace)
		except AttributeError:
			return None

		item_data = ifunc(item)

		if item_data is None:
			return None

		if not isinstance(item_data, tuple):
			item_data = (item_data, )

		if item is None: item = ""
		cr_string = "%s: %s %s" % (
			cgi.escape(module), cgi.escape(namespace), cgi.escape(item)
		)

		out_string = "<html><head><style type='text/css'>p { margin:0; } form{display:inline}</style><title>%s</title></head><body><h1>%s</h1><p>%s</p></body></html>" % (
			cr_string,
			cr_string,
			"</p><p>".join(
				self.htmlize(i, base_url)
				for i
				in item_data
			)
		)
		del item_data

		return out_string


	def __call__(self, environ, start_response):
		"""Run the WSGI application."""
		path_info = environ.get('PATH_INFO', '')
		url = request.construct_url(environ, with_query_string=False)

		# Redirect /introspector to /introspector/ to ensure consistent URLs
		if path_info == '':
			start_response('302 Found', [('Location', url + '/')])
			return [ '' ]

		# Index page
		if path_info == '/':
			start_response('200 OK', [('Content-Type', 'text/html')])
			return [ self.introspect(url, "chiral.web.introspector", "index") ]

		# Parse the URL: [/introspector/]module/namespace/item
		path = path_info.split('/')[1:]
		if len(path) < 3:
			start_response('404 Not Found', [('Content-Type', 'text/html')])
			return [ "404 Not Found" ]

		module, namespace, item = path
		script_name = environ.get('SCRIPT_NAME', '') + "/"

		if module not in sys.modules or not hasattr(sys.modules[module], '_chiral_introspection'):
			start_response('404 Not Found', [('Content-Type', 'text/html')])
			return [ "404 Not Found" ]

		# Commands are slightly different: they must be POST, and the namespace has "cmd_" at the beginning
		if environ["REQUEST_METHOD"] == "POST":
			try:
				ifunc = getattr(sys.modules[module]._chiral_introspection(), "cmd_" + namespace)
			except AttributeError:
				start_response('404 Not Found', [('Content-Type', 'text/html')])
				return [ "404 Not Found" ]

			next_url = ifunc(item)
			start_response('302 Found', [('Location', script_name + next_url)])
			return [ "" ]

		# Prevent shenanigans involving commands sent as GET
		if namespace.startswith("cmd_"):
			start_response('404 Not Found', [('Content-Type', 'text/html')])
			return [ "404 Not Found" ]
					
		out_string = self.introspect(environ.get('SCRIPT_NAME', '') + '/', module, namespace, item)

		if out_string is None:
			start_response('404 Not Found', [('Content-Type', 'text/html')])
			return [ "404 Not Found" ]

		start_response('200 OK', [('Content-Type', 'text/html')])
		return [ out_string ]


class _chiral_introspection(object):
	def main(self):
		return [ ( 
			"Garbage collector: %d objects tracked; " % len(gc.get_objects()),
			"@chiral.web.introspector:gc:collect:Collect Now"
		) ]

	def index(self, item=None):
		module_list = {} 
		for modname in sorted(sys.modules.iterkeys()):
			if "chiral" not in modname or sys.modules[modname] is None:
				continue
			mod = sys.modules[modname]

			m = [ ]

			if hasattr(mod, '_CHIRAL_RELOADABLE'):
				item = ("Source: %s - " % mod.__file__, "@chiral.web.introspector:reload:%s:Reload Now" % modname)
				if hasattr(mod, '__chiral_reload_count__'):
					item = (
						item[0], item[1],
						"; reload count: %d" % mod.__chiral_reload_count__
					)

				m.append(item)

			if hasattr(mod, '_chiral_introspection'):
				introspection = mod._chiral_introspection()
				if hasattr(introspection, "main"):
					m.extend(introspection.main())

			if len(m) == 0:
				continue

			module_list[modname] = m
			
		return module_list

	def cmd_reload(self, module):
		mod = sys.modules[module]
		print "Reloading %s" % (mod, )
		xreload.xreload(mod)

		return ""

	def cmd_gc(self, xgc):
		gc.collect()
		return ""
