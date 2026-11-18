import json, logging, os, re, secrets, threading, uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from collections import defaultdict, deque
from typing import Literal
import httpx
from cryptography.fernet import Fernet
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, JSONResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select, delete
from sqlalchemy.exc import IntegrityError
from apscheduler.schedulers.background import BackgroundScheduler
from .core import Base, engine, Session, TZ, DAYS, next_occurrence
from .models import *
from .services import transit, uber, DELAY_MESSAGE, ONTIME_MESSAGE
from . import auth, places, sl, alternatives
logging.basicConfig(level=logging.INFO, format='%(message)s'); log=logging.getLogger('commute')
Base.metadata.create_all(engine)
scheduler=BackgroundScheduler(timezone=TZ)
oauth_states={}; requests_by_ip=defaultdict(deque)
rule_locks=defaultdict(threading.RLock)
class Coordinates(BaseModel):
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)

class RuleInput(BaseModel):
    from_location: str = Field(min_length=1,max_length=200)
    to_location: str = Field(min_length=1,max_length=200)
    frequency: Literal['weekdays','weekends','custom']
    selected_days: list[Literal['MON','TUE','WED','THU','FRI','SAT','SUN']] = []
    start_time: str
    alert_before_minutes: int = Field(ge=0,le=240)
    notification_enabled: bool = True
    from_place_id: str | None = Field(default=None, max_length=500)
    to_place_id: str | None = Field(default=None, max_length=500)
    from_coordinates: Coordinates | None = None
    to_coordinates: Coordinates | None = None
    @model_validator(mode='after')
    def valid(self):
        if not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',self.start_time): raise ValueError('Start time must be HH:MM')
        if self.frequency=='custom' and not self.selected_days: raise ValueError('Choose at least one day')
        if not self.from_location.strip() or not self.to_location.strip(): raise ValueError('Locations cannot be blank')
        return self
class Settings(BaseModel):
    default_threshold: int = Field(ge=0,le=240)
    preferred_product: str | None = None
    email: str | None = None

def serialize(obj):
    if not obj: return None
    return {c.name:getattr(obj,c.name) for c in obj.__table__.columns}
def rule_data(r):
    d=serialize(r); d['selected_days']=r.selected_days.split(',') if r.selected_days else []
    with Session() as db:
        for point in db.scalars(select(RuleCoordinates).where(RuleCoordinates.rule_id==r.id)):
            d[point.kind + '_coordinates'] = {'latitude':point.latitude,'longitude':point.longitude}
        for place in db.scalars(select(RulePlace).where(RulePlace.rule_id==r.id)):
            d[place.kind + '_place_id'] = place.place_id
    return d

def save_places(db, rule_id, values):
    db.execute(delete(RulePlace).where(RulePlace.rule_id==rule_id))
    db.execute(delete(RuleCoordinates).where(RuleCoordinates.rule_id==rule_id))
    for kind in ('from','to'):
        if values.get(kind + '_coordinates'):
            db.add(RuleCoordinates(rule_id=rule_id, kind=kind, **values[kind + '_coordinates']))
        elif values.get(kind + '_place_id'):
            db.add(RulePlace(rule_id=rule_id, kind=kind, place_id=values[kind + '_place_id']))
    db.commit()
def alternative_data(db,check_id):
    plan=db.scalar(select(AlternativePlan).where(AlternativePlan.check_id==check_id))
    return json.loads(plan.payload) if plan else None

def history(db,rule_id):
    checks=db.scalars(select(JourneyCheck).where(JourneyCheck.rule_id==rule_id).order_by(JourneyCheck.id.desc()).limit(50)).all()
    return [{**serialize(c),'alternatives':alternative_data(db,c.id),'journey':serialize(db.scalar(select(TransitJourney).where(TransitJourney.check_id==c.id))),'delay':serialize(db.scalar(select(DelayResult).where(DelayResult.check_id==c.id))),'notification':serialize(db.scalar(select(Notification).where(Notification.check_id==c.id))),'ride':serialize(db.scalar(select(UberRide).where(UberRide.check_id==c.id)))} for c in checks]
