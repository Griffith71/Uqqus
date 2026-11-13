-- Group chats of up to 500 people count unread messages as guild channels do: the group counts its messages
-- (`chat_groups.message_seq`, one UPDATE per message) and each member's row holds the count they last read
-- (`chat_group_members.last_read_seq`), and a member's unread is the difference. The old per-member `unread_count`, one UPDATE
-- of every member's row per message, is no longer written or read (left in place: the one-off step below reads it).
-- The one-off step carries today's unread counts over, and runs only while this file isn't recorded, so running the
-- file again changes nothing. Safe to run twice.
--   psql "$DATABASE_URL" -f scripts/migrations/2026-12-24_group_unread_seq.sql
ALTER TABLE public.chat_groups ADD COLUMN IF NOT EXISTS message_seq bigint DEFAULT 0 NOT NULL;
ALTER TABLE public.chat_group_members ADD COLUMN IF NOT EXISTS last_read_seq bigint DEFAULT 0 NOT NULL;

UPDATE public.chat_groups g SET message_seq = counts.most
  FROM (SELECT group_id, MAX(unread_count) AS most FROM public.chat_group_members GROUP BY group_id) counts
 WHERE counts.group_id = g.id AND counts.most > 0
   AND NOT EXISTS (SELECT 1 FROM public.schema_migrations WHERE name = '2026-12-24_group_unread_seq.sql');
UPDATE public.chat_group_members m SET last_read_seq = g.message_seq - m.unread_count
  FROM public.chat_groups g
 WHERE g.id = m.group_id AND m.status = 'joined'
   AND NOT EXISTS (SELECT 1 FROM public.schema_migrations WHERE name = '2026-12-24_group_unread_seq.sql');
UPDATE public.chat_group_members m SET last_read_seq = g.message_seq
  FROM public.chat_groups g
 WHERE g.id = m.group_id AND m.status = 'invited'
   AND NOT EXISTS (SELECT 1 FROM public.schema_migrations WHERE name = '2026-12-24_group_unread_seq.sql');

INSERT INTO public.schema_migrations (name, applied_utc)
VALUES ('2026-12-24_group_unread_seq.sql', extract(epoch from now())::int) ON CONFLICT (name) DO NOTHING;
