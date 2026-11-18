from datetime import datetime
import pytest
from fastapi.testclient import TestClient
from app import sl
from app.core import TZ
from app.main import app


def sample():
    return {'journeys':[{'legs':[{
        'origin':{'id':'start','coord':[59.33,18.05],'departureTimePlanned':'2026-11-18T07:30:00Z','departureTimeEstimated':'2026-11-18T07:35:00Z'},
        'destination':{'id':'end','coord':[59.34,18.06],'arrivalTimePlanned':'2026-11-18T07:40:00Z','arrivalTimeEstimated':'2026-11-18T07:52:00Z'},
        'transportation':{'id':'line-17','disassembledName':'17','product':{'class':2}},
        'properties':{'tripId':'trip-1'},
    }]}]}


def test_sl_uses_coordinates_and_requested_departure_without_gtfs(monkeypatch):
    monkeypatch.delenv('TRANSIT_PROVIDER',raising=False)
    monkeypatch.delenv('GTFS_STATIC_URL',raising=False)
    monkeypatch.delenv('TRAFIKLAB_API_KEY',raising=False)
    calls=[]
    class Response:
        def json(self): return sample()
    def fetch(url, **kwargs):
        calls.append((url,kwargs['params']))
        return Response()
    monkeypatch.setattr(sl,'fetch',fetch)
    point={'latitude':59.33,'longitude':18.05}
    journey,result=sl.check('Central','Odenplan',datetime(2026,11,18,8,30,tzinfo=TZ),point,point)
    assert sl.provider() == 'sl'
    assert len(calls)==1
    assert calls[0][1]['name_origin']=='18.05:59.33:WGS84[DD.DDDDD]'
    assert calls[0][1]['itd_date']=='20261118'
    assert calls[0][1]['itd_time']=='0830'
    assert journey['operator']=='sl' and journey['origin_lat']==59.33
    assert result['delay_minutes']==12


@pytest.mark.parametrize('change',['no_journeys','no_estimate','cancelled','walking','wrong_date'])
def test_sl_does_not_report_all_clear_for_unusable_results(change):
    data=sample()
    leg=data['journeys'][0]['legs'][0]
    if change=='no_journeys': data['journeys']=[]
    if change=='no_estimate': del leg['origin']['departureTimeEstimated']
    if change=='cancelled': data['journeys'][0]['isCancelled']=True
    if change=='walking': leg['transportation']['product']['class']=100
    if change=='wrong_date': leg['origin']['departureTimePlanned']='2026-11-19T07:30:00Z'
    with pytest.raises(ValueError):
        sl.normalize(data,datetime(2026,11,18,8,30,tzinfo=TZ))


def test_rule_check_defaults_to_sl_without_gtfs_configuration(monkeypatch):
    monkeypatch.delenv('TRANSIT_PROVIDER',raising=False)
    monkeypatch.delenv('GTFS_STATIC_URL',raising=False)
    monkeypatch.delenv('TRAFIKLAB_API_KEY',raising=False)
    monkeypatch.setenv('APP_USERNAME','reviewer')
    monkeypatch.setenv('APP_PASSWORD','a-long-test-password')
    monkeypatch.setattr('app.main.schedule_rule',lambda db,r:None)
    def check(*args): return sl.normalize(sample(),datetime(2026,11,18,8,30,tzinfo=TZ))
    monkeypatch.setattr(sl,'check',check)
    with TestClient(app) as client:
        client.post('/api/auth/login',json={'username':'reviewer','password':'a-long-test-password'})
        assert client.get('/api/agent').json()['missing_configuration']==[]
        response=client.post('/api/rules',json=dict(from_location='Central',to_location='Odenplan',frequency='weekdays',start_time='08:30',alert_before_minutes=240))
        assert response.status_code==201
        rule_id=response.json()['id']
        try:
            result=client.post(f'/api/rules/{rule_id}/run-now').json()
            assert result['status']=='on_time'
            assert result['journey']['operator']=='sl'
            assert result['delay']['delay_minutes']==12
        finally:
            client.delete(f'/api/rules/{rule_id}')