def run_check(rule_id, occurrence=None, manual=False):
    with rule_locks[rule_id]:
        return _run_check(rule_id,occurrence,manual)

def _run_check(rule_id, occurrence=None, manual=False):
    with Session() as db:
        rule=db.get(CommuteRule,rule_id)
        if not rule: return
        now=datetime.now(TZ)
        if occurrence is None:
            # For manual tests, evaluate the next planned departure even after its check time.
            occurrence=next_occurrence(rule.frequency,rule.selected_days.split(',') if rule.selected_days else [],rule.start_time,now=now,lead=0)[0]
        key=f'{rule.id}:{occurrence.date()}:{rule.start_time}' + (f':manual:{uuid.uuid4()}' if manual else '')
        check=JourneyCheck(rule_id=rule.id,idempotency_key=key); db.add(check)
        try: db.commit()
        except IntegrityError: db.rollback(); return
        try:
            selected = {p.kind:places.details(p.place_id) for p in db.scalars(select(RulePlace).where(RulePlace.rule_id==rule.id))}
            selected.update({p.kind:{'latitude':p.latitude,'longitude':p.longitude} for p in db.scalars(select(RuleCoordinates).where(RuleCoordinates.rule_id==rule.id))})
            if sl.provider() == 'sl':
                j,result=sl.check(rule.from_location,rule.to_location,occurrence,selected.get('from'),selected.get('to'))
            elif sl.provider() == 'gtfs':
                j=transit.plan(rule.from_location,rule.to_location,occurrence,selected.get('from'),selected.get('to')); result=transit.realtime(j)
            else: raise ValueError('TRANSIT_PROVIDER must be sl or gtfs.')
            db.add(TransitJourney(check_id=check.id,**j)); db.add(DelayResult(check_id=check.id,**result))
            delayed=result['delay_minutes'] > rule.alert_before_minutes
            check.decision='delayed' if delayed else 'on_time'; check.status='delayed' if delayed else 'on_time'
            db.add(Notification(check_id=check.id,message=DELAY_MESSAGE if delayed else ONTIME_MESSAGE,status='in_app' if rule.notification_enabled else 'disabled'))
            if delayed:
                try:
                    settings=db.get(User,1)
                    ride_j = dict(j)
                    for kind, prefix in [('from','origin'),('to','destination')]:
                        if kind in selected:
                            ride_j[prefix+'_lat'] = selected[kind]['latitude']; ride_j[prefix+'_lon'] = selected[kind]['longitude']
                    ride=uber.book(db,ride_j,rule.from_location,rule.to_location,occurrence.isoformat(),settings.preferred_product if settings else None); ride.check_id=check.id; db.add(ride); check.status='uber_booked'
                except Exception as e:
                    log.exception('Uber booking failed rule=%s check=%s',rule.id,check.id); check.status='error'; check.error='Demo booking failed. Check your Uber connection and sandbox product availability.'
            db.commit(); log.info('rule=%s check=%s trip=%s delay=%s decision=%s status=%s',rule.id,check.id,j['trip_id'],result['delay_minutes'],check.decision,check.status)
        except sl.NoJourney as e:
            plan=alternatives.suggest(rule.from_location,rule.to_location,occurrence,str(e))
            db.add(AlternativePlan(check_id=check.id,payload=json.dumps(plan)))
            check.status='alternatives'; check.decision='no_sl_journey'
            db.add(Notification(check_id=check.id,message='SL found no suitable journey. Review alternative train and coach options in your dashboard.',status='in_app' if rule.notification_enabled else 'disabled'))
            db.commit()
        except Exception as e:
            log.exception('Journey check failed rule=%s check=%s',rule.id,check.id); check.status='error'; check.error=str(e) if isinstance(e,ValueError) else 'Location or transit service is unavailable. Check configuration and try again.'; db.commit()
        return check.id

def scheduled(rule_id, occurrence_iso):
    run_check(rule_id,datetime.fromisoformat(occurrence_iso))
    with Session() as db:
        r=db.get(CommuteRule,rule_id)
        if r: schedule_rule(db,r)
