"""Chiral Introspector."""

# Chiral, copyright (c) 2007 Jacob Potter
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 2.

import sys
import gc
import types
import traceback

from chiral.core import xreload, coroutine
from chiral.net import reactor

from paste import request

from genshi.template import MarkupTemplate

_CHIRAL_RELOADABLE = True

INTROSPECTOR_ROOT_TEMPLATE = """
<html xmlns:py="http://genshi.edgewall.org/">
 <head>
  <title>Chiral Introspector</title>
  <style type="text/css">
   form {
    display: inline;
   }
  </style>
 </head>
 <body>

  <div py:def="referrers(_referrers_object, _exclusion_list, _recursion_depth = 0)" py:strip="True">

   <?python
	def name_in_frame(frame, obj):
		var_name = "name not found"
		for k, v in frame.f_locals.iteritems():
			if v is obj:
				var_name = "as %s in locals" % (k, )
		else:
			for k, v in frame.f_globals.iteritems():
				if v is obj:
					var_name = "as %s in globals" % (k, )

		return var_name
   ?>

   <a href="#" onclick="this.nextSibling.style.display = 'block'">+</a><ul style="display: none">
    <?python
	_referrers_list = [
		ref
		for ref
		in gc.get_referrers(_referrers_object)
		if (
			ref not in _exclusion_list and
			not (type(ref) == types.FrameType and 'Genshi' in ref.f_code.co_filename) and
			not (type(ref) == dict and ('_referrers_object' in ref or '_i_coro' in ref))
		)
	]
	del ref
	_referrers_list.sort(key = id)
    ?>

    <li py:for="ref in _referrers_list">
     ${repr(ref)}
     <i py:if="type(ref) == types.FrameType">
      ${ref.f_code.co_filename}:${ref.f_code.co_firstlineno} - ${ref.f_lineno};
      ${name_in_frame(ref, _referrers_object)}
     </i>

     <pre py:if="type(ref) == types.TracebackType">${traceback.format_tb(ref)}</pre>

     ${referrers(ref, _exclusion_list + [ _referrers_list ], _recursion_depth - 1) if _recursion_depth > 0 else None}
    </li>
   </ul>
  </div>

  <h1><a href="${rooturl}">Chiral Introspector</a></h1>

  <h2>Garbage Collector</h2>
  <form method="post" action="${rooturl}">
   <p>${len(gc.get_objects())} objects tracked.</p>
   <input type="hidden" name="action" value="gc"/>
   <input type="submit" value="Collect Now"/>
  </form>
  <p py:if="gc_collected is not None">Collected ${gc_collected} objects.</p>
  <p>Collection counts: ${ "%d, %d, %d" % gc.get_count() }</p>
  <p>Garbage: ${gc.garbage}</p>

  <h2>Coroutines</h2>
  <p>${len(list(coro for coro in coroutine._COROUTINES.valuerefs() if coro))} coroutines.</p>
  <ul>
   <?python
	coro_list = coroutine._COROUTINES.values()
	coro_list.sort(key = id)
   ?>
   <li py:for="_i_coro in coro_list">
    <a href="${rooturl}coroutine?id=${id(_i_coro)}">${repr(_i_coro)}</a>:
    ${referrers(_i_coro, [ coro_list ])}
   </li>
   <?python del coro_list ?>
  </ul>

  <h2>Reactor</h2>
  <p>Applications:</p>
  <ul>
   <li py:for="app in reactor.applications">${repr(app)}</li>
  </ul>

  <h2>Modules</h2>
  <ul>
   <li py:for="mod, modname in mod_list">
    <form method="post" action="${rooturl}">
     <input type="hidden" name="mod" value="${modname}"/>
     <b>${modname}</b>: ${mod.__file__}
     <span py:if="hasattr(mod, '_CHIRAL_RELOADABLE')">
      <input type="hidden" name="action" value="reload"/>
      <input type="submit" value="Reload Now"/>
     </span>
     <span py:if="hasattr(mod, '__chiral_reload_count__')">
      Reload count: ${mod.__chiral_reload_count__}
     </span>
    </form>
   </li>

  </ul>
 </body>
</html>
"""

INTROSPECTOR_COROUTINE_TEMPLATE = """
<html xmlns:py="http://genshi.edgewall.org/">
 <head>
  <title>Chiral Introspector</title>
 </head>
 <body>

  <h1>Coroutine ${id(coro)}</h1>
  <p>repr(): ${repr(coro)}</p>

  <p>Referrers:</p>
  <ul>
   <li py:for="ref in gc.get_referrers(coro)">${repr(ref)}</li>
  </ul>

  <p>dir():</p>
  <ul>
   <li py:for="key in dir(coro)"><b>${key}</b>: ${repr(getattr(coro, key))}</li>
  </ul>

 </body>
</html>
"""

INTROSPECTOR_TEST_TEMPLATE = """
<html xmlns:py="http://genshi.edgewall.org/">
 <head>
  <title>Chiral Introspector</title>
 </head>
 <body>
  <h1>Test</h1>
  <p>This is a test of Genshi output.</p>
 </body>
</html>
"""

class Introspector(object):
	"""WSGI application providing the Chiral Introspector."""

	root_template = MarkupTemplate(INTROSPECTOR_ROOT_TEMPLATE)
	coroutine_template = MarkupTemplate(INTROSPECTOR_COROUTINE_TEMPLATE)
	test_template = MarkupTemplate(INTROSPECTOR_TEST_TEMPLATE)

	def __init__(self, next_application = None):
		self.next_application = next_application

	def __call__(self, environ, start_response):
		"""Run the WSGI application."""
		path_info = environ.get('PATH_INFO', '')
		url = request.construct_url(environ, with_query_string=False)

		if path_info == '':
			start_response('302 Found', [('Location', url + '/')])
			return [ '' ]

		elif path_info == '/':
			req = request.parse_formvars(environ)

			if "action" in req and req["action"] == "reload" and "mod" in req:
				mod = sys.modules[req["mod"]]
				print "Reloading %s" % (mod, )
				xreload.xreload(mod)
				start_response('302 Found', [('Location', url)])
				return [ '' ]

			if "action" in req and req["action"] == "gc":
				gc_collected = gc.collect()
			else:
				gc_collected = None

			mod_list = (
				(sys.modules[modname], modname)
				for modname
				in sorted(sys.modules.iterkeys())
				if "chiral" in modname and sys.modules[modname] is not None
			)

			template_stream = self.root_template.generate(
				rooturl = url,
				gc = gc,
				traceback = traceback,
				types = types,
				gc_collected = gc_collected,
				coroutine = coroutine,
				reactor = reactor,
				mod_list = mod_list
			)

			start_response('200 OK', [('Content-Type', 'text/html')])
			return [ template_stream.render() ]

		elif path_info == '/coroutine':
			coroid = int(request.parse_formvars(environ)["id"])

			if coroid not in coroutine._COROUTINES:
				start_response('200 OK', [('Content-Type', 'text/html')])
				return [ "Coroutine not available" ]

			template_stream = self.coroutine_template.generate(coro=coroutine._COROUTINES[coroid], gc=gc)

			start_response('200 OK', [('Content-Type', 'text/html')])
			return [ template_stream.render() ]

		elif path_info == '/test-genshi':
			template_stream = self.test_template.generate()

			start_response('200 OK', [('Content-Type', 'text/html')])
			return [ template_stream.render() ]

		elif self.next_application:
			return self.next_application(environ, start_response)
		else:
			start_response('404 Not Found', [('Content-Type', 'text/html')])
			return [ "404 Not Found" ]
