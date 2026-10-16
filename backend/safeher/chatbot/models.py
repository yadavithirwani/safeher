"""Validated public contracts and emergency responses (no model dependency)."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class Position(BaseModel):
    lat: float = Field(ge=-90, le=90, allow_inf_nan=False)
    lon: float = Field(ge=-180, le=180, allow_inf_nan=False)
    accuracy_m: float = Field(ge=0, le=100000, allow_inf_nan=False)
    observed_at: datetime


class TripPoint(BaseModel):
    lat: float = Field(ge=-90, le=90, allow_inf_nan=False)
    lon: float = Field(ge=-180, le=180, allow_inf_nan=False)


class SelectedTrip(BaseModel):
    origin: TripPoint
    destination: TripPoint
    depart_at: datetime

    @field_validator("depart_at")
    @classmethod
    def require_timezone(cls, value):
        if value.tzinfo is None:
            raise ValueError("Departure needs a timezone")
        return value


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    session_id: str | None = Field(default=None, min_length=20, max_length=100)
    sos: bool = False
    location: Position | None = None
    trip: SelectedTrip | None = None
    lang: Literal["en", "hi"] = "en"


class Action(BaseModel):
    type: Literal["call", "request_location", "show_place", "open_directions"]
    label: str
    value: str | None = None


class ChatResponse(BaseModel):
    session_id: str | None = None
    reply: str
    intent: str
    urgency: Literal["normal", "urgent"] = "normal"
    needs_clarification: bool = False
    places: list[dict] = Field(default_factory=list)
    routes: list[dict] = Field(default_factory=list)
    actions: list[Action] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    provider: str = "deterministic"


URGENT = re.compile(
    r"\bsos\b|\bemergency\b|(?:do not|don't|dont) feel safe|"
    r"(?:being|someone is) follow(?:ed|ing)|help me now|in immediate danger|"
    r"मदद चाहिए|सुरक्षित महसूस नहीं|पीछा", re.IGNORECASE,
)


def is_urgent(req: ChatRequest) -> bool:
    return req.sos or bool(URGENT.search(req.message))


def location_warnings(position: Position | None) -> list[str]:
    if position is None:
        return ["Location is missing; provide a landmark or a permission-derived location."]
    observed = position.observed_at
    if observed.tzinfo is None:
        return ["Location timestamp needs a timezone; request a new location."]
    age = (datetime.now(timezone.utc) - observed).total_seconds()
    out = []
    if age > 120 or age < -30:
        out.append("Location is stale or its timestamp is invalid; request a new location.")
    if position.accuracy_m > 100:
        out.append("Location accuracy is limited; confirm a nearby landmark.")
    return out


def emergency_response(req: ChatRequest) -> ChatResponse:
    hindi = req.lang == "hi"
    return ChatResponse(
        session_id=None, intent="during_trip", urgency="urgent",
        reply=("अगर तुरंत खतरा है, भारत में 112 पर कॉल करें। मैं कॉल नहीं कर सकता या मदद नहीं भेज सकता। "
               "नज़दीकी सहायता खोजने के लिए अपनी जगह या कोई पहचान बताएं।" if hindi else
               "If you are in immediate danger, call 112 in India. I cannot place calls or dispatch help. "
               "To look for nearby assistance, share a location or landmark in your next message."),
        actions=[Action(type="call", label="India emergency: 112", value="tel:112"),
                 Action(type="request_location", label="Provide or refresh location")],
        warnings=["112 is configured for India, not a worldwide emergency number.",
                  *location_warnings(req.location)],
    )
