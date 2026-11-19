import csv, io, json, logging, math, os, time, uuid, zipfile
from datetime import datetime, timedelta, timezone
import httpx
from google.transit import gtfs_realtime_pb2
from .core import TZ, delay_minutes, is_delayed
from .models import *
log = logging.getLogger('commute')
DELAY_MESSAGE = 'Transit is delayed beyond your threshold. A local demo ride was simulated and saved to your history; no real ride was requested.'
ONTIME_MESSAGE = 'Everything as planned, have a great day'

def fetch(url, **kwargs):
    for attempt in range(3):
        try:
            response = httpx.get(url, timeout=20, **kwargs); response.raise_for_status(); return response
        except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as e:
            if isinstance(e, httpx.HTTPStatusError) and e.response.status_code < 500 and e.response.status_code != 429: raise
            if attempt == 2: raise
            time.sleep(.25 * 2**attempt)

class Transit:
    def __init__(self): self.tables={}; self.loaded=0; self.cache={}
    def load(self):
        ttl = int(os.getenv('GTFS_REFRESH_HOURS','24'))*3600
        if self.tables and time.time()-self.loaded < ttl: return
        url=os.getenv('GTFS_STATIC_URL','')
        if not url: raise ValueError('Configure GTFS_STATIC_URL with a Trafiklab GTFS Sweden 3 static feed URL.')
        raw=fetch(url).content if url.startswith(('http://','https://')) else open(url,'rb').read()
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            tables={}
            for name in ['stops','routes','trips','stop_times','calendar','calendar_dates']:
                try: tables[name]=list(csv.DictReader(io.TextIOWrapper(z.open(name+'.txt'),encoding='utf-8-sig')))
                except KeyError: tables[name]=[]
        self.tables=tables; self.loaded=time.time()
    def resolve(self, query):
        # Stop-name matching works without a third-party geocoder; coordinates are the stop coordinates.
        matches=[s for s in self.tables['stops'] if query.casefold() in s.get('stop_name','').casefold() and s.get('stop_lat') and s.get('stop_lon')]
        if not matches: raise ValueError(f'No GTFS stop matches “{query}”. Try a nearby stop name.')
        return matches[:20]
    def nearby(self, place):
        def distance(stop):
            lat1, lat2 = math.radians(place['latitude']), math.radians(float(stop['stop_lat']))
            dlat = lat2 - lat1
            dlon = math.radians(float(stop['stop_lon']) - place['longitude'])
            a = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
            return 6371000 * 2 * math.asin(min(1, math.sqrt(a)))
        stops = [s for s in self.tables['stops'] if s.get('stop_lat') and s.get('stop_lon')]
        nearby = sorted(((distance(s), s) for s in stops), key=lambda item:item[0])
        matches = [s for meters, s in nearby if meters <= 1000][:20]
        if not matches: raise ValueError('No transit stops within 1 km of the selected address in the configured feed.')
        return matches
    def plan(self, origin, destination, departure, origin_place=None, destination_place=None):
        self.load(); origins=self.nearby(origin_place) if origin_place else self.resolve(origin); destinations=self.nearby(destination_place) if destination_place else self.resolve(destination)
        origin_ids={s['stop_id']:s for s in origins}; dest_ids={s['stop_id']:s for s in destinations}
        day=departure.strftime('%Y%m%d'); weekday=departure.strftime('%A').lower()
        active={c['service_id'] for c in self.tables['calendar'] if c.get('start_date','')<=day<=c.get('end_date','') and c.get(weekday)=='1'}
        for c in self.tables['calendar_dates']:
            if c.get('date')==day:
                if c.get('exception_type')=='1': active.add(c['service_id'])
                elif c.get('exception_type')=='2': active.discard(c['service_id'])
        trips={t['trip_id']:t for t in self.tables['trips'] if t.get('service_id') in active}
        routes={r['route_id']:r for r in self.tables['routes']}
        by_trip={}
        for st in self.tables['stop_times']:
            if st.get('trip_id') in trips and st.get('stop_id') in origin_ids.keys() | dest_ids.keys(): by_trip.setdefault(st['trip_id'],[]).append(st)
        def dt(value):
            h,m,s=map(int,value.split(':')); return datetime.combine(departure.date(),datetime.min.time(),TZ)+timedelta(hours=h,minutes=m,seconds=s)
        candidates=[]
        for tid, stops in by_trip.items():
            starts=[s for s in stops if s['stop_id'] in origin_ids]; ends=[s for s in stops if s['stop_id'] in dest_ids]
            for a in starts:
                for b in ends:
                    if int(a['stop_sequence']) >= int(b['stop_sequence']): continue
                    dep=dt(a['departure_time']); arr=dt(b['arrival_time'])
                    if departure-timedelta(minutes=5) <= dep <= departure+timedelta(minutes=90): candidates.append((dep,arr,tid,a,b))
        if not candidates: raise ValueError('No direct journey found near the start time. Try nearby stop names or another time.')
        dep,arr,tid,a,b=min(candidates,key=lambda x:(x[0],x[1])); trip=trips[tid]; route=routes.get(trip['route_id'],{}); o=origin_ids[a['stop_id']]; d=dest_ids[b['stop_id']]
        operator=os.getenv('TRAFIKLAB_OPERATOR','')
        if not operator: raise ValueError('Configure TRAFIKLAB_OPERATOR for the selected regional feed.')
        return dict(trip_id=tid,route_id=trip['route_id'],route_name=route.get('route_short_name') or route.get('route_long_name') or trip['route_id'],operator=operator,origin_stop_id=a['stop_id'],destination_stop_id=b['stop_id'],scheduled_departure=dep.isoformat(),scheduled_arrival=arr.isoformat(),origin_lat=float(o['stop_lat']),origin_lon=float(o['stop_lon']),destination_lat=float(d['stop_lat']),destination_lon=float(d['stop_lon']))
    def feed(self, operator, kind):
        key=os.getenv('TRAFIKLAB_API_KEY','')
        if not key: raise ValueError('Configure TRAFIKLAB_API_KEY to check real-time transit data.')
        cache_key=(operator,kind); now=time.time()
        if cache_key in self.cache and now-self.cache[cache_key][0] < 30: return self.cache[cache_key][1]
        url=f'https://opendata.samtrafiken.se/gtfs-rt-sweden/{operator}/{kind}Sweden.pb'
        response=fetch(url,params={'key':key}); feed=gtfs_realtime_pb2.FeedMessage(); feed.ParseFromString(response.content)
        if not feed.header.timestamp or now-feed.header.timestamp > int(os.getenv('REALTIME_MAX_AGE_SECONDS','300')): raise ValueError('Transit real-time feed is stale. Try again later.')
        self.cache[cache_key]=(now,feed); return feed
    def realtime(self,j):
        feed=self.feed(j['operator'],'TripUpdates'); scheduled=datetime.fromisoformat(j['scheduled_departure']); arrival=datetime.fromisoformat(j['scheduled_arrival']); dep=None; arr=None; warning=None
        for entity in feed.entity:
            if not entity.HasField('trip_update') or entity.trip_update.trip.trip_id != j['trip_id']: continue
            update=entity.trip_update
            if update.trip.schedule_relationship == gtfs_realtime_pb2.TripDescriptor.CANCELED: raise ValueError('The selected transit trip is cancelled. Review your journey manually.')
            for st in update.stop_time_update:
                if st.stop_id == j['origin_stop_id'] and st.HasField('departure'):
                    if st.departure.HasField('time'): dep=datetime.fromtimestamp(st.departure.time,TZ)
                    elif st.departure.HasField('delay'): dep=scheduled+timedelta(seconds=st.departure.delay)
                if st.stop_id == j['destination_stop_id'] and st.HasField('arrival'):
                    if st.arrival.HasField('time'): arr=datetime.fromtimestamp(st.arrival.time,TZ)
                    elif st.arrival.HasField('delay'): arr=arrival+timedelta(seconds=st.arrival.delay)
            if dep is None and update.HasField('delay'): dep=scheduled+timedelta(seconds=update.delay)
            break
        if dep is None: warning='No real-time departure update; assumed on schedule.'; log.warning(warning); dep=scheduled
        try:
            alerts=self.feed(j['operator'],'ServiceAlerts')
            if any(e.HasField('alert') and any((s.HasField('trip') and s.trip.trip_id==j['trip_id']) or (s.HasField('route_id') and s.route_id==j['route_id']) or (s.HasField('stop_id') and s.stop_id in (j['origin_stop_id'],j['destination_stop_id'])) for s in e.alert.informed_entity) for e in alerts.entity): warning=(warning+' ' if warning else '')+'A service alert affects this journey; check the operator for details.'
        except Exception: log.exception('Service alerts unavailable'); warning=(warning+' ' if warning else '')+'Service alerts unavailable.'
        return dict(delay_minutes=delay_minutes(scheduled,dep),estimated_departure=dep.isoformat(),estimated_arrival=(arr or arrival).isoformat(),warning=warning)
transit=Transit()

class Uber:
    def book(self,db,j,pickup,destination,pickup_time,product_id=None):
        # Local demo only: mirrors the reference implementation without calling Uber.
        request_id='SIM-UBER-'+uuid.uuid4().hex[:12].upper()
        demo_driver={'driver':{'name':'Demo driver · simulated'},'vehicle':{'make':'Demo vehicle','model':'simulated'}}
        return UberRide(request_id=request_id,status='simulated',product_name='UberX · demo ride',fare=None,pickup=pickup,destination=destination,pickup_time=pickup_time,eta='5',driver=json.dumps(demo_driver))
uber=Uber()

