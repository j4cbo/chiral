"""Framework functions"""

from chiral.core import tasklet

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

def completion_handler(tlet, retval, exc_info):
	"""
	Called when any tasklet-based page completes. Applies all
	postprocessing steps, and returns the page to the browser.
	"""

	func, environ, start_response = tlet.parameters

	if exc_info:
		# Shit
		raise exc_info

	# Handle all the postprocessing steps
	if hasattr(func, "chiral_postprocessors"):
		for postproc in func.chiral_postprocessors:
			retval = postproc(retval)

	# Return it to the browser
	conn = environ["chiral.http.connection"]
	start_response("200 OK", {})

	tlet.result = [ retval ], None


def tasklet_page(include_get_vars=True):
	def decorate(func):
		def wrapped_function(environ, start_response):

			# Get the request information with Paste
			req = request.parse_formvars(environ, include_get_vars).mixed()

			# Start the tasklet. When it's done, we'll send the response back via
			# HTTP.
			response_tasklet = tasklet.Tasklet(
				func(**req),
				default_callback=completion_handler,
				parameters = (func, environ, start_response)
			)

			environ["chiral.http.set_tasklet"](response_tasklet)
			return [""]

		return wrapped_function

	return decorate
