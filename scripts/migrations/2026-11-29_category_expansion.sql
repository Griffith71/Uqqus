-- Categories: 12 new ones (ids 16-27) and 196 new subcategories (ids 110-305), 101 of them in the 15 categories that
-- exist and 95 in the new ones (ruqqus/helpers/taxonomy_names.py lists every name for the interface languages).
-- Nothing existing is renamed, moved or removed: guilds hold subcategory ids and 44 (Offensive) and 108 (Identity
-- Politics) are named in the code. Also widens curations.category_filter: a curation can now name all of the
-- subcategories, about 280 ids.
--   PYTHONPATH=. python scripts/migrate.py --apply
-- Fresh databases get the same rows from seed-db.sql. Safe to run twice. The app reads the categories once when it
-- starts (and so do its background jobs), so restart all of it afterwards.

CREATE TEMP TABLE category_expansion_categories (id integer, name character varying(64), icon character varying(64));
INSERT INTO category_expansion_categories VALUES
    (16, 'Anime & Manga', 'fa-dragon'),
    (17, 'Education', 'fa-graduation-cap'),
    (18, 'Food', 'fa-utensils'),
    (19, 'Home & Garden', 'fa-seedling'),
    (20, 'Outdoors', 'fa-tree'),
    (21, 'Travel', 'fa-plane'),
    (22, 'Family & Relationships', 'fa-user-friends'),
    (23, 'Vehicles', 'fa-car'),
    (24, 'Music', 'fa-music'),
    (25, 'Film & TV', 'fa-film'),
    (26, 'Books & Writing', 'fa-book'),
    (27, 'Animals', 'fa-paw');

