"""SL Journey Planner v2 adapter; no static GTFS feed or API key required."""
import os
from datetime import datetime, timedelta
from .core import TZ, delay_minutes
from .services import fetch


class NoJourney(ValueError):
    """SL returned no suitable public transport journey, rather than a provider failure."""


def provider():
    return os.getenv('TRANSIT_PROVIDER', 'sl').strip().lower()


def endpoint(path):
    return os.getenv('SL_PLANNER_URL', 'https://journeyplanner.integration.sl.se/v2').rstrip('/') + path


def timestamp(value):
    if not value: raise ValueError('SL did not return the required journey times. Check the SL app before leaving.')
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None: raise ValueError('SL returned a journey time without a timezone.')
    return result.astimezone(TZ)


def location(query, point):
    if point:
        # SL coordinate strings are longitude:latitude, unlike its response arrays.
        return 'coord', f"{point['longitude']}:{point['latitude']}:WGS84[DD.DDDDD]"
    data = fetch(endpoint('/stop-finder'), params={'name_sf':query, 'type_sf':'any', 'any_obj_filter_sf':'46'}).json()
    candidates = [p for p in data.get('locations', []) if p.get('id')]
    if not candidates: raise NoJourney('SL could not find this location. Select an address suggestion and try again.')
    def rank(p):
        exact = query.casefold() in (p.get('name','').casefold(), p.get('disassembledName','').casefold())
        return exact, p.get('type') == 'stop', p.get('isBest', False), p.get('matchQuality', 0)
    return 'any', max(candidates, key=rank)['id']


def normalize(data, departure):
    candidates = []
    for journey in data.get('journeys', data.get('trips', [])):
        legs = journey.get('legs', [])
        transit = [leg for leg in legs if leg.get('transportation', {}).get('product', {}).get('class', 100) < 99]
        if not transit: continue
        if journey.get('isCancelled') or any(leg.get('isCancelled') for leg in transit): continue
        first, last = transit[0], transit[-1]
        start = timestamp(legs[0].get('origin', {}).get('departureTimePlanned'))
        if not departure - timedelta(minutes=5) <= start <= departure + timedelta(minutes=90): continue
        candidates.append((start, journey, transit))
    if not candidates:
        raise NoJourney('SL found no available public transport journey near this start time. Check the locations, time and SL coverage.')
    _, journey, legs = min(candidates, key=lambda item:item[0])
    first, last = legs[0], legs[-1]
    origin, destination = first['origin'], last['destination']
    scheduled = timestamp(origin.get('departureTimePlanned'))
    arrival = timestamp(destination.get('arrivalTimePlanned'))
    estimated = origin.get('departureTimeEstimated')
    estimated_arrival = destination.get('arrivalTimeEstimated')
    if not estimated or not estimated_arrival:
        raise ValueError('SL has no realtime estimate for this departure yet. Check closer to departure or review the SL app.')
    estimated, estimated_arrival = timestamp(estimated), timestamp(estimated_arrival)
    route_ids = [str(leg['transportation'].get('id', 'sl')) for leg in legs]
    names = list(dict.fromkeys(leg['transportation'].get('disassembledName') or leg['transportation'].get('name') or 'SL' for leg in legs))
    trip_ids = [str(leg.get('properties', {}).get('tripId') or leg['transportation'].get('properties', {}).get('RealtimeTripId') or route_ids[i]) for i,leg in enumerate(legs)]
    origin_coords, destination_coords = origin.get('coord'), destination.get('coord')
    if not origin_coords or not destination_coords: raise ValueError('SL did not return stop coordinates for this journey.')
    warnings = ['Source: SL Journey Planner. Delay is the greater of departure and arrival delay.']
    if len(legs)>1: warnings.append(f'This journey includes {len(legs)} transit legs.')
    if any(leg.get('infos') for leg in journey.get('legs', [])):
        warnings.append('SL reports journey notices. Review the SL app for details.')
    return ({'trip_id':' | '.join(trip_ids), 'route_id':' | '.join(route_ids), 'route_name':' → '.join(names),
             'operator':'sl', 'origin_stop_id':str(origin['id']), 'destination_stop_id':str(destination['id']),
             'scheduled_departure':scheduled.isoformat(), 'scheduled_arrival':arrival.isoformat(),
             'origin_lat':origin_coords[0], 'origin_lon':origin_coords[1],
             'destination_lat':destination_coords[0], 'destination_lon':destination_coords[1]},
            {'delay_minutes':max(0, delay_minutes(scheduled,estimated), delay_minutes(arrival,estimated_arrival)),
             'estimated_departure':estimated.isoformat(), 'estimated_arrival':estimated_arrival.isoformat(),
             'warning':' '.join(warnings)})


def check(origin, destination, departure, origin_place=None, destination_place=None):
    origin_type, origin_value = location(origin,origin_place)
    destination_type, destination_value = location(destination,destination_place)
    departure = departure.astimezone(TZ)
    data = fetch(endpoint('/trips'), params={
        'type_origin':origin_type, 'name_origin':origin_value,
        'type_destination':destination_type, 'name_destination':destination_value,
        'calc_number_of_trips':'3', 'language':'en',
        'itd_date':departure.strftime('%Y%m%d'), 'itd_time':departure.strftime('%H%M'),
        'itd_trip_date_time_dep_arr':'dep',
    }).json()
    return normalize(data,departure)
