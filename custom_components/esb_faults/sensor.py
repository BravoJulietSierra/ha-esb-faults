"""Sensor for ESB Faults."""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import aiohttp
from geopy.distance import geodesic

from homeassistant.helpers.entity import Entity

_LOGGER = logging.getLogger(__name__)

# PowerCheck returns times as day-first local Irish text, e.g. "19/10/2026 16:00"
ESB_TIME_FORMAT = "%d/%m/%Y %H:%M"
ESB_TIME_ZONE = ZoneInfo("Europe/Dublin")


def parse_esb_time(value):
    """Convert a PowerCheck time string to an ISO 8601 string with offset.

    Returns None for empty or unparseable values.
    """
    if not value:
        return None
    try:
        parsed = datetime.strptime(value, ESB_TIME_FORMAT)
    except (TypeError, ValueError):
        _LOGGER.debug("Unparseable PowerCheck time: %s", value)
        return None
    return parsed.replace(tzinfo=ESB_TIME_ZONE).isoformat()


def calculate_distance(known_location, outage_location):
    """Calculate proximity from faults to specified location."""
    user_location = (float(known_location[0]), float(known_location[1]))
    outage_location = (
        float(outage_location.split(",")[0]),
        float(outage_location.split(",")[1]),
    )
    distance = geodesic(user_location, outage_location).kilometers
    return distance


def filter_outages_by_proximity(outages, known_location, max_distance):
    """Filter outages."""
    filtered_outages = []

    if max_distance is not None:
        if known_location is None or not all(known_location):
            _LOGGER.warning("Invalid known_location: %s", known_location)
            return filtered_outages

        for outage in outages:
            outage_location = outage.get("p", {}).get("c")
            if outage_location is not None and "," in outage_location:
                try:
                    distance = calculate_distance(known_location, outage_location)
                    if distance is not None and distance <= max_distance:
                        filtered_outages.append(outage)
                except ValueError:
                    _LOGGER.warning("Invalid outage_location: %s", outage_location)
            else:
                _LOGGER.warning("Invalid outage_location: %s", outage_location)
    else:
        filtered_outages = outages

    return filtered_outages


async def fetch_detailed_outage_data(outage_id, api_subscription_key):
    url = f"https://api.esb.ie/esbn/powercheck/v1.0/outages/{outage_id}"
    headers = {"api-subscription-key": api_subscription_key}

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as response:
            if response.status == 200:
                data = await response.json()
                # Extract the desired detailed outage data from the response
                detailed_outage_data = {
                    "outageType": data["outageType"],
                    "location": data["location"],
                    "plannerGroup": data["plannerGroup"],
                    "numCustAffected": data["numCustAffected"],
                    "startTime": data["startTime"],
                    "estRestoreTime": data["estRestoreTime"],
                    "statusMessage": data["statusMessage"],
                    "restoreTime": data["restoreTime"],
                    # Only populated for planned outages
                    "plannedOutageReason": data.get("plannedOutageReason", ""),
                    # Real timestamps (ISO 8601, Europe/Dublin) for templates
                    "startTimeISO": parse_esb_time(data["startTime"]),
                    "estRestoreTimeISO": parse_esb_time(data["estRestoreTime"]),
                    "restoreTimeISO": parse_esb_time(data["restoreTime"]),
                }
                return detailed_outage_data
            else:
                return None


async def fetch_data_from_api(api_subscription_key):
    """Fetch latest faults via ESB Powercheck API."""
    url = "https://api.esb.ie/esbn/powercheck/v1.0/outages"
    headers = {"api-subscription-key": api_subscription_key}

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as response:
            if response.status == 200:
                data = await response.json()
                # Perform necessary processing and return the desired state
                # Example code:
                return data.get("outageMessage")

            return None


NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
# Nominatim's usage policy requires an identifying User-Agent.
NOMINATIM_HEADERS = {
    "User-Agent": "ha-esb-faults (Home Assistant custom integration; "
    "https://github.com/BravoJulietSierra/ha-esb-faults)"
}
ROAD_KEYS = ("road", "pedestrian", "residential", "footway", "path", "cycleway")