CREATE TEMP TABLE category_expansion_subcategories (id integer, cat_id integer, name character varying(64));
INSERT INTO category_expansion_subcategories VALUES
    (110, 1, 'Painting'),
    (111, 1, 'Drawing & Illustration'),
    (112, 1, 'Digital Art'),
    (113, 1, 'Graphic Design'),
    (114, 1, 'Comics & Cartoons'),
    (115, 2, 'Investing'),
    (116, 2, 'Cryptocurrency'),
    (117, 2, 'Real Estate'),
    (118, 2, 'Economics'),
    (119, 2, 'Marketing & Sales'),
    (120, 3, 'Law'),
    (121, 3, 'Ethics'),
    (122, 3, 'Sociology'),
    (123, 3, 'Mythology & Folklore'),
    (124, 3, 'Spirituality'),
    (125, 3, 'LGBTQ'),
    (126, 4, 'Advice'),
    (127, 4, 'Personal Stories'),
    (128, 4, 'Polls & Surveys'),
    (129, 4, 'Unpopular Opinions'),
    (130, 5, 'Stand-Up Comedy'),
    (131, 5, 'Podcasts'),
    (132, 5, 'Fandoms'),
    (133, 5, 'Nostalgia'),
    (134, 5, 'True Crime'),
    (135, 6, 'Action Games'),
    (136, 6, 'Adventure Games'),
    (137, 6, 'Role-Playing Games'),
    (138, 6, 'Strategy Games'),
    (139, 6, 'Simulation Games'),
    (140, 6, 'Sports & Racing Games'),
    (141, 6, 'Shooters'),
    (142, 6, 'Mobile Games'),
    (143, 6, 'Esports'),
    (144, 6, 'Retro Gaming'),
    (145, 6, 'Indie Games'),
    (146, 7, 'Collecting'),
    (147, 7, 'Trading Cards'),
    (148, 7, 'Model Building'),
    (149, 7, 'Toys & Figures'),
    (150, 7, 'Knitting & Sewing'),
    (151, 7, 'Woodworking'),
    (152, 8, 'Nutrition & Diet'),
    (153, 8, 'Yoga'),
    (154, 8, 'Meditation'),
    (155, 8, 'Sleep'),
    (156, 8, 'Chronic Illness'),
    (157, 9, 'Skincare & Makeup'),
    (158, 9, 'Hair & Grooming'),
    (159, 9, 'Self-Improvement'),
    (160, 9, 'Productivity'),
    (161, 9, 'Frugal Living'),
    (162, 10, 'Humor & Jokes'),
    (163, 10, 'Cringe'),
    (164, 10, 'Oddly Satisfying'),
    (165, 10, 'Interesting'),
    (166, 10, 'Viral & Trending'),
    (167, 11, 'Breaking News'),
    (168, 11, 'Crime & Courts'),
    (169, 11, 'Opinion & Analysis'),
    (170, 11, 'Fact Checks'),
    (171, 12, 'US Politics'),
    (172, 12, 'World Politics'),
    (173, 12, 'Elections'),
    (174, 12, 'Government & Policy'),
    (175, 12, 'Military & Defense'),
    (176, 12, 'Centrist'),
    (177, 13, 'Biology'),
    (178, 13, 'Chemistry'),
    (179, 13, 'Physics'),
    (180, 13, 'Earth Sciences'),
    (181, 13, 'Psychology'),
    (182, 13, 'Neuroscience'),
    (183, 13, 'Archaeology'),
    (184, 14, 'Soccer'),
    (185, 14, 'American Football'),
    (186, 14, 'Basketball'),
    (187, 14, 'Baseball'),
    (188, 14, 'Ice Hockey'),
    (189, 14, 'Cricket'),
    (190, 14, 'Rugby'),
    (191, 14, 'Tennis'),
    (192, 14, 'Golf'),
    (193, 14, 'Motorsports'),
    (194, 14, 'Boxing & MMA'),
    (195, 14, 'Pro Wrestling'),
    (196, 14, 'Cycling'),
    (197, 14, 'Running & Athletics'),
    (198, 14, 'Fantasy Sports'),
    (199, 15, 'Artificial Intelligence'),
    (200, 15, '3D Printing'),
    (201, 15, 'DIY Electronics'),
    (202, 15, 'Cybersecurity'),
    (203, 15, 'Mobile & Apps'),
    (204, 15, 'Social Media'),
    (205, 15, 'Streaming Services'),
    (206, 15, 'VR & AR'),
    (207, 15, 'Robotics'),
    (208, 15, 'Linux & Open Source'),
    (209, 15, 'Smart Home'),
    (210, 15, 'Web Development'),
    (211, 16, 'Anime'),
    (212, 16, 'Manga'),
    (213, 16, 'Cosplay'),
    (214, 16, 'Light Novels'),
    (215, 16, 'VTubers'),
    (216, 17, 'College & University'),
    (217, 17, 'High School'),
    (218, 17, 'Study Tips'),
    (219, 17, 'Homework Help'),
    (220, 17, 'Scholarships & Aid'),
    (221, 17, 'Teaching'),
    (222, 17, 'Online Learning'),
    (223, 17, 'Language Learning'),
    (224, 18, 'Cooking'),
    (225, 18, 'Baking'),
    (226, 18, 'Recipes'),
    (227, 18, 'Restaurants'),
    (228, 18, 'Coffee & Tea'),
    (229, 18, 'Cocktails & Spirits'),
    (230, 18, 'Beer & Wine'),
    (231, 18, 'Vegetarian & Vegan'),
    (232, 19, 'Gardening'),
    (233, 19, 'Houseplants'),
    (234, 19, 'Home Improvement'),
    (235, 19, 'Interior Design'),
    (236, 19, 'Cleaning & Organizing'),
    (237, 19, 'Landscaping'),
    (238, 20, 'Camping'),
    (239, 20, 'Hiking'),
    (240, 20, 'Fishing'),
    (241, 20, 'Hunting'),
    (242, 20, 'Birdwatching'),
    (243, 20, 'Wildlife'),
    (244, 20, 'Environment'),
    (245, 20, 'Survival & Bushcraft'),
    (246, 21, 'Travel Advice'),
    (247, 21, 'Backpacking'),
    (248, 21, 'Road Trips'),
    (249, 21, 'Flights & Airlines'),
    (250, 21, 'Hotels & Stays'),
    (251, 21, 'Digital Nomads'),
    (252, 22, 'Parenting'),
    (253, 22, 'Pregnancy & Babies'),
    (254, 22, 'Dating Advice'),
    (255, 22, 'Marriage'),
    (256, 22, 'Weddings'),
    (257, 22, 'Friendship'),
    (258, 23, 'Cars'),
    (259, 23, 'Trucks & SUVs'),
    (260, 23, 'Motorcycles'),
    (261, 23, 'Electric Vehicles'),
    (262, 23, 'Bicycles'),
    (263, 23, 'Aviation'),
    (264, 23, 'Boats & Sailing'),
    (265, 23, 'Car Repair'),
    (266, 24, 'Rock'),
    (267, 24, 'Pop'),
    (268, 24, 'Hip-Hop'),
    (269, 24, 'Electronic'),
    (270, 24, 'Metal'),
    (271, 24, 'Jazz & Blues'),
    (272, 24, 'Classical'),
    (273, 24, 'Country Music'),
    (274, 24, 'R&B & Soul'),
    (275, 24, 'K-Pop'),
    (276, 24, 'Music Production'),
    (277, 24, 'Instruments'),
    (278, 25, 'Action & Adventure'),
    (279, 25, 'Animated Films & Series'),
    (280, 25, 'Comedy Films & Series'),
    (281, 25, 'Crime & Mystery'),
    (282, 25, 'Documentaries'),
    (283, 25, 'Drama Films & Series'),
    (284, 25, 'Fantasy'),
    (285, 25, 'Horror'),
    (286, 25, 'Romance'),
    (287, 25, 'Sci-Fi'),
    (288, 25, 'Superheroes'),
    (289, 25, 'Reality TV'),
    (290, 26, 'Book Discussion'),
    (291, 26, 'Book Recommendations'),
    (292, 26, 'Fiction'),
    (293, 26, 'Genre Fiction'),
    (294, 26, 'Non-Fiction'),
    (295, 26, 'Poetry'),
    (296, 26, 'Fan Fiction'),
    (297, 26, 'Creative Writing'),
    (298, 26, 'Publishing & Authors'),
    (299, 27, 'Dogs'),
    (300, 27, 'Cats'),
    (301, 27, 'Birds'),
    (302, 27, 'Fish & Aquariums'),
    (303, 27, 'Reptiles & Amphibians'),
    (304, 27, 'Horses'),
    (305, 27, 'Pet Care');