def schedule_rule(db,r):
    occurrence, check=next_occurrence(r.frequency,r.selected_days.split(',') if r.selected_days else [],r.start_time)
    r.next_run=check.isoformat(); db.commit()
    scheduler.add_job(scheduled,'date',run_date=check,args=[r.id,occurrence.isoformat()],id=f'rule-{r.id}',replace_existing=True,misfire_grace_time=300)
@asynccontextmanager
async def lifespan(app):
    scheduler.start()
    with Session() as db:
        for r in db.scalars(select(CommuteRule)).all(): schedule_rule(db,r)
    yield
    scheduler.shutdown(wait=False)
app=FastAPI(title='Sweden Transit Delay & Uber Fallback',lifespan=lifespan)
app.add_middleware(CORSMiddleware,allow_origins=os.getenv('CORS_ORIGINS','http://localhost:5173').split(','),allow_methods=['*'],allow_headers=['*'])
@app.middleware('http')
async def middleware(request: Request, call_next):
    request_id=uuid.uuid4().hex; now=datetime.now().timestamp(); q=requests_by_ip[request.client.host if request.client else 'unknown']
    while q and q[0] < now-60: q.popleft()
    if len(q)>=120: return JSONResponse({'detail':'Too many requests; try again shortly.'},status_code=429)
    q.append(now)
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        origin = request.headers.get('origin')
        allowed = set(os.getenv('CORS_ORIGINS','http://localhost:5173').split(',')) | {str(request.base_url).rstrip('/')}
        if origin and origin not in allowed:
            return JSONResponse({'detail':'Request origin is not allowed.'}, status_code=403)
    public = {'/api/health', '/api/auth/login', '/api/auth/session'}
    if request.url.path not in public and request.method != 'OPTIONS':
        if not auth.configured():
            return JSONResponse({'detail':'Set APP_USERNAME and APP_PASSWORD (at least 12 characters) on the server.'}, status_code=503)
        if not auth.authenticated(request.cookies.get(auth.COOKIE)):
            return JSONResponse({'detail':'Sign in to your workspace.'}, status_code=401)
    response=await call_next(request); response.headers['X-Request-ID']=request_id
    response.headers['Cache-Control']='no-store' 
    log.info('request_id=%s method=%s path=%s status=%s',request_id,request.method,request.url.path,response.status_code); return response
class LoginInput(BaseModel):
    username: str = Field(max_length=200)
    password: str = Field(max_length=1024)

@app.get('/api/auth/session')
def session_status(request: Request):
    return {'authenticated':auth.authenticated(request.cookies.get(auth.COOKIE)), 'configured':auth.configured()}

@app.post('/api/auth/login')
def login(data: LoginInput):
    if not auth.configured():
        raise HTTPException(503, 'Set APP_USERNAME and APP_PASSWORD (at least 12 characters) on the server.')
    if not auth.valid_credentials(data.username, data.password):
        raise HTTPException(401, 'Incorrect username or password.')
    response = JSONResponse({'authenticated':True})
    response.set_cookie(auth.COOKIE, auth.create_session(), max_age=auth.MAX_AGE, httponly=True,
                        secure=os.getenv('COOKIE_SECURE','false').lower() == 'true', samesite='lax', path='/')
    return response

@app.post('/api/auth/logout')
def logout(request: Request):
    auth.revoke_session(request.cookies.get(auth.COOKIE))
    response = JSONResponse({'authenticated':False})
    response.delete_cookie(auth.COOKIE, path='/')
    return response

@app.get('/api/agent')
def agent_status():
    with Session() as db:
        checks = db.scalars(select(JourneyCheck)).all()
        rules = db.scalars(select(CommuteRule)).all()
        required = ['GTFS_STATIC_URL','TRAFIKLAB_OPERATOR','TRAFIKLAB_API_KEY'] if sl.provider() == 'gtfs' else []
        return {
            'running':scheduler.running,
            'transit_provider':sl.provider(),
            'missing_configuration':[key for key in required if not os.getenv(key)],
            'rules':len(rules), 'checks':len(checks),
            'delays':sum(c.decision == 'delayed' for c in checks),
            'rides':sum(c.status == 'uber_booked' for c in checks),
            'errors':sum(c.status == 'error' for c in checks),
            'next_check':min((r.next_run for r in rules), default=None),
        }

