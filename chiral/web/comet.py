import datetime
import time

_CHIRAL_RELOADABLE = True

class CometClock(object):
	def __init__(self, looper):
		self.looper = looper

	def comet_tasklet(self, connection):
		curtime = time.time()
		while True:
			yield connection.send("<p>%s</p>\n" % (str(datetime.datetime.now()), ))
			curtime += 1
			yield self.looper.schedule(callbacktime = curtime)

	def __call__(self, environ, start_response):
		path_info = environ.get('PATH_INFO', '')

		if path_info == '/':
			start_response('200 OK', [('Content-Type', 'text/html')])
			environ["chiral.http.set_tasklet"](self.comet_tasklet)
			return  [ """
<html>
<head>
<title>Test</title>
<style type="text/css">
#clockouter { position: relative; }
#clockouter p { position: absolute; background: white; padding: 10px 10px 10px 0; }
</style>
</head>
<body>
<h1>Clock</h1>
<div id="clockouter">
"""
			]
		else:
			start_response('404 Not Found', [('Content-Type', 'text/html')])
			return [ "404 Not Found" ]
