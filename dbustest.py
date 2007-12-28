#/usr/bin/env python2.5

print "Loading modules..."

from chiral.core import stats
from chiral.net import reactor, dbus

print "Initializing..."

sb = dbus.SystemBusConnection()

print "Running..."

reactor.run()

stats.dump()
