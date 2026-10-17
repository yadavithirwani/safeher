"""Foreground location sharing. Bearer capabilities, latest-point-only storage in the user database."""
from __future__ import annotations

import hashlib
import json
import math
import secrets
import time

from fastapi import APIRouter, Header, HTTPException, Response
from pydantic import BaseModel, Field, field_validator

from . import userdb

router = APIRouter(prefix="/api/trips", tags=["Live sharing"])
TTL = 2 * 60 * 60


class Location(BaseModel):
    lat: float = Field(ge=-90, le=90, allow_inf_nan=False)
    lon: float = Field(ge=-180, le=180, allow_inf_nan=False)
    accuracy_m: float = Field(ge=0, le=100000, allow_inf_nan=False)


class TripRequest(BaseModel):
    # GeoJSON order: longitude, latitude. Only the selected route is shared.
    route: list[tuple[float, float]] = Field(default_factory=list, max_length=5000)

    @field_validator("route")
    @classmethod
    def validate_route(cls, route):
        if not all(math.isfinite(lon) and math.isfinite(lat) and
                   -180 <= lon <= 180 and -90 <= lat <= 90 for lon, lat in route):
            raise ValueError("Invalid route coordinates")
        return route


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def find(token):
    row = userdb.trip_get(digest(token))
    if row is None:
        raise HTTPException(404, "Sharing stopped, expired, or link unavailable.")
    return row


def authorize(row, authorization):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(403, "Owner credential required.")
    if not secrets.compare_digest(row["owner_hash"], digest(authorization[7:])):
        raise HTTPException(403, "Owner credential required.")


@router.post("", status_code=201)
def create_trip(req: TripRequest, response: Response):
    viewer, owner = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    expires = time.time() + TTL
    if not userdb.trip_create(digest(viewer), digest(owner), expires, req.route):
        raise HTTPException(503, "Sharing capacity reached. Try again later.")
    response.headers["Cache-Control"] = "no-store"
    return {"viewer_token": viewer, "owner_token": owner, "expires_at": expires}


@router.get("/{token}")
def read_trip(token: str, response: Response):
    row = find(token)
    response.headers["Cache-Control"] = "no-store"
    return {"expires_at": row["expires"], "route": json.loads(row["route"]),
            "location": None if row["updated"] is None else {
                "lat": row["lat"], "lon": row["lon"], "accuracy_m": row["accuracy"],
                "updated_at": row["updated"]}}


@router.put("/{token}/location", status_code=204)
def update_location(token: str, req: Location, authorization: str | None = Header(default=None)):
    row = find(token)
    authorize(row, authorization)
    userdb.trip_set_location(digest(token), req.lat, req.lon, req.accuracy_m)


@router.delete("/{token}", status_code=204)
def stop_trip(token: str, authorization: str | None = Header(default=None)):
    row = find(token)
    authorize(row, authorization)
    userdb.trip_delete(digest(token))


def purge_expired():
    """Called periodically, even if nobody opens an expired link."""
    userdb.purge_expired_trips()
