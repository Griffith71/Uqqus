import time

from flask import *

from ruqqus.helpers.wrappers import *
from ruqqus.helpers.regions import REGION_COUNTRIES, COUNTRY_NAMES
from ruqqus.classes import *
from ruqqus.__main__ import app

# jsvectormap's bundled "world" map renders these 3 unrecognized territories as
# separate shapes with no ISO code of their own; alias each to the ISO code of
# the territory it has no separate geo-detection from, purely so the map colors
# them the same as their region instead of leaving them grey. Not used for any
# actual geo-resolution - COUNTRY_TO_REGION stays ISO-only for that.
MAP_LIBRARY_TERRITORY_ALIASES = {"_0": "CY", "_1": "XK", "_2": "SO"}


@app.route("/regions", methods=["GET"])
@auth_desired
def regions_list(v):

    regions = g.db.query(Region).order_by(Region.default_name.asc()).all()

    proposals_by_region = {
        region.id: g.db.query(RegionNameProposal)
                        .filter_by(region_id=region.id)
                        .order_by(RegionNameProposal.vote_count.desc())
                        .limit(5)
                        .all()
        for region in regions
    }

    my_vote_by_region = {}
    if v and v.display_region:
        my_region = g.db.query(Region).filter_by(code=v.display_region).first()
        if my_region:
            my_vote = g.db.query(RegionNameVote).filter_by(
                region_id=my_region.id, user_id=v.id).first()
            if my_vote:
                my_vote_by_region[my_region.id] = my_vote.proposal_id

    # alpha-2 country code -> that region's colour, for the map's per-country series
    country_colors = {
        cc: region.color
        for region in regions
        for cc in REGION_COUNTRIES.get(region.code, [])
    }

    # extend with the 3 synthetic map-library-only territory keys so they
    # render in their aliased parent's color instead of default grey
    for map_key, iso_code in MAP_LIBRARY_TERRITORY_ALIASES.items():
        if iso_code in country_colors:
            country_colors[map_key] = country_colors[iso_code]

    # region code -> sorted readable country names, for the collapsible list
    region_country_names = {
        code: [COUNTRY_NAMES.get(cc, cc) for cc in sorted(countries, key=lambda c: COUNTRY_NAMES.get(c, c))]
        for code, countries in REGION_COUNTRIES.items()
    }

    return render_template(
        "regions.html",
        v=v,
        regions=regions,
        proposals_by_region=proposals_by_region,
        my_vote_by_region=my_vote_by_region,
        region_countries=REGION_COUNTRIES,
        region_country_names=region_country_names,
        country_colors=country_colors,
    )


@app.route("/regions/<region_code>/propose", methods=["POST"])
@auth_required
@validate_formkey
def regions_propose_name(region_code, v):

    if v.display_region != region_code:
        abort(403)

    region = g.db.query(Region).filter_by(code=region_code).first()
    if not region:
        abort(404)

    proposed_name = request.form.get("proposed_name", "").strip()
    if not proposed_name or len(proposed_name) > 64:
        abort(400)

    g.db.add(RegionNameProposal(
        region_id=region.id,
        proposed_name=proposed_name,
        created_by_id=v.id,
        created_utc=int(time.time()),
    ))
    g.db.commit()

    return redirect("/regions")


@app.route("/regions/<region_code>/vote/<int:proposal_id>", methods=["POST"])
@auth_required
@validate_formkey
def regions_vote(region_code, proposal_id, v):

    if v.display_region != region_code:
        abort(403)

    region = g.db.query(Region).filter_by(code=region_code).first()
    if not region:
        abort(404)

    proposal = g.db.query(RegionNameProposal).filter_by(
        id=proposal_id, region_id=region.id).first()
    if not proposal:
        abort(404)

    existing_vote = g.db.query(RegionNameVote).filter_by(
        region_id=region.id, user_id=v.id).first()

    if existing_vote and existing_vote.proposal_id == proposal.id:
        return redirect("/regions")

    if existing_vote:
        old_proposal = g.db.query(RegionNameProposal).filter_by(
            id=existing_vote.proposal_id).first()
        if old_proposal:
            old_proposal.vote_count = max(0, (old_proposal.vote_count or 0) - 1)
            g.db.add(old_proposal)

        existing_vote.proposal_id = proposal.id
        existing_vote.created_utc = int(time.time())
        g.db.add(existing_vote)
    else:
        g.db.add(RegionNameVote(
            region_id=region.id,
            proposal_id=proposal.id,
            user_id=v.id,
            created_utc=int(time.time()),
        ))

    proposal.vote_count = (proposal.vote_count or 0) + 1
    g.db.add(proposal)
    g.db.commit()

    # highest-voted proposal becomes the region's current display name
    leader = g.db.query(RegionNameProposal).filter_by(
        region_id=region.id
    ).order_by(
        RegionNameProposal.vote_count.desc(),
        RegionNameProposal.created_utc.asc()
    ).first()

    if leader and leader.proposed_name != region.current_name:
        region.current_name = leader.proposed_name
        g.db.add(region)
        g.db.commit()

    return redirect("/regions")
