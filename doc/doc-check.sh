#!/bin/sh

LATESTFILE="~jacob/code/chiral-doc/lastest-update"

LASTREV=`svnlook youngest ~jacob/svn/chiral`
LASTDOC=$(LATESTFILE)

if [ "$LASTREV" -gt "$LASTDOC" ]
then
	echo "updating"
	echo $LASTREV > $LATESTFILE 
fi
