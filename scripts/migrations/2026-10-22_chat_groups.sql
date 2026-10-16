-- Group chats (helpers/chat_groups.py, helpers/chat_group_store.py): one end-to-end encrypted Matrix room per group,
-- made and controlled by the site's bot like a 1:1 chat. A member is 'invited' (the group is in their Requests) or
-- 'joined' (in their Inbox); leaving or being removed deletes the row. 1:1 chats stay in chat_conversations.
-- Safe to run twice.
--   psql "$DATABASE_URL" -f scripts/migrations/2026-10-22_chat_groups.sql
CREATE TABLE IF NOT EXISTS public.chat_groups (
    id SERIAL PRIMARY KEY,
    matrix_room_id character varying(255) NOT NULL,
    name character varying(50) DEFAULT '' NOT NULL,
    name_severity smallint DEFAULT 0 NOT NULL,
    created_by_id integer NOT NULL,
    created_utc integer NOT NULL,
    last_activity_utc integer DEFAULT 0 NOT NULL,
    deleted_utc integer DEFAULT 0 NOT NULL,
    CONSTRAINT chat_groups_room_key UNIQUE (matrix_room_id)
);

CREATE TABLE IF NOT EXISTS public.chat_group_members (
    group_id integer NOT NULL REFERENCES public.chat_groups(id) ON DELETE CASCADE,
    user_id integer NOT NULL,
    role character varying(8) DEFAULT 'member' NOT NULL,
    status character varying(8) DEFAULT 'invited' NOT NULL,
    invited_by_id integer,
    unread_count integer DEFAULT 0 NOT NULL,
    last_read_utc integer DEFAULT 0 NOT NULL,
    joined_utc integer DEFAULT 0 NOT NULL,
    invited_utc integer DEFAULT 0 NOT NULL,
    PRIMARY KEY (group_id, user_id),
    CONSTRAINT chat_group_members_role_check CHECK (role IN ('owner', 'admin', 'member')),
    CONSTRAINT chat_group_members_status_check CHECK (status IN ('invited', 'joined'))
);

CREATE INDEX IF NOT EXISTS chat_group_members_user_idx ON public.chat_group_members (user_id, status);
CREATE INDEX IF NOT EXISTS chat_groups_creator_idx ON public.chat_groups (created_by_id, created_utc);
