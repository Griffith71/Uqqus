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
