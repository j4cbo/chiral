try:
	_stats
except NameError, e:
	_stats = {}

def increment(key):
	try:
		_stats[key] += 1
	except KeyError:
		_stats[key] = 1

def dump():
	print "Statistics:"
	for key, value in _stats.iteritems():
		print "%s: %d" % (key, value)
