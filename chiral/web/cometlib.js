var chiral = {
	version : "$Rev $",
	web : {},
	log : function(message) { }
}

chiral.web.CometStream = function(source_url, data_callback) {

	method = "iframe";

	if (source_url.indexOf("?") != -1) {
		source_url = source_url + "&_chiral_comet_method=" + method;
	} else {
		source_url = source_url + "?_chiral_comet_method=" + method;
	}

	this.run_MXMR = function() {
		var xhr = new XMLHttpRequest();
		xhr.multipart = true;
		xhr.open("GET", source_url);
		xhr.onload = function(event) {
			if (xhr.readyState == 4) {
				if (xhr.status == 200) {
					data_callback(event.responseText);
				} else {
					chiral.log("chiral.web.CometStream MXMR failed: " + xhr.statusText);
				}
			}
		}

		xhr.send("")
	}

	this.run_iframe = function() {

		/* Create the iframe in which the page will be loaded */
		var ifr = document.createElement("iframe");
		ifr.src = source_url;

		/* To make sure the iframe remains hidden, set both DOM level 1
		   and CSS properties
		*/
		ifr.style.visibility = "hidden";
		ifr.style.position = "absolute";
		ifr.style.border = "0px none";
		ifr.style.left = "-10px";
		ifr.style.top = "-10px";
		ifr.width = 0;
		ifr.height = 0;
		ifr.marginWidth = 0;
		ifr.marginHeight = 0;
		ifr.frameBorder = 0;
		ifr.scrolling = 0;

		ifr.iframe_info = "test";

		/* We may be able to attach it now; if not, then add a callback */
		if (document.body) {
			document.body.appendChild(ifr);
		} else if (document.attachEvent) {
			document.attachEvent("onload", function() {
				document.body.appendChild(ifr);
			});
		} else {
			window.addEventListener("load", function() {
				document.body.appendChild(ifr);
			}, true);
		}
setTimeout(function() { alert(ifr.contentWindow) }, 1000);
	}

	this["run_" + method]();
}

var cs = new chiral.web.CometStream("/static/c.html", function(s) { alert(s); })

