"""Address search through Photon (default) or Google Places."""
import os
import threading
import time
from collections import OrderedDict
from urllib.parse import quote
import httpx

BASE = 'https://places.googleapis.com/v1'

def enabled():
    return bool(os.getenv('GOOGLE_PLACES_API_KEY'))

def google_autocomplete(query, session_token):
    response = httpx.post(BASE + '/places:autocomplete', timeout=10,
        headers={'X-Goog-Api-Key':os.environ['GOOGLE_PLACES_API_KEY']},
        json={'input':query, 'includedRegionCodes':['se'], 'languageCode':'en', 'sessionToken':session_token})
    response.raise_for_status()
    return [{'place_id':p['placeId'], 'name':p['text']['text'], 'provider':'google'}
            for item in response.json().get('suggestions', [])
            if (p := item.get('placePrediction'))]

def details(place_id, session_token=None):
    if not enabled(): raise ValueError('Configure GOOGLE_PLACES_API_KEY to use saved addresses.')
    response = httpx.get(BASE + '/places/' + quote(place_id, safe=''), timeout=10,
        headers={'X-Goog-Api-Key':os.environ['GOOGLE_PLACES_API_KEY'],
                 'X-Goog-FieldMask':'id,formattedAddress,location'},
        params={'sessionToken':session_token} if session_token else {})
    response.raise_for_status()
    data = response.json()
    return {'place_id':data['id'], 'name':data.get('formattedAddress',''),
            'latitude':data['location']['latitude'], 'longitude':data['location']['longitude']}


_cache = OrderedDict()
_lock = threading.Lock()
_last_request = 0.0

def provider():
    return os.getenv('LOCATION_PROVIDER', 'photon').strip().lower()

def autocomplete(query, session_token=''):
    if provider() == 'google':
        if not enabled(): raise ValueError('Google address search needs a GOOGLE_PLACES_API_KEY. Choose Photon for search without a key.')
        return google_autocomplete(query, session_token)
    if provider() != 'photon': raise ValueError('LOCATION_PROVIDER must be photon or google.')
    return photon_autocomplete(query)

def photon_autocomplete(query):
    global _last_request
    base = os.getenv('PHOTON_BASE_URL', 'https://photon.komoot.io').rstrip('/')
    key = (base, query.strip().casefold())
    with _lock:
        cached = _cache.get(key)
        if cached and time.monotonic() - cached[0] < 300:
            _cache.move_to_end(key)
            return cached[1]
        # Keep public demo traffic modest, including concurrent dashboard searches.
        time.sleep(max(0, 1 - (time.monotonic() - _last_request)))
        _last_request = time.monotonic()
        response = httpx.get(base + '/api/', params={'q':query.strip(), 'limit':6, 'lang':'en', 'countrycode':'SE'},
                             headers={'User-Agent':'CommuteCompanion/1.0'}, timeout=10)
        response.raise_for_status()
        results = []
        seen = set()
        for feature in response.json().get('features', []):
            p = feature.get('properties', {})
            geometry = feature.get('geometry', {})
            coords = geometry.get('coordinates', [])
            if geometry.get('type') != 'Point' or len(coords) != 2: continue
            lon, lat = coords
            if not (-90 <= lat <= 90 and -180 <= lon <= 180): continue
            street = ' '.join(str(p[k]) for k in ('street','housenumber') if p.get(k))
            parts = [p.get('name'), street, p.get('postcode'), p.get('city') or p.get('town') or p.get('village'), p.get('state'), p.get('country')]
            name = ', '.join(dict.fromkeys(str(part) for part in parts if part))
            identity = (name, lat, lon)
            if not name or identity in seen: continue
            seen.add(identity)
            results.append({'name':name, 'provider':'photon', 'coordinates':{'latitude':lat,'longitude':lon}})
        _cache[key] = (time.monotonic(), results)
        if len(_cache) > 128: _cache.popitem(last=False)
        return results
