"""Monitor receipt freshness does not establish RF or video progress."""
import asyncio
from aiohttp import ClientSession, ClientTimeout, web
from aiohttp.test_utils import TestClient, TestServer
import pytest
from pigeonvision.ground.radio_health import RadioMonitor
from pigeonvision.ground.server import Receiver, create_app, RECEIVER
from pigeonvision.cli import parser

SAMPLE = {'lock': True, 'snr': 8.5, 'fec': {'frames': 10, 'errors': 1, 'fer': .1}}


def test_freshness_unlock_and_recovery():
    now = [0.0]
    monitor = RadioMonitor('http://127.0.0.1:9004', clock=lambda: now[0])
    assert monitor.status()['state'] == 'waiting'
    monitor.accept(SAMPLE)
    assert monitor.status()['snr_db'] == 8.5
    now[0] = 3.1
    stale = monitor.status()
    assert stale['state'] == 'stale'
    assert stale['lock'] is stale['snr_db'] is stale['fec_fer'] is None
    assert stale['fec_frames'] == 10
    monitor.accept({**SAMPLE, 'lock': False})
    assert monitor.status()['lock'] is False
    assert monitor.status()['snr_db'] is None
    monitor.accept(SAMPLE)
    assert monitor.status()['state'] == 'current'
    assert monitor.status()['sample_age_s'] == 0


@pytest.mark.parametrize('change', [
    {'lock': 1}, {'snr': float('nan')}, {'snr': 10 ** 400}, {'fec': {}},
    {'fec': {'frames': 1, 'errors': 2, 'fer': 2}},
    {'fec': {'frames': 10, 'errors': 1, 'fer': .9}},
])
def test_reject_invalid_payload(change):
    monitor = RadioMonitor('http://localhost:9004')
    with pytest.raises(ValueError):
        monitor.accept({**SAMPLE, **change})
    assert monitor.status()['state'] == 'waiting'


def test_default_status_and_cli_unchanged():
    assert 'radio_health' not in Receiver('udp://127.0.0.1:5000').status()
    assert parser().parse_args(['view', 'udp://127.0.0.1:5000']).radio_monitor_url is None
    app = create_app('udp://127.0.0.1:5000', radio_monitor_url='http://localhost:9004')
    assert app[RECEIVER].status()['radio_health']['state'] == 'waiting'


def test_http_timeout_invalid_reply_and_recovery():
    async def scenario():
        mode = ['timeout']
        async def handler(request):
            if mode[0] == 'timeout':
                await asyncio.sleep(.1)
            if mode[0] == 'invalid':
                return web.json_response({'lock': 'yes'})
            return web.json_response(SAMPLE)
        app = web.Application()
        app.router.add_get('/', handler)
        async with TestServer(app) as server:
            monitor = RadioMonitor(str(server.make_url('/')))
            async with ClientSession(timeout=ClientTimeout(total=.02)) as session:
                await monitor.poll_once(session)
                assert monitor.status()['state'] == 'unavailable'
                mode[0] = 'ok'
                await monitor.poll_once(session)
                assert monitor.status()['state'] == 'current'
                mode[0] = 'invalid'
                await monitor.poll_once(session)
                assert monitor.status()['state'] == 'stale'
                assert monitor.status()['lock'] is None
                mode[0] = 'ok'
                await monitor.poll_once(session)
                assert monitor.status()['state'] == 'current'
                assert monitor.status()['error'] is None
    asyncio.run(scenario())


def test_app_health_polls_and_cleans_up(monkeypatch):
    monkeypatch.setattr(Receiver, 'start', lambda self: None)
    monkeypatch.setattr(Receiver, 'stop', lambda self: None)
    async def scenario():
        upstream = web.Application()
        async def reply(request):
            return web.json_response(SAMPLE)
        upstream.router.add_get('/', reply)
        async with TestServer(upstream) as server:
            app = create_app('udp://127.0.0.1:5000',
                             radio_monitor_url=str(server.make_url('/')))
            async with TestClient(TestServer(app)) as client:
                for _ in range(30):
                    response = await client.get('/health')
                    status = await response.json()
                    if status['radio_health']['state'] == 'current':
                        break
                    await asyncio.sleep(.01)
                assert status['radio_health']['lock'] is True
                assert status['radio_health']['fec_errors'] == 1
    asyncio.run(scenario())


