-- The Log, slice D3e: a stable alias per thread for anonymous participants. "Anonymous #3" is the same person in one thread (a post's comment space: the post, its
-- guild copies and everything said under any of them) and a different number in every other thread; a number once given is never reused or changed, and it
-- outlives an account's deletion (helpers/anon_aliases.py). No foreign keys (the project's rule). Safe to run twice.
CREATE TABLE IF NOT EXISTS public.anon_aliases (
    scope_kind character varying(8) NOT NULL,
    scope_id integer NOT NULL,
    user_id integer NOT NULL,
    n integer NOT NULL,
    created_utc bigint DEFAULT 0 NOT NULL,
    PRIMARY KEY (scope_kind, scope_id, user_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS anon_aliases_number_idx ON public.anon_aliases (scope_kind, scope_id, n);
