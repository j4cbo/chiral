#!/bin/bash

LATESTFILE=~jacob/code/chiral-doc/latest-update

LASTREV=`svnlook youngest ~jacob/svn/chiral`
LASTDOC=$(< $LATESTFILE)

if [ "$LASTREV" -gt "$LASTDOC" ]
then
	cd ~jacob/code/chiral
	svn up -q
	pydoctor -c ~jacob/code/chiral-doc/chiral-doc.cfg -q --make-html \
	    > ~jacob/www/chiral.j4cbo.com/chiral-doc/doc.log 2>&1
	echo $LASTREV > $LATESTFILE 
fi
