"""
Statistics tracking

The statistics package keeps track of events in a simple, global dictionary. When
increment() is called with a given key, the corresponding value is set to 1 if it is not
already present, or incremented if it is present.
"""

try:
	_STATS # pylint: disable-msg=W0104
except NameError:
	_STATS = {}


if __debug__:
	def increment(key):
		"""Increment the call count for an event.

		Example::

			if key in cache:
				return cache[key]
			else:
				stats.increment("myapp.cache.misses")
				return self.get(key)
		"""
		try:
			_STATS[key] += 1
		except KeyError:
			_STATS[key] = 1
else:
	def increment(key): # pylint: disable-msg=W0613
		"""Increment the call count for an event. (Disabled in non-debug mode.)"""
		pass

def dump():
	"""Dump a list of the number of times each recorded event ocurred to stdout."""

	print "Statistics:"
	keys = sorted(_STATS.iterkeys())
	for key in keys:
		print "%s: %d" % (key, _STATS[key])

def retrieve():
	"""Return a copy of the statistics dict."""
	return _STATS.copy()

def clear():
	"""Clear all statistics counters."""

__all__ = [ "increment", "dump", "retrieve", "clear" ]
