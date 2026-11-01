"""Hands counted requests to Redis off the request (helpers/traffic.py), so that nothing about the counter can slow or break
a page: `put` only puts a `Hit` on a queue in this worker and returns. One greenlet per worker empties it in batches, so
the counter holds at most one of the site's Redis connections at a time (the pool is shared with the pages, and refuses a
caller when it is empty).

Redis down, or hanging: the batch being sent is dropped after `SEND_TIMEOUT`, the queue fills, and past `MAX_WAITING` new
counts are dropped. The counts fall short for that time, with one log line every `WARN_EVERY` seconds per worker, and nothing
else happens. A worker that stops loses what is in
its queue, which is what arrived in the last moment. Separate from helpers/traffic_store.py because gevent is not part
of the test environment's rules and SQL."""
import logging
import time

import gevent
from gevent.queue import Empty, Full, Queue

MAX_WAITING = 2000
BATCH = 200
SEND_TIMEOUT = 2
WARN_EVERY = 300


class Sender:
    def __init__(self, redis_client, record):
        self.r = redis_client
        self.record = record
        self.queue = None
        self.runner = None
        self.warned = 0

    def put(self, hit):
        if self.r is None:
            return
        if self.queue is None:              # made in the worker, not in a parent that forked it
            self.queue = Queue(MAX_WAITING)
        try:
            self.queue.put_nowait(hit)
        except Full:
            return
        if self.runner is None or self.runner.dead:
            self.runner = gevent.spawn(self._run)

    def _next_batch(self):
        hits = [self.queue.get()]
        while len(hits) < BATCH:
            try:
                hits.append(self.queue.get_nowait())
            except Empty:
                break
        return hits

    def _run(self):
        while True:
            hits = self._next_batch()
            try:
                with gevent.Timeout(SEND_TIMEOUT):
                    self.record(self.r, hits)
            except (Exception, gevent.Timeout) as error:    # a Timeout is no Exception, and it is what a hung Redis gives
                now = time.time()
                if now - self.warned > WARN_EVERY:
                    self.warned = now
                    logging.warning("traffic counts dropped: %r", error)
