"""Optional gr-dvbs2rx monitor adapter; receipt age is not RF/video age."""
import asyncio
import json
import math
import time
from urllib.parse import urlparse

from aiohttp import ClientError, ClientSession, ClientTimeout

MAX_RESPONSE_BYTES = 16 * 1024
POLL_TIMEOUT_S = .75
POLL_INTERVAL_S = 1
STALE_AFTER_S = 3.0


class RadioMonitor:
    def __init__(self, url, *, clock=time.monotonic, stale_after=STALE_AFTER_S):
        parsed = urlparse(url)
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname:
            raise ValueError('Radio monitor URL must be an HTTP(S) URL')
        try:
            parsed.port
        except ValueError as exc:
            raise ValueError('Invalid radio monitor URL port') from exc
        self.url, self.clock, self.stale_after = url, clock, stale_after
        self.sample = None
        self.received_at = None
        self.error = None

    def accept(self, value):
        if not isinstance(value, dict) or type(value.get('lock')) is not bool:
            raise ValueError('Radio monitor requires a boolean lock')
        fec = value.get('fec')
        if not isinstance(fec, dict):
            raise ValueError('Radio monitor requires FEC counters')
        frames, errors = fec.get('frames'), fec.get('errors')
        if any(type(v) is not int or v < 0 for v in (frames, errors)) or errors > frames:
            raise ValueError('Invalid radio FEC counters')
        snr, fer = value.get('snr'), fec.get('fer')
        for name, number in [('snr', snr), ('fer', fer)]:
            if number is None:
                continue
            try:
                finite = type(number) in (int, float) and math.isfinite(number)
            except OverflowError:
                finite = False
            if not finite:
                raise ValueError(f'Invalid radio {name}')
        expected = errors / frames if frames else None
        if fer != expected:
            raise ValueError('Radio FER disagrees with cumulative FEC counters')
        self.sample = dict(lock=value['lock'], snr_db=snr, fec_frames=frames,
                           fec_errors=errors, fec_fer=fer)
        self.received_at, self.error = self.clock(), None

    def status(self):
        age = None if self.received_at is None else max(0, self.clock() - self.received_at)
        if age is None:
            state = 'waiting' if self.error is None else 'unavailable'
        elif self.error is not None or age > self.stale_after:
            state = 'stale'
        else:
            state = 'current'  # Monitor reply freshness, not RF sample progress.
        result = dict(state=state, sample_age_s=age, lock=None, snr_db=None,
                      fec_frames=None, fec_errors=None, fec_fer=None, error=self.error)
        if self.sample:
            result.update(self.sample)
            if state != 'current':
                result.update(lock=None, snr_db=None, fec_fer=None)
            elif not result['lock']:
                result.update(snr_db=None, fec_fer=None)
        return result

    async def poll_once(self, session):
        try:
            async with session.get(self.url, allow_redirects=False) as response:
                response.raise_for_status()
                data = bytearray()
                async for chunk in response.content.iter_chunked(4096):
                    data.extend(chunk)
                    if len(data) > MAX_RESPONSE_BYTES:
                        raise ValueError('Radio monitor response exceeds 16 KiB')
                self.accept(json.loads(data))
        except (ClientError, asyncio.TimeoutError, ValueError, UnicodeError, RecursionError) as exc:
            self.error = f'{type(exc).__name__}: {exc}'

    async def run(self, publish):
        async with ClientSession(timeout=ClientTimeout(total=POLL_TIMEOUT_S)) as session:
            while True:
                await self.poll_once(session)
                publish()
                await asyncio.sleep(POLL_INTERVAL_S)
