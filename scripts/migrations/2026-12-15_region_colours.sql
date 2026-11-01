-- Regions' colours (the owner, 2026-10-31: "more of an upbeat colour scheme for the countries ... a good diversity of colours, and that
-- similar colours aren't too close to each other"). The map and the cards on /regions draw each region in regions.color; the old ones
-- were browns, olives, greys and near-blacks, with the same few hues side by side.
--
-- The new ones are 32 bright colours chosen as far apart from each other as the screen allows (none brown or muddy, all clear of the
-- map's grey land), then given to regions by where they sit on the map: regions that border each other, and regions that are close
-- across a strait or a sea, get colours that differ in hue as well as in lightness and still differ for protan and deutan eyes.
-- scripts/region_colours.py makes them (and says how); tests/test_region_colours.py holds the result to those rules, with the map's
-- distances in tests/data/region_neighbours.json. Nothing else reads the column.
--   PYTHONPATH=. python scripts/migrate.py --apply
-- Fresh databases get the same colours from seed-db.sql. Safe to run twice.

UPDATE regions r SET color = v.color
FROM (VALUES
    ('andean_north', '#00ceaa'),
    ('arabian_peninsula', '#fa6c4f'),
    ('balkans', '#fdde0e'),
    ('british_isles', '#fca8fc'),
    ('caribbean', '#fe8df6'),
    ('caucasus_turkiye', '#699df8'),
    ('central_america', '#c67ffb'),
    ('central_asia', '#febf12'),
    ('central_europe', '#8de38c'),
    ('central_southeastern_africa', '#00b9fd'),
    ('danubian_steppe', '#fb637f'),
    ('east_asia', '#db687a'),
    ('eastern_europe_russia', '#03e3fe'),
    ('equatorial_nile', '#e95fa6'),
    ('finland_baltics', '#ac67dd'),
    ('horn_of_africa', '#d0ee65'),
    ('iran_afghanistan', '#2bee9d'),
    ('levant_mesopotamia', '#bf67b2'),
    ('mainland_southeast_asia', '#addf46'),
    ('maritime_southeast_asia', '#d79afc'),
    ('north_africa', '#2ee9ce'),
    ('north_america', '#51b862'),
    ('northern_south_america', '#dcd015'),
    ('oceania', '#81ce74'),
    ('sahel', '#847efe'),
    ('scandinavia', '#fe843d'),
    ('south_asia', '#6487e4'),
    ('southern_africa', '#fb8081'),
    ('southern_cone', '#a283e6'),
    ('southwestern_europe', '#e65639'),
    ('west_africa', '#f7a30d'),
    ('western_central_europe', '#148df7')
) AS v(code, color)
WHERE r.code = v.code AND r.color IS DISTINCT FROM v.color;
