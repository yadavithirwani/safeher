"""Shared ADK function tools. Evidence is retained only in the current invocation."""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone

from google.adk.tools import ToolContext

from ..areas import area_for
from ..routing import load_area
from .maps import KINDS, MapsClient, MapsUnavailable
from .models import ChatResponse


QUESTIONS = {
    "location": "Near which location: your current position, origin, or destination? Provide a landmark or coordinates.",
    "destination": "What is your destination? If you mean home, provide its address for this session.",
    "origin": "Where will you start your walk? Please include the city or a nearby landmark.",
    "departure": "What date and local time will you leave? Include the city or timezone.",
    "ambiguous": "Please confirm which location you mean before I search or plan a route.",
    "scope": "I can help compare walking routes, find local places, or offer emergency call options in India.",
}


def coordinates(lat: float, lon: float):
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("Invalid coordinates")
    return lat, lon


def confirmed_point(lat, lon, context):
    coordinates(lat, lon)
    location = context.state.get("turn:location", {})
    if location and abs(location["lat"] - lat) < 0.000001 and abs(location["lon"] - lon) < 0.000001:
        return lat, lon
    text = context.state.get("turn:coordinate_evidence", "")
    numbers = [float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", text)]
    if not all(any(abs(value - number) < 0.000001 for number in numbers) for value in (lat, lon)):
        raise ValueError("Confirm coordinates in the conversation before using this location.")
    return lat, lon


def retain(context, result):
    evidence = list(context.state.get("turn:evidence", []))
    evidence.append(result)
    context.state["turn:evidence"] = evidence
    return result


async def resolve_location(query: str, tool_context: ToolContext) -> dict:
    """Resolve an explicit place name. Present candidates and obtain user confirmation before using them."""
    from ..api import LANDMARKS  # lazy import avoids app initialization cycle
    matches = [{"name": name, "lat": lat, "lon": lon, "source": "safeher_landmark"}
               for rows in LANDMARKS.values() for name, lat, lon in rows
               if query.strip() and query.casefold() in name.casefold()][:3]
    if matches:
        return retain(tool_context, {"candidates": matches, "source": "safeher_landmarks"})
    try:
        return retain(tool_context, await MapsClient().resolve(query))
    except MapsUnavailable as exc:
        return {"error": str(exc), "instruction": "Ask for coordinates or a known pilot landmark."}


def local_places(kind, lat, lon):
    area = area_for(lat, lon)
    if area is None:
        return []
    data = load_area(area.id)
    now = datetime.now(area.tz)
    results = []
    for distance, poi in (data.safe_spaces if kind != "toilets" else data.stops).within((lat, lon), 2500):
        if poi.kind != kind:
            continue
        opened, assumed = poi.is_open(now)
        results.append({"id": poi.osm, "name": poi.name or kind, "kind": kind,
            "lat": poi.lat, "lon": poi.lon, "distance_m": round(distance),
            "distance_basis": "straight_line", "source": "openstreetmap",
            "open_now": opened, "hours_basis": "assumed" if assumed else "mapped_not_verified",
            "attributions": ["© OpenStreetMap contributors (ODbL)"],
            "retrieved_at": datetime.now(timezone.utc).isoformat()})
        if len(results) == 3:
            break
    return results


async def search_nearby_places(kind: str, lat: float, lon: float, tool_context: ToolContext) -> dict:
    """Find up to three places within 2.5 km by straight-line distance. Categories: pharmacy, police, hospital, toilets, cafe, supermarket. Use only confirmed coordinates."""
    try:
        confirmed_point(lat, lon, tool_context)
    except ValueError:
        return {"error": "Confirm coordinates or provide a fresh location first."}
    if kind not in KINDS:
        return {"error": "Unsupported category", "allowed": sorted(KINDS)}
    try:
        result = await MapsClient().nearby(kind, lat, lon)
    except MapsUnavailable:
        places = await asyncio.to_thread(local_places, kind, lat, lon)
        result = {"places": places, "source": "openstreetmap",
                  "warning": "Google Maps unavailable; using limited cached pilot data."}
    return retain(tool_context, result)


async def get_place_details(place_id: str, tool_context: ToolContext) -> dict:
    """Get Google details only for a place returned by search in this turn."""
    places = [p for e in tool_context.state.get("turn:evidence", []) for p in e.get("places", [])]
    if not any(p["id"] == place_id and p["source"] == "google_places" for p in places):
        return {"error": "Search for this place first."}
    try:
        return retain(tool_context, {"details": await MapsClient().details(place_id), "source": "google_places"})
    except MapsUnavailable as exc:
        return {"error": str(exc)}


async def estimate_walking_access(origin_lat: float, origin_lon: float, destination_lat: float,
                                  destination_lon: float, tool_context: ToolContext) -> dict:
    """Estimate walking time and distance using confirmed coordinates. Does not produce a safety score."""
    try:
        origin = confirmed_point(origin_lat, origin_lon, tool_context)
        destination = confirmed_point(destination_lat, destination_lon, tool_context)
        return retain(tool_context, await MapsClient().walking(origin, destination))
    except (MapsUnavailable, ValueError):
        return {"error": "Walking estimate unavailable; do not substitute driving or straight-line time."}


async def plan_supported_trip(origin_lat: float, origin_lon: float, destination_lat: float,
                              destination_lon: float, depart_at: str, tool_context: ToolContext) -> dict:
    """Compare pilot-area walking routes. depart_at must be a user-confirmed ISO date/time with timezone. Home must be explicitly resolved; never guessed."""
    from ..api import _reports_for_planning
    from ..plan import plan
    try:
        origin = confirmed_point(origin_lat, origin_lon, tool_context)
        destination = confirmed_point(destination_lat, destination_lon, tool_context)
        when = datetime.fromisoformat(depart_at)
        if depart_at not in tool_context.state.get("turn:coordinate_evidence", ""):
            raise ValueError("Departure must be explicitly confirmed as an ISO timestamp for this initial backend version.")
        if when.tzinfo is None:
            return {"error": "Provide an explicit date, time and timezone."}
        result = await asyncio.to_thread(plan, origin, destination, when, _reports_for_planning())
    except ValueError as exc:
        return {"error": str(exc)}
    # Avoid sending every route coordinate/piece to the model.
    routes = [{k: r[k] for k in ("label", "distance_m", "duration_min", "score", "band",
               "breakdown", "reasons", "weak_stretches", "plan_b")} for r in result["routes"]]
    return retain(tool_context, {"routes": routes, "source": "safeher",
        "departure_local": result["departure_local"], "warnings": result["data_notes"],
        "disclaimer": result["disclaimer"]})


def get_plan_b_coverage(route_label: str, tool_context: ToolContext) -> dict:
    """Read Plan B for a route computed in this turn, not an arbitrary trip or private session."""
    for evidence in tool_context.state.get("turn:evidence", []):
        for route in evidence.get("routes", []):
            if route.get("label") == route_label and "plan_b" in route:
                return {"plan_b": route["plan_b"], "route_label": route_label}
    return {"error": "Plan a supported trip first."}


def get_emergency_actions(tool_context: ToolContext) -> dict:
    """Escalate to fixed India emergency actions; does not call or dispatch anyone."""
    tool_context.state["turn:emergency"] = True
    return {"number": "112", "jurisdiction": "India", "dispatch": False}


def ask_clarification(topic: str, tool_context: ToolContext) -> dict:
    """Ask a grounded clarification. Topics: location, destination, origin, departure, ambiguous, scope."""
    topic = topic if topic in QUESTIONS else "scope"
    tool_context.state["turn:clarification"] = topic
    return {"question": QUESTIONS[topic]}


def render_evidence(evidence: list[dict]) -> ChatResponse:
    """Use tool facts, not unchecked model prose, for place/route claims and actions."""
    from .models import Action
    response = ChatResponse(reply="I couldn't find a supported result. Try a specific landmark or location.",
                            intent="local_enquiry", provider="adk")
    for item in evidence:
        if item.get("warning"):
            response.warnings.append(item["warning"])
        if "candidates" in item and item["candidates"]:
            candidates = item["candidates"]
            response.reply = "Please confirm the location: " + "; ".join(
                str(c.get("name") or c.get("address")) for c in candidates)
            response.needs_clarification = True
            response.places = candidates
        if "places" in item:
            response.places = item["places"]
            if response.places:
                response.reply = "Nearby results (straight-line distance, not walking distance): " + "; ".join(
                    f"{p['name']} — {p['distance_m']} m; hours: {p['hours_basis']}"
                    for p in response.places)
                response.warnings.append("Opening hours do not verify actual availability or willingness to assist.")
                response.actions = [Action(type="show_place", label=p["name"], value=p["id"])
                                    for p in response.places]
        if "routes" in item:
            response.routes = item["routes"]
            response.intent = "pre_trip"
            if item.get("source") == "safeher":
                response.reply = "Walking route comparison: " + "; ".join(
                    f"{r['label']}: {r['duration_min']} min, {r['distance_m']} m, support score {r['score']}"
                    for r in response.routes)
                response.warnings.extend(item.get("warnings", []))
                response.warnings.append(item["disclaimer"])
            else:
                response.reply = "Google walking estimates are available below; these routes have no SafeHer score."
    return response
