-- The Log, slice D3f (actions on an alias): a guildmaster can exile or chat-mute "Anonymous #3" without learning who it is (the Log plan's decision 24).
-- The ban row (bans / chatbans) is an ordinary one for the account, marked with the thread and number it was made by, so the guild's lists draw "Anonymous #3"
-- and never the username. The Log's entry carries the number (public: it is drawn on the item already) and, for the staff and the appeal alone, the account
-- (`subject_user_id`: never selected below staff, never in `target_user_id`, so the `to=` filter and the account's own log can't find it). Safe to run twice.
ALTER TABLE public.bans ADD COLUMN IF NOT EXISTS alias_post_id integer;
ALTER TABLE public.bans ADD COLUMN IF NOT EXISTS alias_n integer;
ALTER TABLE public.chatbans ADD COLUMN IF NOT EXISTS alias_post_id integer;
ALTER TABLE public.chatbans ADD COLUMN IF NOT EXISTS alias_n integer;
ALTER TABLE public.log_entries ADD COLUMN IF NOT EXISTS alias_n integer;
ALTER TABLE public.log_entries ADD COLUMN IF NOT EXISTS subject_user_id integer;
