-- The video platforms added to KNOWN_PROVIDER_HANDLERS on 2026-10-16 (ruqqus/helpers/embed.py: Streamable, Kick,
-- Loom, TED) get the same repair as 2026-10-10_trusted_provider_embeds.sql: a post whose page's own og:video player
-- was saved as "iframe_untrusted" (strict sandbox, where the platform's player cannot load) when it pointed back at
-- that same platform is the platform's own player. New posts get "iframe" through embed_trust.same_provider_player.
-- Data only, safe to run twice.

UPDATE submissions_aux a
SET embed_type = 'iframe'
FROM (VALUES
    ('streamable.com'), ('kick.com'), ('loom.com'), ('ted.com')
) AS provider(domain)
WHERE a.embed_type = 'iframe_untrusted'
  AND lower(substring(a.embed_url FROM '^https://([^/:?#]+)')) ~ ('(^|\.)' || replace(provider.domain, '.', '\.') || '$')
  AND lower(substring(a.url FROM '^https?://([^/:?#]+)')) ~ ('(^|\.)' || replace(provider.domain, '.', '\.') || '$');
