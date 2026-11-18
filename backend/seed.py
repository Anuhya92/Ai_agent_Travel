from app.core import Base, engine, Session, next_occurrence
from app.models import CommuteRule
Base.metadata.create_all(engine)
with Session() as db:
    for origin,destination,frequency,start in [('Odenplan','Stockholm City','weekdays','08:30'),('Stockholm City','Odenplan','weekends','10:00')]:
        _,check=next_occurrence(frequency,[],start)
        db.add(CommuteRule(from_location=origin,to_location=destination,frequency=frequency,selected_days='',start_time=start,alert_before_minutes=10,notification_enabled=True,next_run=check.isoformat()))
    db.commit()
print('Added two example rules. Restart the backend to schedule them.')
