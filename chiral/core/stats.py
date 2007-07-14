"""Statistics tracking for Chiral"""

try:
	_STATS # pylint: disable-msg=W0104
except NameError, e:
	_STATS = {}


if __debug__:
	def increment(key):
		"""Increment the call count for an event."""
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
