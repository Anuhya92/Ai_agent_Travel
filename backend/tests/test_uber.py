from app.services import Uber
class Response:
    def __init__(self,data): self.data=data
    def raise_for_status(self): pass
    def json(self): return self.data

def test_sandbox_fare_is_obtained_immediately_before_request(monkeypatch):
    calls=[]
    monkeypatch.setenv('UBER_ENV','sandbox')
    monkeypatch.setattr(Uber,'token',lambda self,db:'short-lived')
    def get(url,**kwargs):
        calls.append(url); return Response({'products':[{'product_id':'p','display_name':'Demo'}]})
    def post(url,**kwargs):
        calls.append(url)
        if url.endswith('/estimate'): return Response({'fare':{'fare_id':'fresh','display':'100 SEK'}})
        assert kwargs['json']['fare_id']=='fresh'
        return Response({'request_id':'ride-1','status':'processing'})
    monkeypatch.setattr('app.services.httpx.get',get); monkeypatch.setattr('app.services.httpx.post',post)
    ride=Uber().book(None,{'origin_lat':1,'origin_lon':2,'destination_lat':3,'destination_lon':4},'A','B','08:30')
    assert ride.request_id=='ride-1'
    assert all('sandbox-api.uber.com' in url for url in calls)
    assert calls[-2].endswith('/requests/estimate') and calls[-1].endswith('/requests')
