// Headless viewer regression with local assets and a mocked WebSocket.
// Set NODE_PATH to an existing Playwright installation. No camera needed.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const assets = path.resolve(__dirname, '../python/pigeonvision/ground/static');
(async () => {
  const browser = await chromium.launch({channel:'chrome',headless:true,args:['--enable-gpu']});
  try {
    const page = await browser.newPage({viewport:{width:1440,height:1000}});
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.route('http://127.0.0.1:9876/**', route => {
      const resource = new URL(route.request().url()).pathname;
      const file = resource === '/' ? 'index.html' : resource.replace(/^\/static\//,'');
      if (file === 'favicon.ico') return route.fulfill({status:204});
      if (file.includes('..')) return route.abort();
      return route.fulfill({body:fs.readFileSync(path.join(assets,file)),contentType:file.endsWith('.js')?'application/javascript':file.endsWith('.css')?'text/css':'text/html'});
    });
    await page.addInitScript(() => {
      window.WebSocket = class {
        static OPEN = 1;
        constructor() { this.readyState=1; window.testSocket=this; queueMicrotask(()=>this.onopen?.()); }
        send() { throw Error('Mocked telemetry reset check must not send outbound controls'); }
        close() { this.readyState=3; this.onclose?.(); }
      };
    });
    await page.goto('http://127.0.0.1:9876/');
    await page.waitForFunction(()=>window.pigeonGround?.snapshot().connected);
    await page.selectOption('#view','b');
    await page.click('#rotate-view');
    const before = await page.evaluate(()=>window.pigeonGround.snapshot());
    assert.equal(before.viewerRotation.B,180);
    const emit = value => page.evaluate(value=>window.testSocket.onmessage({data:JSON.stringify(value)}),value);
    const status = (replay,generation) => ({type:'status',state:'ready',source:replay?'fixture.ts':'udp://127.0.0.1:1234',replay,playing:true,generation,reset:true});
    const sample = (session,seq,time,value) => ({type:'metadata',record:{type:'sensors',session_id:session,sources:[{source:'bmi088_accel',valid:true,base_monotonic_us:0,rows:[[seq,time,time,time,[value,0,0]]]}]}});
    const session = id => ({type:'metadata',record:{type:'session',session_id:id,cameras:[]}});
    const health = (id,pts,phase) => ({type:'metadata',record:{type:'health',session_id:id,pts_us:pts,phase,phase_stale:false}});
    const expectValue = async value => {
      await page.waitForTimeout(250); // Let the 200 ms diagnostics timer observe rejected records.
      await page.waitForFunction(value=>document.getElementById('flight-telemetry').textContent.includes(`accel_m_s2: ${value}, 0, 0`),value);
    };
    await emit(status(true,1));
    await emit(session('one'));
    await emit(sample('one',90,900000,9));
    await emit(health('one',90,'ASCENT'));
    await expectValue(9);
    // Same-generation decoder reset must preserve sensor ordering and session retirement.
    await emit(status(true,1));
    await emit(sample('one',1,10000,1));
    await expectValue(9);
    await emit(session('two'));
    await emit(sample('two',1,10000,2));
    await expectValue(2);
    await emit(status(true,1));
    await emit(session('one'));
    await emit(sample('one',90,900000,9));
    await expectValue(2);
    // The receiver's new replay generation permits rewinding the same file/session.
    await emit(status(true,2));
    await emit(session('one'));
    await emit(sample('one',1,10000,1));
    await emit(health('one',1,'PAD'));
    await expectValue(1);
    await page.waitForFunction(()=>document.getElementById('flight-telemetry').textContent.includes('Phase: PAD'));
    const after = await page.evaluate(()=>window.pigeonGround.snapshot());
    assert.deepEqual(after.viewerRotation,before.viewerRotation);
    assert.deepEqual(after.focus,before.focus);
    assert.deepEqual(after.perspective,before.perspective);
    assert.equal(await page.inputValue('#view'),'b');
    // Live reconnect advances receiver generation without allowing an old session back.
    await emit(status(false,3));
    await emit(session('live-one'));
    await emit(sample('live-one',1,10000,3));
    await emit(session('live-two'));
    await emit(sample('live-two',1,10000,4));
    await expectValue(4);
    await page.evaluate(()=>window.testSocket.close());
    await page.waitForFunction(()=>!window.pigeonGround.snapshot().connected);
    await page.waitForFunction(()=>document.getElementById('flight-telemetry').textContent.includes('Sensors: no samples received'));
    await page.waitForFunction(()=>window.pigeonGround.snapshot().connected);
    await emit(status(false,4));
    await emit(session('live-one'));
    await emit(sample('live-one',1,10000,3));
    assert.match(await page.locator('#flight-telemetry').textContent(),/Sensors: no samples received/);
    await emit(sample('live-two',2,20000,5));
    await expectValue(5);
    assert.deepEqual(errors,[]);
    const result = {checked:['same-generation decoder reset preserves ordering','retired session cannot return after decoder reset','new replay generation permits timestamp rewind','live reconnect preserves retired sessions','view selection, rotation, focus and perspective persist','no JavaScript errors']};
    console.log(JSON.stringify(result));
  } finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exitCode=1;});