async def fetch_nearest_road(latitude, longitude):
    """Reverse geocode a point to the nearest named road using OpenStreetMap.

    Returns None if the lookup fails or no road is found.
    """
    params = {
        "format": "jsonv2",
        "lat": latitude,
        "lon": longitude,
        "zoom": 17,  # street level
        "addressdetails": 1,
    }
    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(
                NOMINATIM_URL, params=params, headers=NOMINATIM_HEADERS
            ) as response:
                if response.status != 200:
                    _LOGGER.debug("Nominatim returned status %s", response.status)
                    return None
                data = await response.json()
    except (aiohttp.ClientError, TimeoutError, ValueError) as err:
        _LOGGER.debug("Nominatim lookup failed: %s", err)
        return None

    address = data.get("address", {})
    for key in ROAD_KEYS:
        if address.get(key):
            return address[key]
    return None


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up the ESB Faults sensor."""
    latitude = entry.data.get("latitude")
    longitude = entry.data.get("longitude")
    proximity = entry.data.get("proximity")
    api_subscription_key = entry.data.get("api_subscription_key")

    sensor = ESBFaultsSensor(latitude, longitude, proximity, api_subscription_key)
    await sensor.async_update()
    async_add_entities([sensor])


class ESBFaultsSensor(Entity):
    """Representation of an ESB Faults sensor."""

    def __init__(self, latitude, longitude, proximity, api_subscription_key):
        """Init with specicied location, and valid key."""
        self._latitude = latitude
        self._longitude = longitude
        self._proximity = proximity
        self._api_subscription_key = api_subscription_key
        self._state = None
        self._attributes = {}
        # outage id -> nearest road, so each fault is only geocoded once
        self._road_cache = {}

    async def _nearest_road(self, outage_id, latitude, longitude):
        """Return the nearest road for an outage, using a per-id cache."""
        if outage_id not in self._road_cache:
            road = await fetch_nearest_road(latitude, longitude)
            if road is None:
                return None  # don't cache failures, retry next update
            self._road_cache[outage_id] = road
        return self._road_cache[outage_id]

    async def async_update(self):
        """Update the sensor data."""
        outages = await fetch_data_from_api(self._api_subscription_key)
        if outages is not None:
            known_location = (self._latitude, self._longitude)
            max_distance = self._proximity

            filtered_outages = filter_outages_by_proximity(
                outages, known_location, max_distance
            )

            self._state = len(filtered_outages)
            self._attributes = {"outages": []}

            for outage in filtered_outages:
                outage_location = outage["p"]["c"]
                outage_latitude, outage_longitude = map(
                    float, outage_location.split(",")
                )

                outage_distance = geodesic(
                    (self._latitude, self._longitude),
                    (outage_latitude, outage_longitude),
                ).kilometers

                outage_data = {
                    "id": outage["i"],
                    "type": outage["t"],
                    "location": outage_location,
                    # Keep the pin coordinates: the detail call below
                    # overwrites "location" with the area name.
                    "latitude": outage_latitude,
                    "longitude": outage_longitude,
                    "distance": round(outage_distance, 2),
                }
                
                # Fetch detailed outage data for the outage ID
                detailed_outage_data = await fetch_detailed_outage_data(
                    outage["i"], self._api_subscription_key
                )
                if detailed_outage_data is not None:
                    outage_data.update(detailed_outage_data)

                # Add the nearest road alongside the named area,
                # e.g. "Sutton (near Moyclare Rd)".
                road = await self._nearest_road(
                    outage["i"], outage_latitude, outage_longitude
                )
                outage_data["nearest_road"] = road
                if road and detailed_outage_data is not None:
                    outage_data["location_detail"] = (
                        f"{outage_data['location']} (near {road})"
                    )

                self._attributes["outages"].append(outage_data)

            # Forget roads for outages that are no longer listed
            current_ids = {o["id"] for o in self._attributes["outages"]}
            self._road_cache = {
                k: v for k, v in self._road_cache.items() if k in current_ids
            }
        else:
            self._state = None
            self._attributes = {}

    @property
    def unique_id(self):
        """Return the unique ID of the sensor."""
        return "esb_faults_outages"

    @property
    def name(self):
        """Return the name of the sensor."""
        return "ESB Faults Sensor"

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._state

    @property
    def device_class(self):
        """Return the device class of the sensor."""
        return "problem"

    @property
    def extra_state_attributes(self):
        """Return the sensor attributes."""
        return self._attributes
