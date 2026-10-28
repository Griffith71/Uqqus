-- Notifications in the reader's own language (helpers/i18n_runtime.py, helpers/alerts.py notify): a system notice keeps
-- its English text as before (body, body_html: the API, the data export and search read it) and also the message it
-- was made from and its values, so the notifications page can draw it again in each reader's interface language. One
-- notice shared by many readers (alerts.send_to_many, the bells) is read by each in their own.
--   PYTHONPATH=. python scripts/migrate.py --apply
-- Safe to run twice.

ALTER TABLE public.comments_aux ADD COLUMN IF NOT EXISTS notice_msgid text;
ALTER TABLE public.comments_aux ADD COLUMN IF NOT EXISTS notice_params text;
