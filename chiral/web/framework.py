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
	def decorate(func):
		def wrapped_function(environ, start_response):

			# Get the request information with Paste
			req = request.parse_formvars(environ, include_get_vars).mixed()

			# Start the coroutine. When it's done, we'll send the response back via
			# HTTP.
			response_coro = coroutine.Coroutine(
				func(**req),
				autostart=False
			)

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
				conn = environ["chiral.http.connection"]
				start_response("200 OK", {})

				return [ retval ], None

			response_coro.add_completion_callback(completion_handler)

			environ["chiral.http.set_coro"](response_coro)
			return [""]

		return wrapped_function

	return decorate
