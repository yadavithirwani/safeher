"""Minimal Google Maps REST adapter. Never expose provider errors or credentials."""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from urllib.parse import quote

import httpx
from dotenv import load_dotenv

from ..areas import REPO_ROOT
from ..geo import haversine_m

load_dotenv(REPO_ROOT / "backend" / ".env")
KINDS = {"pharmacy", "police", "hospital", "toilets", "cafe", "supermarket"}


class MapsUnavailable(Exception):
    """Safe error code, deliberately without request URLs or provider response bodies."""


class MapsClient:
    def __init__(self, key: str | None = None, transport=None):
        self.key = key if key is not None else os.getenv("GOOGLE_MAPS_API_KEY", "")
        self.transport = transport

    async def request(self, method, url, *, mask=None, body=None, params=None):
        if not self.key:
            raise MapsUnavailable("maps_not_configured")
        headers = {"X-Goog-Api-Key": self.key}
        if mask:
            headers["X-Goog-FieldMask"] = mask
        try:
            async with httpx.AsyncClient(timeout=8, transport=self.transport) as client:
                response = await client.request(method, url, headers=headers, json=body, params=params)
                if response.status_code != 200:
                    raise MapsUnavailable(f"maps_http_{response.status_code}")
                return response.json()
        except (httpx.HTTPError, ValueError):
            raise MapsUnavailable("maps_unavailable") from None

    async def resolve(self, query: str):
        if not 1 <= len(query.strip()) <= 160:
            return {"error": "Provide a place name of at most 160 characters."}
        # Geocoding requires the key as a query parameter; never log this request.
        data = await self.request("GET", "https://maps.googleapis.com/maps/api/geocode/json",
                                  params={"address": query, "key": self.key, "region": "in"})
        if data.get("status") not in {"OK", "ZERO_RESULTS"}:
            raise MapsUnavailable("geocoding_unavailable")
        return {"source": "google_geocoding", "candidates": [
            {"id": r["place_id"], "address": r["formatted_address"],
             "lat": r["geometry"]["location"]["lat"], "lon": r["geometry"]["location"]["lng"]}
            for r in data.get("results", [])[:3]]}

    async def nearby(self, kind: str, lat: float, lon: float):
        if kind not in KINDS:
            return {"error": "Unsupported category", "allowed": sorted(KINDS)}
        google_kind = "public_bathroom" if kind == "toilets" else kind
        mask = ("places.id,places.displayName,places.location,places.formattedAddress,"
                "places.currentOpeningHours,places.googleMapsUri,places.attributions")
        data = await self.request("POST", "https://places.googleapis.com/v1/places:searchNearby",
            mask=mask, body={"includedTypes": [google_kind], "maxResultCount": 3,
                "rankPreference": "DISTANCE", "locationRestriction": {"circle": {
                    "center": {"latitude": lat, "longitude": lon}, "radius": 2500}}})
        places = []
        for p in data.get("places", []):
            point = p["location"]
            places.append({"id": p["id"], "name": p["displayName"]["text"], "kind": kind,
                "lat": point["latitude"], "lon": point["longitude"],
                "address": p.get("formattedAddress"),
                "distance_m": round(haversine_m((lat, lon), (point["latitude"], point["longitude"]))),
                "distance_basis": "straight_line", "source": "google_places",
                "open_now": p.get("currentOpeningHours", {}).get("openNow"),
                "hours_basis": "provider_listing_not_verified",
                "attributions": p.get("attributions", []),
                "retrieved_at": datetime.now(timezone.utc).isoformat()})
        return {"places": places, "source": "google_places"}

    async def details(self, place_id: str):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", place_id):
            return {"error": "Invalid place ID"}
        return await self.request("GET", f"https://places.googleapis.com/v1/places/{quote(place_id)}",
            mask="id,displayName,location,formattedAddress,internationalPhoneNumber,currentOpeningHours,attributions")

    async def walking(self, origin: tuple, destination: tuple):
        def waypoint(p):
            return {"location": {"latLng": {"latitude": p[0], "longitude": p[1]}}}
        data = await self.request("POST", "https://routes.googleapis.com/directions/v2:computeRoutes",
            mask="routes.duration,routes.distanceMeters,routes.warnings",
            body={"origin": waypoint(origin), "destination": waypoint(destination),
                  "travelMode": "WALK", "units": "METRIC"})
        return {"source": "google_routes", "scored": False, "routes": data.get("routes", [])}
