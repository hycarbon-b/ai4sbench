from __future__ import annotations

import re
from collections.abc import Iterable

from src.core.enums import ReviewerApplicationStatus
from src.db.models import Proposal, ReviewerApplication


def reviewer_matches_proposal(proposal: Proposal, application: ReviewerApplication) -> bool:
    proposal_domains = {part.strip().casefold() for part in proposal.domain.split(",") if part.strip()}
    reviewer_domains = {part.strip().casefold() for part in application.domain.split(",") if part.strip()}
    login = (application.github or "").strip()
    return bool(
        application.user_id
        and application.status == ReviewerApplicationStatus.APPROVED
        and re.fullmatch(r"[A-Za-z\d](?:[A-Za-z\d]|-(?=[A-Za-z\d])){0,38}", login)
        and login.casefold() != (proposal.author_login or "").casefold()
        and proposal_domains.intersection(reviewer_domains)
    )


def reviewer_selector(
    proposal: Proposal, applications: Iterable[ReviewerApplication]
) -> list[ReviewerApplication]:
    """Return approved, domain-compatible people available for manual selection.

    Field names are free text in this benchmark, so they are shown to the
    administrator but are not used as an exact-match eligibility rule.
    """
    seen_logins: set[str] = set()
    seen_users: set[str] = set()
    selected: list[ReviewerApplication] = []
    for application in applications:
        login = (application.github or "").strip()
        key = login.casefold()
        user_id = str(application.user_id) if application.user_id is not None else ""
        if not user_id or user_id in seen_users or key in seen_logins:
            continue
        # The newest application controls review access, even if it is pending
        # or rejected; do not revive an older approved application for that user.
        seen_users.add(user_id)
        if key:
            seen_logins.add(key)
        if not reviewer_matches_proposal(proposal, application):
            continue
        selected.append(application)
    return selected
