-- Guild roles (helpers/guild_roles.py, helpers/guild_role_store.py): named, coloured groups a guildmaster gives members,
-- that carry a few permissions (not slowed by slow mode, remove messages, pin messages) and can allow or deny reading
-- and writing in a channel, over its ladder (helpers/guild_channels.py). Safe to run twice.
--   psql "$DATABASE_URL" -f scripts/migrations/2026-11-08_guild_roles.sql
CREATE TABLE IF NOT EXISTS public.guild_roles (
    id SERIAL PRIMARY KEY,
    board_id integer NOT NULL REFERENCES public.boards(id) ON DELETE CASCADE,
    name character varying(30) NOT NULL,
    word_severity smallint DEFAULT 0 NOT NULL,
    colour character varying(12) DEFAULT 'gray' NOT NULL,
    position integer DEFAULT 0 NOT NULL,
    perm_slow_exempt boolean DEFAULT false NOT NULL,
    perm_remove_messages boolean DEFAULT false NOT NULL,
    perm_pin boolean DEFAULT false NOT NULL
);

CREATE INDEX IF NOT EXISTS guild_roles_board_idx ON public.guild_roles (board_id, position, id);

CREATE TABLE IF NOT EXISTS public.guild_role_members (
    role_id integer NOT NULL REFERENCES public.guild_roles(id) ON DELETE CASCADE,
    user_id integer NOT NULL,
    assigned_by integer NOT NULL,
    created_utc integer DEFAULT 0 NOT NULL,
    PRIMARY KEY (role_id, user_id)
);

CREATE INDEX IF NOT EXISTS guild_role_members_user_idx ON public.guild_role_members (user_id);

CREATE TABLE IF NOT EXISTS public.guild_channel_overwrites (
    channel_id integer NOT NULL REFERENCES public.guild_channels(id) ON DELETE CASCADE,
    role_id integer NOT NULL REFERENCES public.guild_roles(id) ON DELETE CASCADE,
    read_ow smallint DEFAULT 0 NOT NULL,
    write_ow smallint DEFAULT 0 NOT NULL,
    PRIMARY KEY (channel_id, role_id),
    CONSTRAINT guild_channel_overwrites_check CHECK (read_ow IN (0, 1, 2) AND write_ow IN (0, 1, 2))
);

CREATE INDEX IF NOT EXISTS guild_channel_overwrites_role_idx ON public.guild_channel_overwrites (role_id);