def test_initial_upstream_zero_frame_payload():
    monitor = RadioMonitor('http://localhost:9004')
    monitor.accept({'lock': False, 'snr': None,
                    'fec': {'frames': 0, 'errors': 0, 'fer': None}})
    status = monitor.status()
    assert status['state'] == 'current'
    assert status['lock'] is False
    assert status['fec_frames'] == status['fec_errors'] == 0
    assert status['snr_db'] is status['fec_fer'] is status['error'] is None


def test_cancel_during_stalled_http_closes_session(monkeypatch):
    sessions = []
    def tracked_session(**kwargs):
        session = ClientSession(**kwargs)
        sessions.append(session)
        return session
    monkeypatch.setattr('pigeonvision.ground.radio_health.ClientSession', tracked_session)

    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()
        transports = []
        async def stall(request):
            response = web.StreamResponse()
            await response.prepare(request)
            transports.append(request.transport)
            started.set()
            await release.wait()  # No body or EOF until the test has cancelled.
            return response
        app = web.Application()
        app.router.add_get('/', stall)
        async with TestServer(app) as server:
            monitor = RadioMonitor(str(server.make_url('/')))
            published = []
            task = asyncio.create_task(monitor.run(lambda: published.append(True)))
            try:
                await asyncio.wait_for(started.wait(), timeout=1)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, timeout=.25)
                assert sessions[0].closed
                assert sessions[0].connector is None
                assert not published
                assert monitor.status()['state'] == 'waiting'
                for _ in range(30):
                    if transports[0].is_closing():
                        break
                    await asyncio.sleep(.01)
                assert transports[0].is_closing()
            finally:
                release.set()
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


@pytest.mark.parametrize('url', ['file:///tmp/monitor', 'http://', 'http://localhost:bad'])
def test_invalid_monitor_url_does_not_open_recording(url, tmp_path):
    target = tmp_path / 'transport.ts'
    with pytest.raises(ValueError):
        create_app('udp://127.0.0.1:5000', record_transport=str(target),
                   radio_monitor_url=url)
    assert not target.exists()
    assert not target.with_name(target.name + '.recording.json').exists()


def test_oversized_and_redirected_replies_do_not_replace_sample():
    async def scenario():
        mode = ['oversized']
        followups = []
        async def reply(request):
            if request.path == '/redirected':
                followups.append(True)
                return web.json_response(SAMPLE)
            if mode[0] == 'redirect':
                raise web.HTTPFound('/redirected')
            return web.Response(body=b' ' * 16385)
        app = web.Application()
        app.router.add_get('/{path:.*}', reply)
        async with TestServer(app) as server:
            monitor = RadioMonitor(str(server.make_url('/')))
            monitor.accept(SAMPLE)
            async with ClientSession() as session:
                for value in ('oversized', 'redirect'):
                    mode[0] = value
                    await monitor.poll_once(session)
                    status = monitor.status()
                    assert status['state'] == 'stale'
                    assert status['lock'] is None
                    assert status['fec_frames'] == SAMPLE['fec']['frames']
            assert not followups
    asyncio.run(scenario())


def test_deeply_nested_json_does_not_stop_monitor():
    async def scenario():
        async def reply(request):
            return web.Response(body=b'[' * 8000 + b']' * 8000)
        app = web.Application()
        app.router.add_get('/', reply)
        async with TestServer(app) as server:
            monitor = RadioMonitor(str(server.make_url('/')))
            async with ClientSession() as session:
                await monitor.poll_once(session)
            assert monitor.status()['state'] == 'unavailable'
            assert monitor.status()['error'].startswith(('RecursionError:', 'ValueError:'))
            monitor.accept(SAMPLE)
            assert monitor.status()['state'] == 'current'
    asyncio.run(scenario())
