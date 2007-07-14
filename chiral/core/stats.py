try:
	_stats
except NameError, e:
	_stats = {}


if __debug__:
	def increment(key):
		try:
			_stats[key] += 1
		except KeyError:
			_stats[key] = 1
else:
	def increment(key):
		pass

def dump():
	print "Statistics:"
	keys = sorted(_stats.iterkeys())
	for key in keys:
		print "%s: %d" % (key, _stats[key])