@app.get('/api/health')
def health(): return {'status':'ok','uber_environment':'sandbox','check_lead_minutes':int(os.getenv('CHECK_LEAD_MINUTES','10')),'google_places_enabled':places.provider() == 'google','location_provider':places.provider(),'transit_provider':sl.provider(),'gemini_configured':alternatives.configured()}
@app.get('/api/rules')
def rules():
    with Session() as db: return [{**rule_data(r),'latest':(history(db,r.id) or [None])[0]} for r in db.scalars(select(CommuteRule)).all()]
@app.post('/api/rules',status_code=201)
def create_rule(data:RuleInput):
    with Session() as db:
        values=data.model_dump(); values['selected_days']=','.join(values['selected_days']); r=CommuteRule(**{k:v for k,v in values.items() if not k.endswith(('_place_id', '_coordinates'))},next_run=''); db.add(r); db.commit(); save_places(db,r.id,values); schedule_rule(db,r); return rule_data(r)
@app.get('/api/rules/{rule_id}')
def get_rule(rule_id:int):
    with Session() as db:
        r=db.get(CommuteRule,rule_id)
        if not r: raise HTTPException(404,'Rule not found')
        return rule_data(r)
@app.patch('/api/rules/{rule_id}')
def update_rule(rule_id:int,data:RuleInput):
    with Session() as db:
        r=db.get(CommuteRule,rule_id)
        if not r: raise HTTPException(404,'Rule not found')
        for k,v in data.model_dump().items():
            if not k.endswith(('_place_id', '_coordinates')): setattr(r,k,','.join(v) if k=='selected_days' else v)
        save_places(db,r.id,data.model_dump())
        schedule_rule(db,r); return rule_data(r)
@app.delete('/api/rules/{rule_id}')
def delete_rule(rule_id:int):
    with rule_locks[rule_id]:
        return _delete_rule(rule_id)

def _delete_rule(rule_id):
    with Session() as db:
        r=db.get(CommuteRule,rule_id)
        if not r: raise HTTPException(404,'Rule not found')
        for c in db.scalars(select(JourneyCheck).where(JourneyCheck.rule_id==rule_id)).all():
            for model in [AlternativePlan,UberRide,Notification,DelayResult,TransitJourney]: db.execute(delete(model).where(model.check_id==c.id))
            db.delete(c)
        db.execute(delete(RulePlace).where(RulePlace.rule_id==rule_id))
        db.execute(delete(RuleCoordinates).where(RuleCoordinates.rule_id==rule_id))
        db.delete(r); db.commit()
        if scheduler.get_job(f'rule-{rule_id}'): scheduler.remove_job(f'rule-{rule_id}')
        return {'deleted':True}
@app.post('/api/rules/{rule_id}/run-now')
def run_now(rule_id:int):
    with Session() as db:
        if not db.get(CommuteRule,rule_id): raise HTTPException(404,'Rule not found')
    run_check(rule_id,manual=True)
    with Session() as db:
        results=history(db,rule_id)
        if not results: raise HTTPException(404,'Rule or check was removed.')
        return results[0]
@app.get('/api/rules/{rule_id}/history')
def rule_history(rule_id:int):
    with Session() as db:
        if not db.get(CommuteRule,rule_id): raise HTTPException(404,'Rule not found')
        return history(db,rule_id)
@app.get('/api/settings')
def get_settings():
    with Session() as db:
        u=db.get(User,1)
        return {'default_threshold':u.default_threshold if u else 10,'preferred_product':u.preferred_product if u else None,'email':u.email if u else None,'uber_connected':bool(db.scalar(select(UberAccount))),'uber_environment':'sandbox','check_lead_minutes':int(os.getenv('CHECK_LEAD_MINUTES','10')),'google_places_enabled':places.provider() == 'google','location_provider':places.provider(),'transit_provider':sl.provider(),'gemini_configured':alternatives.configured()}
