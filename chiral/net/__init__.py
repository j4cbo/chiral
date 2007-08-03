"""TCP/IP networking."""

# Chiral, copyright (c) 2007 Jacob Potter
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 2.

from chiral.net import netcore

# This is not a constant.
#pylint: disable-msg=C0103
reactor = netcore.DefaultReactor()
