from ruqqus.helpers.base36 import *
from ruqqus.helpers.lazy import lazy
from ruqqus.helpers.i18n_runtime import date_text, datetime_text, tr, trn, trp
import math
import random
import time

from ruqqus.__main__ import cache, app


class Stndrd:

    @property
    @lazy
    def base36id(self):
        return base36encode(self.id)

    @property
    @lazy
    def created_date(self):
        return date_text(self.created_utc, "%d %B %Y")

    @property
    @lazy
    def created_datetime(self):
        return datetime_text(self.created_utc, "%d %B %Y at %H:%M:%S")

    @property
    @lazy
    def created_iso(self):

        t = time.gmtime(self.created_utc)
        return time.strftime("%Y-%m-%dT%H:%M:%S+00:00", t)

    @property
    def permalink_full(self):
        return f"http{'s' if app.config['FORCE_HTTPS'] else ''}://{app.config['SERVER_NAME']}{self.permalink}"
    


class Age_times:

    @property
    def age(self):

        now = int(time.time())

        return now - self.created_utc

    @property
    def created_date(self):
        # the same format as Stndrd.created_date: Submission and Comment list these mix-ins in a different
        # order, so whichever wins, a post and a comment show their dates alike
        return date_text(self.created_utc, "%d %B %Y")

    @property
    def created_datetime(self):

        return datetime_text(self.created_utc, "%d %B %Y at %H:%M:%S")

    @property
    def age_string(self):

        age = self.age

        return _ago(age, self.created_utc, whole_months=True)

    @property
    def edited_string(self):

        if not self.edited_utc:
            # Translators: when a post or comment was last edited, for one that never was
            return trp("edited", "never")

        age = int(time.time()) - self.edited_utc

        return _ago(age, self.edited_utc, whole_months=False)

    @property
    def edited_date(self):
        return date_text(self.edited_utc, "%d %B %Y")

    @property
    def edited_datetime(self):
        return datetime_text(self.edited_utc, "%d %B %Y at %H:%M:%S")
    
    @property
    @lazy
    def age_years(self):
        
        now=time.gmtime()
        ctd=time.gmtime(self.created_utc)
        
        years = now.tm_year - ctd.tm_year
        
        if now.tm_yday < ctd.tm_yday:
            years-=1
            
        return years


def _ago(age, then_utc, whole_months):
    """"just now", "5 minutes ago", "3 months ago": how long ago a time `age` seconds back was (`then_utc`). Under a
    month it counts minutes, hours and days; then calendar months, and with `whole_months` a month counts only once
    its day of the month has come round again. Every number here is at least 1."""
    if age < 60:
        # Translators: how long ago something was posted or edited
        return tr("just now")
    elif age < 3600:
        return trn("%(num)s minute ago", "%(num)s minutes ago", int(age / 60))
    elif age < 86400:
        return trn("%(num)s hour ago", "%(num)s hours ago", int(age / 3600))
    elif age < 2678400:
        return trn("%(num)s day ago", "%(num)s days ago", int(age / 86400))

    now = time.gmtime()
    ctd = time.gmtime(then_utc)

    # compute number of months
    months = now.tm_mon - ctd.tm_mon + 12 * (now.tm_year - ctd.tm_year)
    # remove a month count if current day of month < creation day of month
    if whole_months and now.tm_mday < ctd.tm_mday:
        months -= 1

    if months < 12:
        return trn("%(num)s month ago", "%(num)s months ago", months)
    years = int(months / 12) if whole_months else now.tm_year - ctd.tm_year
    return trn("%(num)s year ago", "%(num)s years ago", years)


class Scores:

    @property
    #@cache.memoize(timeout=60)
    def score_percent(self):
        # try:
        # return int((self.ups/(self.ups+self.downs))*100)
        # except ZeroDivisionError:
        # return 0

        return 101

    @property
    #@cache.memoize(timeout=60)
    def score(self):
        return int(self.score_top) or 0


class Fuzzing:

    @property
    #@cache.memoize(timeout=60)
    def score_fuzzed(self):

        real = self.score_top if self.score_top else self.score
        real = int(real)
        if real <= 10:
            return real

        k = 0.01

        a = math.floor(real * (1 - k))
        b = math.ceil(real * (1 + k))
        return random.randint(a, b)

    @property
    def upvotes_fuzzed(self):

        # a post made for guilds only adds its guilds' votes (helpers/space_counts.py); nothing else has the attribute
        upvotes = self.upvotes + self.__dict__.get("_space_ups", 0)
        if upvotes <= 10 or self.is_archived:
            return upvotes

        lower = int(upvotes * 0.99)
        upper = int(upvotes * 1.01) + 1

        return random.randint(lower, upper)

    @property
    def downvotes_fuzzed(self):
        downvotes = self.downvotes + self.__dict__.get("_space_downs", 0)
        if downvotes <= 10 or self.is_archived:
            return downvotes

        lower = int(downvotes * 0.99)
        upper = int(downvotes * 1.01) + 1

        return random.randint(lower, upper)
