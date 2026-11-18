import json
from datetime import datetime
import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from app import alternatives, sl
from app.core import Session, TZ
from app.main import app, scheduler
from app.models import AlternativePlan, JourneyCheck, RuleCoordinates

DEPARTURE=datetime(2026,11,18,8,30,tzinfo=TZ)


def test_without_key_offers_operator_links_without_calling_gemini(monkeypatch):
    monkeypatch.delenv('GEMINI_API_KEY',raising=False)
    def forbidden(*args,**kwargs): raise AssertionError('No Gemini call without credentials')
    monkeypatch.setattr(alternatives.httpx,'post',forbidden)
    result=alternatives.suggest('Stockholm','Gothenburg',DEPARTURE,'SL found no journey.')
    assert result['source']=='operator_links' and result['ai_status']=='not_configured'
    assert {option['operator'] for option in result['options']}=={'SJ','Vy','FlixBus'}
    assert result['availability_verified'] is False


def test_gemini_suggestions_are_validated_and_links_supplied_by_application(monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY','test-key')
    def post(url,**kwargs):
        assert kwargs['headers']['x-goog-api-key']=='test-key'
        prompt=kwargs['json']['contents'][0]['parts'][0]['text']
        assert 'Stockholm' in prompt and '2026-11-18T08:30:00+01:00' in prompt
        assert 'responseJsonSchema' in kwargs['json']['generationConfig']
        return httpx.Response(200,request=httpx.Request('POST',url),json={'candidates':[{
            'finishReason':'STOP','content':{'parts':[{'text':json.dumps({'options':[
                {'operator':'SJ','reason':'Check for a train between the cities, allowing time to reach the station.'}]})}]}}]})
    monkeypatch.setattr(alternatives.httpx,'post',post)
    result=alternatives.suggest('Stockholm','Gothenburg',DEPARTURE,'No SL route')
    assert result['source']=='gemini'
    assert result['options'][0]['url']=='https://www.sj.se/en'


@pytest.mark.parametrize('failure',['timeout','unauthorized','malformed','unsafe_operator','blocked'])
def test_gemini_failures_keep_operator_links(failure,monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY','test-key')
    def post(url,**kwargs):
        request=httpx.Request('POST',url)
        if failure=='timeout': raise httpx.ReadTimeout('private upstream error',request=request)
        if failure=='unauthorized': return httpx.Response(403,request=request,json={'error':'private upstream error'})
        text='not valid JSON' if failure=='malformed' else json.dumps({'options':[{'operator':'unknown','reason':'invalid'}]})
        return httpx.Response(200,request=request,json={'candidates':[{'finishReason':'SAFETY' if failure=='blocked' else 'STOP','content':{'parts':[{'text':text}]}}]})
    monkeypatch.setattr(alternatives.httpx,'post',post)
    result=alternatives.suggest('Stockholm','Gothenburg',DEPARTURE,'No SL route')
    assert result['source']=='operator_links' and result['ai_status']=='unavailable'
    assert len(result['options'])==3
    assert 'private upstream error' not in json.dumps(result)


def test_no_sl_journey_is_saved_as_alternatives_and_deletion_removes_history_and_schedule(monkeypatch):
    monkeypatch.setenv('TRANSIT_PROVIDER','sl')
    monkeypatch.setenv('APP_USERNAME','reviewer')
    monkeypatch.setenv('APP_PASSWORD','a-long-test-password')
    monkeypatch.delenv('GEMINI_API_KEY',raising=False)
    def no_route(*args): raise sl.NoJourney('SL found no available public transport journey.')
    def no_booking(*args,**kwargs): raise AssertionError('No ride must be booked from alternative suggestions')
    monkeypatch.setattr(sl,'check',no_route)
    monkeypatch.setattr('app.main.uber.book',no_booking)
    with TestClient(app) as client:
        client.post('/api/auth/login',json={'username':'reviewer','password':'a-long-test-password'})
        response=client.post('/api/rules',json=dict(from_location='Stockholm',to_location='Gothenburg',from_coordinates={'latitude':59.33,'longitude':18.05},frequency='weekdays',start_time='08:30',alert_before_minutes=10))
        assert response.status_code==201
        rule_id=response.json()['id']
        assert scheduler.get_job(f'rule-{rule_id}') is not None
        result=client.post(f'/api/rules/{rule_id}/run-now').json()
        assert result['status']=='alternatives' and result['decision']=='no_sl_journey'
        assert result['ride'] is None and result['delay'] is None
        assert result['alternatives']['source']=='operator_links'
        assert client.get(f'/api/rules/{rule_id}/history').json()[0]['alternatives']==result['alternatives']
        check_id=result['id']
        assert client.delete(f'/api/rules/{rule_id}').status_code==200
        assert scheduler.get_job(f'rule-{rule_id}') is None
        assert client.get(f'/api/rules/{rule_id}').status_code==404
        with Session() as db:
            assert not db.get(JourneyCheck,check_id)
            assert not db.scalar(select(AlternativePlan).where(AlternativePlan.check_id==check_id))
            assert not db.scalar(select(RuleCoordinates).where(RuleCoordinates.rule_id==rule_id))


def test_sl_outage_remains_error_instead_of_claiming_no_transport(monkeypatch):
    monkeypatch.setenv('TRANSIT_PROVIDER','sl')
    monkeypatch.setenv('APP_USERNAME','reviewer')
    monkeypatch.setenv('APP_PASSWORD','a-long-test-password')
    monkeypatch.setattr('app.main.schedule_rule',lambda db,r:None)
    def failed(*args): raise httpx.ConnectError('network failure')
    def no_ai(*args): raise AssertionError('An outage must not be described as no public transport')
    monkeypatch.setattr(sl,'check',failed)
    monkeypatch.setattr(alternatives,'suggest',no_ai)
    with TestClient(app) as client:
        client.post('/api/auth/login',json={'username':'reviewer','password':'a-long-test-password'})
        rule_id=client.post('/api/rules',json=dict(from_location='Central',to_location='Odenplan',frequency='weekdays',start_time='08:30',alert_before_minutes=10)).json()['id']
        try:
            result=client.post(f'/api/rules/{rule_id}/run-now').json()
            assert result['status']=='error' and result['alternatives'] is None
        finally:
            client.delete(f'/api/rules/{rule_id}')
