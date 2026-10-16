"""Shared ADK function tools. Evidence is retained only in the current invocation."""
from __future__ import annotations

import asyncio
import re
from datetime import datetime

from google.adk.tools import ToolContext

from .local_data import local_details, local_places, local_search, local_walking
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
    for evidence in context.state.get("turn:evidence", []):
        for place in evidence.get("places", []):
            if abs(place.get("lat", 1000) - lat) < 0.000001 and abs(place.get("lon", 1000) - lon) < 0.000001:
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
    """Search local landmarks and OSM POI names/aliases first. Return candidates requiring confirmation; not a worldwide address geocoder."""
    if not isinstance(query, str) or not 1 <= len(query.strip()) <= 160:
        return {"error": "Provide a place name of at most 160 characters."}
    matches = await asyncio.to_thread(local_search, query)
    if matches:
        return retain(tool_context, {"candidates": matches, "source": "local_pilot_search",
            "warning": "Local name search, not full address geocoding. Confirm the candidate and city."})
    try:
        result = await MapsClient().resolve(query)
    except MapsUnavailable:
        result = {"candidates": [], "source": "local_pilot_search",
            "warning": "Google geocoding unavailable and no local match. Cached data covers only two pilot areas.",
            "error": "location_unresolved"}
    return retain(tool_context, result)



async def search_nearby_places(kind: str, lat: float, lon: float, tool_context: ToolContext) -> dict:
    """Find up to three places within 2.5 km by straight-line distance. Categories: pharmacy, police, hospital, toilets, cafe, supermarket. Use only confirmed coordinates."""
    try:
        confirmed_point(lat, lon, tool_context)
    except ValueError:
        return {"error": "Confirm coordinates or provide a fresh location first."}
    if kind not in KINDS:
        return {"error": "Unsupported category", "allowed": sorted(KINDS)}
    warning = None
    try:
        result = await MapsClient().nearby(kind, lat, lon)
        if result.get("places"):
            return retain(tool_context, result)
        warning = "Google returned no matching places; checking limited cached pilot data."
    except MapsUnavailable:
        warning = "Google Maps unavailable; using limited cached pilot data."
    places = await asyncio.to_thread(local_places, kind, lat, lon)
    result = {"places": places, "source": "openstreetmap", "warning": warning}
    if not places:
        result["warning"] += " No cached matches; this does not mean no assistance exists."
    return retain(tool_context, result)


async def get_place_details(place_id: str, tool_context: ToolContext) -> dict:
    """Get provider-specific details only for a place returned by search in this turn."""
    places = [p for e in tool_context.state.get("turn:evidence", []) for p in e.get("places", [])]
    place = next((p for p in places if p["id"] == place_id), None)
    if place is None:
        return {"error": "Search for this place first."}
    if place["source"] == "openstreetmap":
        return retain(tool_context, await asyncio.to_thread(local_details, place))
    if place["source"] != "google_places":
        return {"error": "Unsupported place source."}
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
    except ValueError:
        return {"error": "Confirm both coordinates before estimating walking access."}
    try:
        result = await MapsClient().walking(origin, destination)
        if result.get("routes"):
            return retain(tool_context, result)
    except MapsUnavailable:
        pass
    result = await asyncio.to_thread(local_walking, origin, destination)
    result["warning"] = "Google walking estimate unavailable. " + result.get(
        "warning", "No usable cached walking connection; do not substitute driving or straight-line time.")
    return retain(tool_context, result)


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
        if "candidates" in item and not item["candidates"]:
            response.reply = "I couldn't resolve that place. Provide a more specific landmark, city, or explicit coordinates."
            response.needs_clarification = True
        if "details" in item:
            details = item["details"]
            for place in response.places:
                if place.get("id") == details.get("id"):
                    place.update(details=details)
        if "places" in item:
            response.places = item["places"]
            response.needs_clarification = False
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
                response.reply = ("Walking estimates are available below; these routes have no SafeHer score."
                                  if response.routes else "No walking estimate is available for these locations.")
    return response
