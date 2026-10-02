const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
(async () => {
  const src = fs.readFileSync(path.join(__dirname, "../python/pigeonvision/ground/static/telemetry.js"), "utf8");
  const { TelemetryState } = await import(`data:text/javascript;base64,${Buffer.from(src).toString("base64")}`);
  const t = new TelemetryState();
  assert(t.lines(0).includes("Sensors: no samples received"));
  t.accept({type:"health",phase:"COAST",phase_stale:false,lifecycle:"running"},100);
  t.accept({type:"sensors",sources:[{source:"ina226",columns:["monotonic_us","valid","voltage_v"],rows:[[12,true,0],[13,true,7.2]]}]},100);
  assert(t.lines(200).some(x=>x.includes("7.2")));
  assert(t.lines(200).includes("Phase: COAST"));
  assert(t.lines(4000).some(x=>x.includes("ina226: stale · last voltage_v: 7.2")));
  assert(t.lines(4000).includes("Phase: UNKNOWN · stale"));
  t.accept({type:"sensors",sources:[{source:"ina226",columns:["valid","voltage_v"],rows:[[false,null]]}]},4100);
  assert(t.lines(4200).includes("ina226: invalid"));
  const ordered=new TelemetryState();
  const group=(valid,rows,extra={})=>({source:"bmi088_accel",valid,base_monotonic_us:1000,rows,shared_values:{saturated:false},...extra});
  ordered.accept({type:"sensors",session_id:"one",backend:"simulation",timestamp_basis:"host_read",acquisition_clock_domain:"CLOCK_BOOTTIME",sources:[
    group(true,[[1,10,8,10,[0,0,9.8]],[3,30,28,30,[0,0,9.9]]],{missed_polls:7}),
    group(false,[[2,20,18,10]],{status:"not_ready"})]},100);
  assert.equal(ordered.sources.bmi088_accel.sample.sequence,3);
  assert.equal(ordered.sources.bmi088_accel.sample.monotonic_us,1030);
  assert.equal(ordered.sources.bmi088_accel.sample.backend,"simulation");
  assert.equal(ordered.sources.bmi088_accel.sample.timestamp_basis,"host_read");
  assert.equal(ordered.sources.bmi088_accel.sample.missed_polls,7);
  assert.equal(ordered.sources.bmi088_accel.sample.values.saturated,false);
  assert(ordered.lines(101).some(x=>x.includes("bmi088_accel [simulation]:")));
  ordered.accept({type:"sensors",session_id:"one",backend:"i2c",sources:[group(false,[[2,20,18,10]])]},200);
  assert.equal(ordered.sources.bmi088_accel.receivedAt,100); // Old UDP packets cannot refresh freshness.
  ordered.accept({type:"sensors",session_id:"one",backend:"i2c",sources:[group(true,[[4,40,38,40,[1,2,3]]])]},300);
  assert.equal(ordered.sources.bmi088_accel.sample.backend,"i2c");
  ordered.accept({type:"session",session_id:"two"},400);
  assert.deepEqual(ordered.sources,{});
  console.log("ground telemetry freshness passed");
})();
