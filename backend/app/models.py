from datetime import datetime, timezone
from sqlalchemy import String, Integer, Boolean, Float, Text, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .core import Base

def utcnow(): return datetime.now(timezone.utc).isoformat()
class User(Base):
    __tablename__='users'
    id: Mapped[int] = mapped_column(primary_key=True)
    default_threshold: Mapped[int] = mapped_column(default=10)
    preferred_product: Mapped[str | None] = mapped_column(String, nullable=True)
    email: Mapped[str | None] = mapped_column(String, nullable=True)
class CommuteRule(Base):
    __tablename__='rules'
    id: Mapped[int] = mapped_column(primary_key=True)
    from_location: Mapped[str] = mapped_column(String)
    to_location: Mapped[str] = mapped_column(String)
    frequency: Mapped[str] = mapped_column(String)
    selected_days: Mapped[str] = mapped_column(String, default='')
    start_time: Mapped[str] = mapped_column(String)
    alert_before_minutes: Mapped[int] = mapped_column(Integer)
    notification_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    next_run: Mapped[str] = mapped_column(String)
class JourneyCheck(Base):
    __tablename__='journey_checks'
    __table_args__=(UniqueConstraint('idempotency_key'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    rule_id: Mapped[int] = mapped_column(ForeignKey('rules.id', ondelete='CASCADE'))
    idempotency_key: Mapped[str] = mapped_column(String)
    created_at: Mapped[str] = mapped_column(String, default=utcnow)
    status: Mapped[str] = mapped_column(String, default='checking')
    decision: Mapped[str | None] = mapped_column(String, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
class TransitJourney(Base):
    __tablename__='transit_journeys'
    id: Mapped[int] = mapped_column(primary_key=True)
    check_id: Mapped[int] = mapped_column(ForeignKey('journey_checks.id', ondelete='CASCADE'))
    trip_id: Mapped[str] = mapped_column(String)
    route_id: Mapped[str] = mapped_column(String)
    route_name: Mapped[str] = mapped_column(String)
    operator: Mapped[str] = mapped_column(String)
    origin_stop_id: Mapped[str] = mapped_column(String)
    destination_stop_id: Mapped[str] = mapped_column(String)
    scheduled_departure: Mapped[str] = mapped_column(String)
    scheduled_arrival: Mapped[str] = mapped_column(String)
    origin_lat: Mapped[float] = mapped_column(Float)
    origin_lon: Mapped[float] = mapped_column(Float)
    destination_lat: Mapped[float] = mapped_column(Float)
    destination_lon: Mapped[float] = mapped_column(Float)
class DelayResult(Base):
    __tablename__='delay_results'
    id: Mapped[int] = mapped_column(primary_key=True)
    check_id: Mapped[int] = mapped_column(ForeignKey('journey_checks.id', ondelete='CASCADE'))
    delay_minutes: Mapped[int] = mapped_column(Integer)
    estimated_departure: Mapped[str] = mapped_column(String)
    estimated_arrival: Mapped[str] = mapped_column(String)
    warning: Mapped[str | None] = mapped_column(Text, nullable=True)
class Notification(Base):
    __tablename__='notifications'
    id: Mapped[int] = mapped_column(primary_key=True)
    check_id: Mapped[int] = mapped_column(ForeignKey('journey_checks.id', ondelete='CASCADE'))
    title: Mapped[str] = mapped_column(String, default='Commute Delay Alert')
    message: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String, default='in_app')
class UberRide(Base):
    __tablename__='uber_rides'
    id: Mapped[int] = mapped_column(primary_key=True)
    check_id: Mapped[int | None] = mapped_column(ForeignKey('journey_checks.id', ondelete='CASCADE'), nullable=True)
    request_id: Mapped[str] = mapped_column(String, unique=True)
    status: Mapped[str] = mapped_column(String)
    product_name: Mapped[str] = mapped_column(String)
    fare: Mapped[str | None] = mapped_column(String, nullable=True)
    pickup: Mapped[str] = mapped_column(String)
    destination: Mapped[str] = mapped_column(String)
    pickup_time: Mapped[str] = mapped_column(String)
    eta: Mapped[str | None] = mapped_column(String, nullable=True)
    driver: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String, default=utcnow)
class UberAccount(Base):
    __tablename__='uber_accounts'
    id: Mapped[int] = mapped_column(primary_key=True)
    encrypted_refresh_token: Mapped[str] = mapped_column(Text)

class WorkspaceSession(Base):
    __tablename__ = 'workspace_sessions'
    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String, unique=True)
    expires_at: Mapped[int] = mapped_column(Integer)

class RulePlace(Base):
    __tablename__ = 'rule_places'
    __table_args__ = (UniqueConstraint('rule_id', 'kind'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    rule_id: Mapped[int] = mapped_column(ForeignKey('rules.id', ondelete='CASCADE'))
    kind: Mapped[str] = mapped_column(String)
    place_id: Mapped[str] = mapped_column(String)


class RuleCoordinates(Base):
    __tablename__ = 'rule_coordinates'
    __table_args__ = (UniqueConstraint('rule_id', 'kind'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    rule_id: Mapped[int] = mapped_column(ForeignKey('rules.id', ondelete='CASCADE'))
    kind: Mapped[str] = mapped_column(String)
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)


class AlternativePlan(Base):
    __tablename__ = 'alternative_plans'
    id: Mapped[int] = mapped_column(primary_key=True)
    check_id: Mapped[int] = mapped_column(ForeignKey('journey_checks.id', ondelete='CASCADE'), unique=True)
    payload: Mapped[str] = mapped_column(Text)
