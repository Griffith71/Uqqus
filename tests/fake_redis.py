"""A small in-memory stand-in for the Redis commands the live features use (strings, hashes, expiry ignored)."""


class FakeRedis:
    def __init__(self):
        self.data = {}
        self.published = []

    # strings
    def get(self, key):
        value = self.data.get(key)
        return value if isinstance(value, str) else None

    def set(self, key, value, ex=None, nx=False):
        if nx and key in self.data:
            return None
        self.data[key] = str(value)
        return True

    def delete(self, *keys):
        n = 0
        for key in keys:
            if key in self.data:
                del self.data[key]
                n += 1
        return n

    def expire(self, key, seconds):
        return key in self.data

    def ttl(self, key):
        return 30 if key in self.data else -2

    # hashes
    def _hash(self, key):
        value = self.data.setdefault(key, {})
        if not isinstance(value, dict):
            raise TypeError("not a hash")
        return value

    def hset(self, key, field=None, value=None, mapping=None):
        h = self._hash(key)
        if mapping:
            for k, v in mapping.items():
                h[str(k)] = str(v)
        if field is not None:
            h[str(field)] = str(value)
        return 1

    def hsetnx(self, key, field, value):
        h = self._hash(key)
        if field in h:
            return 0
        h[field] = str(value)
        return 1

    def hget(self, key, field):
        value = self.data.get(key)
        return value.get(field) if isinstance(value, dict) else None

    def hgetall(self, key):
        value = self.data.get(key)
        return dict(value) if isinstance(value, dict) else {}

    def hincrby(self, key, field, amount=1):
        h = self._hash(key)
        h[field] = str(int(h.get(field, 0)) + amount)
        return int(h[field])

    def hdel(self, key, *fields):
        h = self.data.get(key)
        if not isinstance(h, dict):
            return 0
        n = 0
        for field in fields:
            if field in h:
                del h[field]
                n += 1
        if not h:
            del self.data[key]
        return n

    # sets
    def sadd(self, key, *members):
        s = self.data.setdefault(key, set())
        before = len(s)
        s.update(str(m) for m in members)
        return len(s) - before

    def srem(self, key, *members):
        s = self.data.get(key)
        if not isinstance(s, set):
            return 0
        n = sum(1 for m in members if str(m) in s)
        s.difference_update(str(m) for m in members)
        return n

    def smembers(self, key):
        s = self.data.get(key)
        return set(s) if isinstance(s, set) else set()

    def scard(self, key):
        return len(self.smembers(key))

    def incr(self, key):
        value = int(self.data.get(key, 0)) + 1
        self.data[key] = str(value)
        return value

    # pub/sub
    def publish(self, channel, message):
        self.published.append((channel, message))
        return 1

    # keys
    def exists(self, key):
        return 1 if key in self.data else 0

    def renamenx(self, source, target):
        if source not in self.data:
            raise RuntimeError("no such key")
        if target in self.data:
            return False
        self.data[target] = self.data.pop(source)
        return True

    # sorted sets (a dict of member -> score; order is by score, then member, as in Redis)
    def zincrby(self, key, amount, member):
        z = self.data.setdefault(key, {})
        z[str(member)] = float(z.get(str(member), 0)) + amount
        return z[str(member)]

    def _ranked(self, key):
        z = self.data.get(key)
        return sorted(z.items(), key=lambda kv: (kv[1], kv[0])) if isinstance(z, dict) else []

    def zrange(self, key, start, stop, withscores=False):
        rows = self._ranked(key)
        rows = rows[start:] if stop == -1 else rows[start:stop + 1]
        return rows if withscores else [member for member, _ in rows]

    def zremrangebyrank(self, key, start, stop):
        rows = self._ranked(key)
        end = len(rows) + stop + 1 if stop < 0 else stop + 1
        gone = rows[start:max(end, 0)]
        for member, _ in gone:
            del self.data[key][member]
        if key in self.data and not self.data[key]:
            del self.data[key]
        return len(gone)

    def pipeline(self, transaction=True):
        return FakePipeline(self)


class FakePipeline:
    """Commands queued and run together by `execute`, as a Redis transaction does. A failure leaves nothing done."""

    def __init__(self, redis):
        self.redis = redis
        self.queued = []

    def __getattr__(self, name):
        def queue(*args, **kwargs):
            self.queued.append((name, args, kwargs))
            return self
        return queue

    def execute(self):
        queued, self.queued = self.queued, []
        return [getattr(self.redis, name)(*args, **kwargs) for name, args, kwargs in queued]