@app.put('/api/settings')
def set_settings(data:Settings):
    with Session() as db:
        u=db.get(User,1) or User(id=1); db.add(u)
        for k,v in data.model_dump().items(): setattr(u,k,v)
        db.commit(); return {'saved':True}
@app.get('/api/locations')
def locations(q:str='', session_token:str=''):
    if len(q.strip())<2: return []
    if len(q)>200 or len(session_token)>100: raise HTTPException(400,'Search text is too long.')
    try:
        return places.autocomplete(q,session_token)
    except ValueError as e: raise HTTPException(400,str(e))
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 429: raise HTTPException(503,'Address search is busy. Please wait a moment and try again.')
        raise HTTPException(502,'Address search provider rejected the request. Check the provider configuration.')
    except Exception: raise HTTPException(502,'Address search is unavailable. Check your internet connection and try again.')

@app.get('/api/locations/details')
def location_details(place_id:str, session_token:str=''):
    if not places.enabled(): raise HTTPException(503,'Google Places is not configured.')
    if len(place_id)>500 or len(session_token)>100: raise HTTPException(400,'Invalid location selection.')
    try: return places.details(place_id,session_token)
    except Exception: raise HTTPException(502,'Could not resolve this place. Please select it again.')

@app.post('/api/uber/connect')
def connect():
    client=os.getenv('UBER_CLIENT_ID'); redirect=os.getenv('UBER_REDIRECT_URI'); key=os.getenv('TOKEN_ENCRYPTION_KEY')
    if not client or not redirect or not key: raise HTTPException(400,'Configure Uber credentials, redirect URI, and TOKEN_ENCRYPTION_KEY first.')
    state=secrets.token_urlsafe(32); oauth_states[state]=datetime.now().timestamp()
    from urllib.parse import urlencode
    return {'url':'https://login.uber.com/oauth/v2/authorize?'+urlencode({'client_id':client,'response_type':'code','redirect_uri':redirect,'scope':'request','state':state})}
@app.get('/api/uber/callback')
def callback(code:str,state:str):
    created=oauth_states.pop(state,None)
    if not created or datetime.now().timestamp()-created>600: raise HTTPException(400,'OAuth session expired. Start connecting again.')
    try:
        r=httpx.post('https://login.uber.com/oauth/v2/token',data={'client_id':os.getenv('UBER_CLIENT_ID'),'client_secret':os.getenv('UBER_CLIENT_SECRET'),'grant_type':'authorization_code','redirect_uri':os.getenv('UBER_REDIRECT_URI'),'code':code},timeout=20); r.raise_for_status(); refresh=r.json()['refresh_token']; encrypted=Fernet(os.environ['TOKEN_ENCRYPTION_KEY'].encode()).encrypt(refresh.encode()).decode()
        with Session() as db:
            account=db.scalar(select(UberAccount)) or UberAccount(encrypted_refresh_token=encrypted); account.encrypted_refresh_token=encrypted; db.add(account); db.commit()
    except Exception: raise HTTPException(400,'Uber connection failed. Check your sandbox credentials and try again.')
    return RedirectResponse(os.getenv('FRONTEND_URL','http://localhost:5173')+'/?page=settings')
@app.post('/api/uber/demo-ride')
def demo_ride(): raise HTTPException(400,'Run a commute check to create a demo ride with a verified journey.')
@app.delete('/api/uber/rides/{request_id}')
def cancel(request_id:str):
    with Session() as db:
        ride=db.scalar(select(UberRide).where(UberRide.request_id==request_id))
        if not ride: raise HTTPException(404,'Demo ride not found')
        if os.getenv('UBER_ENV','sandbox')!='sandbox': raise HTTPException(400,'Only sandbox cancellation is supported.')
        try:
            r=httpx.delete(uber.base+'/requests/'+request_id,headers={'Authorization':'Bearer '+uber.token(db)},timeout=20); r.raise_for_status(); ride.status='cancelled'; db.commit(); return serialize(ride)
        except Exception: raise HTTPException(502,'Could not cancel the demo ride. Check your Uber connection and try again.')
