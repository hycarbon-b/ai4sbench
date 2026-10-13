"""Shared domain enums used by SQLAlchemy models and API schemas."""

from enum import StrEnum


class StringEnum(StrEnum):
    """A string-valued enum that stays compatible with existing API values."""


class ProposalStatus(StringEnum):
    DRAFT = "draft"
    PENDING = "pending"
    APPROVED = "approved"
    CHANGES_REQUESTED = "changes_requested"
    REJECTED = "rejected"


class ProposalReviewDecision(StringEnum):
    APPROVED = "approved"
    CHANGES_REQUESTED = "changes_requested"
    REJECTED = "rejected"


class ReviewerApplicationStatus(StringEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class AIReviewDecision(StringEnum):
    STRONG_REJECT = "Strong Reject"
    REJECT = "Reject"
    UNCERTAIN = "Uncertain"
    ACCEPT = "Accept"
    STRONG_ACCEPT = "Strong Accept"


class AIReviewStatus(StringEnum):
    COMPLETED = "completed"
    UNAVAILABLE = "unavailable"
