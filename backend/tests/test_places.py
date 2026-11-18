import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from app import places
from app.core import Session, next_occurrence, TZ
from app.main import app
from app.models import CommuteRule, RulePlace
from app.services import Transit
from datetime import datetime


def test_autocomplete_filters_query_predictions_and_keeps_place_ids(monkeypatch):
    monkeypatch.setenv('GOOGLE_PLACES_API_KEY','test-key')
    monkeypatch.setenv('LOCATION_PROVIDER','google')
    def post(url, **kwargs):
        assert kwargs['json']['includedRegionCodes'] == ['se']
        assert kwargs['json']['sessionToken'] == 'session-1'
        assert kwargs['headers']['X-Goog-Api-Key'] == 'test-key'
        return httpx.Response(200, request=httpx.Request('POST',url), json={'suggestions':[
            {'queryPrediction':{'text':{'text':'query only'}}},
            {'placePrediction':{'placeId':'place-1','text':{'text':'An address, Stockholm'}}}]})
    monkeypatch.setattr(places.httpx,'post',post)
    assert places.autocomplete('an address','session-1') == [
        {'place_id':'place-1','name':'An address, Stockholm','provider':'google'}]


def test_nearby_stops_rejects_distant_feed():
    transit = Transit()
    transit.tables = {'stops':[
        {'stop_id':'near','stop_lat':'59.33','stop_lon':'18.06'},
        {'stop_id':'far','stop_lat':'57.7','stop_lon':'11.97'}]}
    assert [s['stop_id'] for s in transit.nearby({'latitude':59.3301,'longitude':18.0601})] == ['near']
    with pytest.raises(ValueError, match='within 1 km'):
        transit.nearby({'latitude':65,'longitude':20})


def test_place_selection_lifecycle_and_provider_error(monkeypatch):
    monkeypatch.setenv('APP_USERNAME','reviewer')
    monkeypatch.setenv('APP_PASSWORD','a-long-test-password')
    monkeypatch.setenv('GOOGLE_PLACES_API_KEY','test-key')
    monkeypatch.setenv('LOCATION_PROVIDER','google')
    # Avoid scheduling background work; this scenario tests location persistence.
    monkeypatch.setattr('app.main.schedule_rule',lambda db,r:None)
    with TestClient(app) as client:
        client.post('/api/auth/login',json={'username':'reviewer','password':'a-long-test-password'})
        def fail(*args): raise httpx.ConnectError('unavailable')
        monkeypatch.setattr(places,'autocomplete',fail)
        assert client.get('/api/locations?q=Stockholm&session_token=s').status_code == 502
        data = dict(from_location='An address, Stockholm',to_location='Central',from_place_id='place-1',frequency='weekdays',start_time='08:30',alert_before_minutes=10)
        response = client.post('/api/rules',json=data)
        assert response.status_code == 201
        rule_id = response.json()['id']
        try:
            assert client.get(f'/api/rules/{rule_id}').json()['from_place_id'] == 'place-1'
            data['from_location'] = 'Different stop'
            data['from_place_id'] = None
            assert client.patch(f'/api/rules/{rule_id}',json=data).status_code == 200
            assert not client.get(f'/api/rules/{rule_id}').json().get('from_place_id')
        finally:
            client.delete(f'/api/rules/{rule_id}')
        with Session() as db:
            assert not db.scalar(select(RulePlace).where(RulePlace.rule_id==rule_id))


def test_check_is_before_start_and_skips_passed_check_time():
    departure, check = next_occurrence('weekdays',[],'08:30',now=datetime(2026,11,18,8,0,tzinfo=TZ),lead=10)
    assert (check.hour,check.minute) == (8,20)
    assert departure.date() == check.date()
    departure, check = next_occurrence('weekdays',[],'08:30',now=datetime(2026,11,18,8,21,tzinfo=TZ),lead=10)
    assert departure.day == 19


def test_photon_search_without_keys_or_transit_and_coordinate_persistence(monkeypatch):
    from app.models import RuleCoordinates
    monkeypatch.setenv('LOCATION_PROVIDER','photon')
    monkeypatch.delenv('GOOGLE_PLACES_API_KEY',raising=False)
    monkeypatch.delenv('GTFS_STATIC_URL',raising=False)
    monkeypatch.setenv('APP_USERNAME','reviewer')
    monkeypatch.setenv('APP_PASSWORD','a-long-test-password')
    monkeypatch.setattr('app.main.schedule_rule',lambda db,r:None)
    places._cache.clear()
    calls = []
    def get(url, **kwargs):
        calls.append((url,kwargs))
        assert kwargs['params']['countrycode'] == 'SE'
        return httpx.Response(200, request=httpx.Request('GET',url), json={'features':[
            {'properties':{'name':'Stockholm Central','city':'Stockholm','country':'Sweden'},
             'geometry':{'type':'Point','coordinates':[18.0575,59.3299]}}]})
    monkeypatch.setattr(places.httpx,'get',get)
    with TestClient(app) as client:
        client.post('/api/auth/login',json={'username':'reviewer','password':'a-long-test-password'})
        response = client.get('/api/locations?q=Stockholm%20Central')
        assert response.status_code == 200
        result = response.json()[0]
        assert result['provider'] == 'photon'
        point = {'latitude':59.3299,'longitude':18.0575}
        assert result['coordinates'] == point
        assert client.get('/api/locations?q=Stockholm%20Central').json() == response.json()
        assert len(calls) == 1
        data = dict(from_location=result['name'],to_location='Other stop',from_coordinates=point,
                    frequency='weekdays',start_time='08:30',alert_before_minutes=10)
        response = client.post('/api/rules',json=data)
        assert response.status_code == 201
        rule_id = response.json()['id']
        try:
            assert client.get(f'/api/rules/{rule_id}').json()['from_coordinates'] == point
            data['from_location']='Typed another address'
            data['from_coordinates']=None
            assert client.patch(f'/api/rules/{rule_id}',json=data).status_code == 200
            assert not client.get(f'/api/rules/{rule_id}').json().get('from_coordinates')
            data['from_coordinates']=point
            assert client.patch(f'/api/rules/{rule_id}',json=data).status_code == 200
        finally:
            client.delete(f'/api/rules/{rule_id}')
        with Session() as db:
            assert not db.scalar(select(RuleCoordinates).where(RuleCoordinates.rule_id==rule_id))


def test_photon_throttled_response_is_reported(monkeypatch):
    from fastapi import HTTPException
    from app.main import locations
    monkeypatch.setenv('LOCATION_PROVIDER','photon')
    def throttled(*args):
        response=httpx.Response(429,request=httpx.Request('GET','https://photon.komoot.io/api/'))
        response.raise_for_status()
    monkeypatch.setattr(places,'autocomplete',throttled)
    with pytest.raises(HTTPException) as error:
        locations('Stockholm')
    assert error.value.status_code == 503
    assert 'busy' in error.value.detail
