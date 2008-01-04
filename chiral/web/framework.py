"""Framework functions"""

# Chiral, copyright (c) 2007 Jacob Potter
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 2.

from chiral.core import coroutine

from paste import request
import pkg_resources

_ENGINES = {}

for entry_point in pkg_resources.iter_entry_points('python.templating.engines'):
	try:
		print "Loading %s" % entry_point
		_ENGINES[entry_point.name] = entry_point.load()
	except pkg_resources.DistributionNotFound:
		# If we get a DistributionNotFound, just leave that engine out
		pass
	except:
		import traceback
		import warnings

		warnings.warn("Unable to load template engine entry point: '%s': %s" % (
			entry_point,
			traceback.format_exc()
		), RuntimeWarning, 2)


def use_template(engine, template, **kwargs):
	"""Filter the result from the decorated function through a template engine.

	This uses the ``python.templating.engines`` entry point of ``pkg_resources`` to
	automatically load installed templating engines, such as Kid (via TurboKid) and
	Genshi. It should be applied around the `coroutine_page` decorator. A
	``chiral_postprocessors`` attribute will be added to the wrapped function which will
	filter its output through the given engine and template. Any ``kwargs`` are passed
	to the engine's ``render`` method.

	For example::

		@coroutine_page()
		@use_template("genshi", "chiral.web.templates.helloworld")
		def asyncpagetest():
			yield
			raise StopIteration({ "foo": "Test Page" })
	"""

	if engine not in _ENGINES:
		raise Exception("Engine '%s' is not available." % (engine, ))

	eng = _ENGINES[engine]()

	def apply_template(res):
		return eng.render(res, template=template, **kwargs)

	def decorate(func):
		if not hasattr(func, "chiral_postprocessors"):
			func.chiral_postprocessors = []

		func.chiral_postprocessors.append(apply_template)

		return func

	return decorate


def coroutine_page(include_get_vars=True):
	"""
	Turn a coroutine function into a WSGI callable.

	This is best illustrated by an example::

		@coroutine_page()
		def sample_page(name=None):
			yield reactor.schedule(0.5)
			template = "<html><body><h1>Hello, %s!</h1></body></h1>"
			if name:
				raise StopIteration(template % (cgi.escape(name), ))
			else:
				raise StopIteration(template % ("world", ))

	The ``sample_page`` function is now a WSGI callable. When invoked,
	``paste.request.parse_formvars`` is used to parse the GET request, and the
	resultant dict is passed as kwargs to the original function. A coroutine
	is instantiated around it, and returned to the `chiral.web.httpd` WSGI server
	with its ``chiral.http.set_coro`` extension method.

	If the ``sample_page`` coroutine causes an exception, it will be reraised,
	causing the httpd to return a 500 Internal Server Error. Otherwise, the request
	returns with a 200 OK. Optionally, a ``chiral.postprocessors`` attribute can be
	set on the function with a list of callables, through which the return value will
	be filtered before being sent to the browser. This mechanism is used by the `use_template` 
	decorator.

	:param include_get_vars:
		Whether GET values should be included in the result; normally True.
		See the documentation for ``paste.request.parse_formvars``. 
	"""

	def decorate(func):
		def wrapped_function(environ, start_response):

			# Get the request information with Paste
			req = request.parse_formvars(environ, include_get_vars).mixed()

			# Start the coroutine. When it's done, we'll send the response back via
			# HTTP.
			response_coro = coroutine.Coroutine(func(**req))

			def completion_handler(retval, exc_info):
				"""
				Called when any coroutine-based page completes. Applies all
				postprocessing steps, and returns the page to the browser.
				"""

				if exc_info:
					# Shit
					raise exc_info[0], exc_info[1], exc_info[2]

				# Handle all the postprocessing steps
				if hasattr(func, "chiral_postprocessors"):
					for postproc in func.chiral_postprocessors:
						retval = postproc(retval)

				# Return it to the browser
				start_response("200 OK", {})

				return [ retval ], None

			response_coro.add_completion_callback(completion_handler)

			environ["chiral.http.set_coro"](response_coro)
			return [""]

		return wrapped_function

	return decorate
