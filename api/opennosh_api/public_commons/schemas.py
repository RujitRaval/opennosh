from datetime import datetime, timedelta
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class CommonsSnapshotState(StrEnum):
    LIVE = "live"
    QUIET = "quiet"
    STALE = "stale"
    PARTIAL = "partial"
    ILLUSTRATIVE = "illustrative"
    UNAVAILABLE = "unavailable"


class CommonsSnapshotReason(StrEnum):
    ACTIVITY_PROJECTION_LAG = "activity_projection_lag"
    INVALID_LATEST_POINTER = "invalid_latest_pointer"
    INVALID_RELEASE_MANIFEST = "invalid_release_manifest"
    LATEST_RELEASE_UNAVAILABLE = "latest_release_unavailable"
    NO_PUBLISHED_RELEASE = "no_published_release"


class AcceptedEventType(StrEnum):
    FOOD = "food"
    SOURCE = "source"
    PORTION = "portion"
    PACK = "pack"


class AcceptedActivityEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: Annotated[str, Field(min_length=1, max_length=128)]
    event_type: AcceptedEventType
    food_or_pack_id: Annotated[str, Field(min_length=1, max_length=160)]
    food_locale: Annotated[str, Field(min_length=1, max_length=80)]
    accepted_at: datetime
    source_commit: Annotated[str, Field(pattern=r"^[0-9a-f]{7,64}$")]
    href: Annotated[
        str, Field(max_length=512, pattern=r"^/[A-Za-z0-9_?&=.%~-][A-Za-z0-9/_?&=.%~-]*$")
    ] | None = None
    summary: Annotated[str, Field(min_length=1, max_length=240)]
    public_contributor_credit: Annotated[str, Field(min_length=1, max_length=100)] | None = None

    @field_validator("accepted_at")
    @classmethod
    def accepted_at_is_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("accepted_at must include a timezone")
        return value


class MostRecentVerifiedRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    record_id: Annotated[str, Field(min_length=1, max_length=160)]
    name: Annotated[str, Field(min_length=1, max_length=160)]
    food_locale: Annotated[str, Field(min_length=1, max_length=80)]
    verified_at: datetime
    href: Annotated[
        str, Field(max_length=512, pattern=r"^/[A-Za-z0-9_?&=.%~-][A-Za-z0-9/_?&=.%~-]*$")
    ]

    @field_validator("verified_at")
    @classmethod
    def verified_at_is_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("verified_at must include a timezone")
        return value


class PublicReleaseProof(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Annotated[str, Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$")]
    manifest_digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    publication_receipt_digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    published_at: datetime

    @field_validator("published_at")
    @classmethod
    def published_at_is_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("published_at must include a timezone")
        return value


class CommonsActivityWindow(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    starts_at: datetime
    ends_at: datetime
    accepted_count: Annotated[int, Field(ge=0)]
    events: Annotated[tuple[AcceptedActivityEvent, ...], Field(max_length=4)] = ()
    most_recent_verified_record: MostRecentVerifiedRecord | None = None

    @model_validator(mode="after")
    def validate_window(self) -> "CommonsActivityWindow":
        if self.starts_at >= self.ends_at:
            raise ValueError("activity window must end after it starts")
        if self.accepted_count < len(self.events):
            raise ValueError("accepted_count cannot be lower than the returned event count")
        return self


class CommonsComponentFreshness(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    release: Literal["verified", "stale", "unavailable"]
    activity: Literal["verified", "partial", "stale", "unavailable"]
    checked_at: datetime
    stale_since: datetime | None = None


class PublicCommonsSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1"] = "1"
    snapshot_id: Annotated[str, Field(min_length=1, max_length=160)]
    as_of: datetime
    state: CommonsSnapshotState
    release: PublicReleaseProof | None
    verified_record_count: Annotated[int, Field(ge=0)] | None
    activity: CommonsActivityWindow
    freshness: CommonsComponentFreshness
    reasons: tuple[CommonsSnapshotReason, ...] = ()

    @model_validator(mode="after")
    def validate_proof_boundary(self) -> "PublicCommonsSnapshot":
        unverified_state = self.state in {
            CommonsSnapshotState.ILLUSTRATIVE,
            CommonsSnapshotState.UNAVAILABLE,
        }
        has_any_proof = self.release is not None or self.verified_record_count is not None
        has_complete_proof = self.release is not None and self.verified_record_count is not None
        if unverified_state and has_any_proof:
            raise ValueError("unverified snapshots cannot claim release proof")
        if not unverified_state and not has_complete_proof:
            raise ValueError("available snapshots require a verified release and count")

        expected_freshness = {
            CommonsSnapshotState.LIVE: ("verified", "verified"),
            CommonsSnapshotState.QUIET: ("verified", "verified"),
            CommonsSnapshotState.STALE: ("stale", "stale"),
            CommonsSnapshotState.PARTIAL: ("verified", "partial"),
            CommonsSnapshotState.ILLUSTRATIVE: ("unavailable", "unavailable"),
            CommonsSnapshotState.UNAVAILABLE: ("unavailable", "unavailable"),
        }[self.state]
        if (self.freshness.release, self.freshness.activity) != expected_freshness:
            raise ValueError("snapshot state and component freshness disagree")
        if (self.state is CommonsSnapshotState.STALE) != (self.freshness.stale_since is not None):
            raise ValueError("only stale snapshots carry stale_since")
        if self.freshness.checked_at != self.as_of:
            raise ValueError("freshness must use the snapshot as_of bucket")
        if self.state is CommonsSnapshotState.STALE:
            if self.activity.ends_at > self.as_of:
                raise ValueError("stale activity cannot be newer than the retry time")
        elif self.activity.ends_at != self.as_of:
            raise ValueError("current activity must use the snapshot as_of bucket")
        if self.activity.ends_at - self.activity.starts_at != timedelta(hours=24):
            raise ValueError("activity window must cover exactly 24 hours")

        events = self.activity.events
        if any(event.href is None for event in events):
            raise ValueError("public activity events require a record link")
        if any(
            event.accepted_at < self.activity.starts_at or event.accepted_at > self.activity.ends_at
            for event in events
        ):
            raise ValueError("activity event falls outside the declared window")
        if self.release is not None:
            if any(event.accepted_at > self.release.published_at for event in events):
                raise ValueError("activity cannot include a newer release event")
            recent = self.activity.most_recent_verified_record
            if recent is not None and recent.verified_at > self.release.published_at:
                raise ValueError("recent record cannot be newer than its release")

        if self.state is CommonsSnapshotState.LIVE and (
            self.activity.accepted_count == 0 or not events
        ):
            raise ValueError("live snapshots require accepted activity")
        if self.state is CommonsSnapshotState.QUIET and (
            self.activity.accepted_count != 0 or events
        ):
            raise ValueError("quiet snapshots cannot contain accepted activity")
        if self.state is CommonsSnapshotState.UNAVAILABLE and (
            self.activity.accepted_count != 0
            or events
            or self.activity.most_recent_verified_record is not None
        ):
            raise ValueError("unavailable snapshots cannot contain activity claims")
        return self