-- These tables have no unique key but id, so ON CONFLICT (id) DO NOTHING alone would skip a row silently if a
-- database had drifted from the seed (an admin's own category at id 16, say). Stop and name the row instead.
DO $$
DECLARE
    clash text;
BEGIN
    SELECT string_agg(format('category %s is %L, expected %L', c.id, c.name, n.name), '; ' ORDER BY c.id) INTO clash
    FROM categories c JOIN category_expansion_categories n ON n.id = c.id
    WHERE c.name IS DISTINCT FROM n.name;
    IF clash IS NOT NULL THEN
        RAISE EXCEPTION 'category expansion: %', clash;
    END IF;

    SELECT string_agg(format('subcategory %s is %L in category %s, expected %L in category %s',
                             s.id, s.name, s.cat_id, n.name, n.cat_id), '; ' ORDER BY s.id) INTO clash
    FROM subcategories s JOIN category_expansion_subcategories n ON n.id = s.id
    WHERE s.name IS DISTINCT FROM n.name OR s.cat_id IS DISTINCT FROM n.cat_id;
    IF clash IS NOT NULL THEN
        RAISE EXCEPTION 'category expansion: %', clash;
    END IF;
END $$;

INSERT INTO categories (id, color, visible, name, description, icon, is_nsfw)
SELECT id, '805ad5', true, name, '', icon, false FROM category_expansion_categories
ON CONFLICT (id) DO NOTHING;

INSERT INTO subcategories (id, cat_id, name, description, _visible)
SELECT id, cat_id, name, NULL, NULL FROM category_expansion_subcategories
ON CONFLICT (id) DO NOTHING;

DROP TABLE category_expansion_categories;
DROP TABLE category_expansion_subcategories;

SELECT setval('categories_id_seq', (SELECT max(id) FROM categories));
SELECT setval('subcategories_id_seq', (SELECT max(id) FROM subcategories));

ALTER TABLE curations ALTER COLUMN category_filter TYPE character varying(2000);
