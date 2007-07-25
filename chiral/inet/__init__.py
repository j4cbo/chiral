"""TCP/IP networking."""

from chiral.inet import netcore

# This is not a constant.
#pylint: disable-msg=C0103
reactor = netcore.DefaultReactor()
