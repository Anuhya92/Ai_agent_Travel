import os
from datetime import datetime, timedelta, date, time, timezone
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

load_dotenv()
TZ = ZoneInfo('Europe/Stockholm')
DATABASE_URL = os.getenv('DATABASE_URL', 'sqlite:///./commute.db')
engine = create_engine(DATABASE_URL, connect_args={'check_same_thread': False} if DATABASE_URL.startswith('sqlite') else {})
Session = sessionmaker(bind=engine, expire_on_commit=False)
class Base(DeclarativeBase): pass
DAYS = ['MON','TUE','WED','THU','FRI','SAT','SUN']
def next_occurrence(frequency, selected_days, start_time, now=None, lead=None):
    now = (now or datetime.now(TZ)).astimezone(TZ)
    lead = int(os.getenv('CHECK_LEAD_MINUTES', '10')) if lead is None else lead
    allowed = DAYS[:5] if frequency == 'weekdays' else DAYS[5:] if frequency == 'weekends' else selected_days
    h, m = map(int, start_time.split(':'))
    for offset in range(15):
        day = now.date() + timedelta(days=offset)
        if DAYS[day.weekday()] not in allowed: continue
        # Round-trip detects nonexistent spring-forward wall times; use the first valid time after the gap.
        candidate = datetime.combine(day, time(h,m), TZ)
        candidate = candidate.astimezone(timezone.utc).astimezone(TZ)
        check = (candidate.astimezone(timezone.utc) - timedelta(minutes=lead)).astimezone(TZ)
        if check > now: return candidate, check
    raise ValueError('No upcoming occurrence')
def delay_minutes(scheduled, estimated):
    return int((estimated - scheduled).total_seconds() / 60)
def is_delayed(delay, threshold): return delay > threshold
