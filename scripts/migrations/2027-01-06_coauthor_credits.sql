-- The Log, slice D3f: a flag per credit. A co-author of a post (and every other author of a post written together, who is one of its accepted co-authors) may have
-- their credit anonymous: hidden from everyone but admins and themselves, shown by their alias ("Anonymous #2") in the byline and on the shared-post pages
-- (helpers/coauthors.py). A post made anonymous from the start sets every flag; "Remove my name" on a credit and an account's deletion set one. The post's
-- primary author's credit stays `submissions.is_anonymous`. Safe to run twice.
ALTER TABLE public.post_coauthors ADD COLUMN IF NOT EXISTS is_anonymous boolean DEFAULT false NOT NULL;
