from app.services import Uber


def test_demo_ride_is_simulated_locally_without_uber_api(monkeypatch):
    def unexpected_request(*args, **kwargs):
        raise AssertionError('Local demo rides must not call the Uber API.')

    monkeypatch.setattr('app.services.httpx.get', unexpected_request)
    monkeypatch.setattr('app.services.httpx.post', unexpected_request)

    ride = Uber().book(
        None,
        {'origin_lat': 1, 'origin_lon': 2, 'destination_lat': 3, 'destination_lon': 4},
        'A',
        'B',
        '2026-11-19T08:30:00+01:00',
    )

    assert ride.request_id.startswith('SIM-UBER-')
    assert ride.status == 'simulated'
    assert ride.product_name == 'UberX · demo ride'
    assert ride.pickup == 'A'
    assert ride.destination == 'B'
    assert ride.pickup_time == '2026-11-19T08:30:00+01:00'
