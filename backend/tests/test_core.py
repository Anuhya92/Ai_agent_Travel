from datetime import datetime, timedelta
from app.core import TZ, next_occurrence, delay_minutes, is_delayed
from app.services import DELAY_MESSAGE, ONTIME_MESSAGE

def test_delay_and_threshold():
    a=datetime(2026,1,1,8,tzinfo=TZ)
    assert delay_minutes(a,a+timedelta(minutes=11))==11
    assert is_delayed(11,10) and not is_delayed(10,10)
def test_next_weekday_and_custom():
    now=datetime(2026,11,20,12,tzinfo=TZ) # Friday
    assert next_occurrence('weekdays',[],'08:30',now)[0].weekday()==0
    assert next_occurrence('custom',['WED'],'08:30',now)[0].weekday()==2
    assert next_occurrence('weekends',[],'08:30',now)[0].weekday()==5
def test_dst():
    now=datetime(2026,3,28,12,tzinfo=TZ)
    occurrence,check=next_occurrence('weekends',[],'08:30',now)
    assert occurrence.hour==8 and check.hour==8 and check.minute==20
    assert occurrence.utcoffset()==timedelta(hours=2)
def test_messages():
    assert DELAY_MESSAGE=='Tough start for the day but lets make it better through a UBER ride'
    assert ONTIME_MESSAGE=='Everything as planned, have a great day'
