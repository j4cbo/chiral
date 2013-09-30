Chiral
======

Chiral is a lightweight coroutine-based networking framework
for high-performance internet and Web services.

Coroutines in Chiral are based on Python 2.5's generators, as 
specified in [PEP 342](http://www.python.org/dev/peps/pep-0342/).
The Coroutine class wraps around a generator
and handles scheduling. Coroutines are expected to yield
WaitConditions, which carry the conditions for the coroutine to be
resumed again. This makes asynchronous networking simple and
straightforward; see the API documentation (docstrings) for more.

On top of Coroutines, Chiral provides:

- TCP connection management
- High-performance networking with epoll()
- Introspection and on-the-fly code reloading
- A fast HTTP server supporting WSGI, with extensions for Coroutine-based pages
- A native memcached client
- Deeply-integrated COMET support
- In progress: an instant messaging framework; a thread pool system for calling blocking code
- Planned: asynchronous database access with DB-API or SQLAlchemy

Chiral development is currently inactive.
