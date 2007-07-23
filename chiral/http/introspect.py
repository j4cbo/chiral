import sys
import gc

from chiral.core import xreload, tasklet
from chiral.inet import reactor

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

  <h2>Tasklets</h2>
  <p>${len(list(tlet for tlet in tasklet._TASKLETS.valuerefs() if tlet))} tasklets.</p>
  <ul>
   <li py:for="tlet in tasklet._TASKLETS.values()">
    <a href="${rooturl}tasklet?id=${id(tlet)}">${repr(tlet)}</a>
   </li>
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

INTROSPECTOR_TASKLET_TEMPLATE = """
<html xmlns:py="http://genshi.edgewall.org/">
 <head>
  <title>Chiral Introspector</title>
 </head>
 <body>

  <h1>Tasklet ${id(tlet)}</h1>
  <p>repr(): ${repr(tlet)}</p>

  <p>Referrers:</p>
  <ul>
   <li py:for="ref in gc.get_referrers(tlet)">${repr(ref)}</li>
  </ul>

  <p>dir():</p>
  <ul>
   <li py:for="key in dir(tlet)"><b>${key}</b>: ${repr(getattr(tlet, key))}</li>
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

class IntrospectorApplication(object):
	"""WSGI application providing the Chiral Introspector."""

	root_template = MarkupTemplate(INTROSPECTOR_ROOT_TEMPLATE)
	tasklet_template = MarkupTemplate(INTROSPECTOR_TASKLET_TEMPLATE)
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
				gc_collected = gc_collected,
				tasklet = tasklet,
				reactor = reactor,
				mod_list = mod_list
			)

			start_response('200 OK', [('Content-Type', 'text/html')])
			return [ template_stream.render() ]

		elif path_info == '/tasklet':
			tletid = int(request.parse_formvars(environ)["id"])

			if tletid not in tasklet._TASKLETS:
				start_response('200 OK', [('Content-Type', 'text/html')])
				return [ "Tasklet not available" ]

			template_stream = self.tasklet_template.generate(tlet=tasklet._TASKLETS[tletid], gc=gc)

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
