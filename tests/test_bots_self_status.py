"""A member's own account counts as posting automatically only while that is approved (running or paused): a
proposal still waiting, or one rejected (its row is kept), leaves their scripted posts merely marked (bug hunt)."""
from types import SimpleNamespace as NS

import pytest

from ruqqus.helpers import bots


def own(status):
    return NS(kind=bots.SELF, status=status, owner_id=7, proposed_by_id=7, user_id=7, username="ann")


@pytest.mark.parametrize("status, automated", [
    (bots.APPROVED, True), (bots.PAUSED, True), (bots.PENDING, False), (bots.REJECTED, False),
])
def test_only_approved_automation_makes_a_header_post_automated(status, automated):
    assert bots.is_automated(False, True, own(status)) is automated
