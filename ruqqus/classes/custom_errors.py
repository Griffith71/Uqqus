from werkzeug.exceptions import NotFound

from ruqqus.__main__ import app


class CircleOnly(NotFound):
    """A post the viewer may not see because of who it was made for (helpers/circle_guard.py). It IS a 404, so every
    route that does not know about it fails closed; the error handler in routes/circles.py draws a gate for a page
    request. `post` is only for that gate: it never leaves the server."""

    def __init__(self, post):
        super().__init__()
        self.post = post


class PaymentRequired(Exception):
    status_code=402
    def __init__(self):
        Exception.__init__(self)
        self.status_code=402


class DatabaseOverload(Exception):
    status_code=500
    def __init__(self):
        Exception.__init__(self)
        self.status_code=500