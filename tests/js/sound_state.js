// Sound's desired state vs. its output, against a fake Web Audio graph. Run: node tests/js/sound_state.js
"use strict";
const assert = require("assert");
const path = require("path");

// Fake timers: run by hand, so "after 1.8 s" is deterministic.
let timers = [], now = 0, nextId = 1;
global.setTimeout = (fn, ms = 0) => { const id = nextId++; timers.push({ id, at: now + ms, fn }); return id; };
global.clearTimeout = (id) => { timers = timers.filter((t) => t.id !== id); };
function advance(ms) {
  now += ms;
  for (;;) {
    timers.sort((a, b) => a.at - b.at);
    const t = timers[0];
    if (!t || t.at > now) break;
    timers.shift(); t.fn();
  }
}

// A param remembers the value it is heading to: that is all the mix needs.
class Param {
  constructor(v) { this.value = v; this.target = v; }
  setValueAtTime(v) { this.target = v; }
  setTargetAtTime(v) { this.target = v; }
  exponentialRampToValueAtTime(v) { this.target = v; }
  linearRampToValueAtTime(v) { this.target = v; }
  cancelScheduledValues() {}
}
class Node {
  constructor() { this.gain = new Param(1); this.frequency = new Param(440); this.detune = new Param(0); this.Q = new Param(1);
    for (const k of ["threshold", "knee", "ratio", "attack", "release"]) this[k] = new Param(0); }
  connect(n) { return n; }
  disconnect() {} start() {} stop() { this.stopped = true; } addEventListener() {}
}
class FakeContext {
  constructor() { this.state = "running"; this.currentTime = 0; this.sampleRate = 8000; this.destination = new Node(); }
  createGain() { return new Node(); } createDynamicsCompressor() { return new Node(); }
  createBiquadFilter() { return new Node(); } createOscillator() { return new Node(); }
  createBufferSource() { return new Node(); }
  createBuffer(ch, len) { return { getChannelData: () => new Float32Array(len) }; }
  resume() { this.state = "running"; return Promise.resolve(); }
}
global.window = { AudioContext: FakeContext };
global.localStorage = { getItem: () => null, setItem() {} };

const { Sound } = require(path.join(__dirname, "..", "..", "web", "sound.js"));
const HUM_OFF = 0.0001;
const hum = () => Sound.layers.room.hum.gain.target;
const room = () => Sound.layers.room.gain.gain.target;
const shadow = (id) => (Sound.layers[id] ? Sound.layers[id].gain.gain.target : 0);
const master = () => Sound.master.gain.target;

function fresh() { Sound.reset(); Sound.on = true; Sound.init(); if (master() !== 1) Sound.master.gain.target = 1; advance(10000); }
const cases = {
  "mute, power out, unmute: hum stays off"() {
    fresh();
    assert.strictEqual(hum(), Sound.HUM);
    Sound.toggle(); assert.strictEqual(master(), 0);
    Sound.power(false);
    Sound.toggle();
    assert.strictEqual(master(), 1);
    assert.strictEqual(hum(), HUM_OFF);
  },
  "focus a shadow, mute, close the drawer, unmute: the room is restored"() {
    fresh();
    Sound.focusShadow("B");
    assert.ok(Math.abs(room() - Sound.ROOM * 0.35) < 1e-9 && shadow("B") === 0.045);
    Sound.toggle();
    Sound.focusShadow(null);
    Sound.toggle();
    assert.strictEqual(Sound.want.focused, null);
    assert.strictEqual(room(), Sound.ROOM);
    advance(3000);
    assert.ok(!Sound.layers.B, "the other night's layer is released");
  },
  "mute, open ending, unmute: both nights quietly mixed"() {
    fresh();
    Sound.focusShadow("D");
    Sound.toggle();
    Sound.ending("open");
    Sound.toggle();
    assert.strictEqual(room(), Sound.ROOM);
    assert.strictEqual(shadow("B"), 0.012);
    assert.strictEqual(shadow("D"), 0.012);
  },
  "power out, mute, erase ending, unmute: power and room come back, other nights gone"() {
    fresh();
    Sound.power(false);
    Sound.crossing(1, "pocket", 0, 0.5);           // a bleed of B in progress
    Sound.toggle();
    Sound.ending("erase");
    advance(2000);                                // the power returns 1.8 s in, muted or not
    Sound.toggle();
    assert.strictEqual(hum(), Sound.HUM);
    assert.strictEqual(room(), Sound.ROOM);
    assert.strictEqual(shadow("B"), HUM_OFF);
    advance(3000);
    assert.ok(!Sound.layers.B && !Sound.layers.D);
  },
  "state set before audio exists is applied when it starts"() {
    Sound.reset(); Sound.ctx = null; Sound.layers = {};
    Sound.power(false); Sound.focusShadow("B");
    Sound.init();
    assert.strictEqual(hum(), HUM_OFF);
    assert.strictEqual(shadow("B"), 0.045);
  },
  "reset clears everything"() {
    fresh();
    Sound.power(false); Sound.focusShadow("B"); Sound.ending("erase"); Sound.scene("ending");
    Sound.reset();
    assert.deepStrictEqual(Sound.want, { power: true, focused: null, ending: null, scene: null, hush: false });
    assert.deepStrictEqual(Object.keys(Sound.layers), []);
    assert.strictEqual(Sound.mix.power, null);
    assert.strictEqual(timers.length, 0, "no timer survives a reset");
    Sound.init();
    assert.strictEqual(hum(), Sound.HUM);
    assert.strictEqual(room(), Sound.ROOM);
  },
  "map: closing at once, before the tones start, is silent and safe"() {
    fresh();
    Sound.mapStop(); Sound.mapStop();
    assert.strictEqual(Sound.mapVoices, null);
    Sound.mapStart();
    assert.ok(Sound.mapVoices, "reopening starts the tones");
    Sound.mapStop();
  },
  "map: closing mid-reveal stops both tones and the pending step"() {
    fresh();
    Sound.mapStart();
    const v = Sound.mapVoices;
    Sound.mapApart(45); Sound.mapMeet();          // a step is now pending for 0.7 s
    Sound.mapStop();                              // closeMap()
    assert.ok(v.a.stopped && v.b.stopped, "both oscillators stopped");
    assert.strictEqual(Sound.mapVoices, null);
    Sound.mapStart();                             // reopen at once
    const v2 = Sound.mapVoices;
    advance(1000);                                // the old step's moment passes
    assert.strictEqual(v2.b.detune.target, 0, "an earlier opening's step never reaches the new tones");
    assert.ok(!v2.a.stopped && !v2.b.stopped);
    Sound.mapStop();
  },
  "map: closing after the full reveal, then reopening"() {
    fresh();
    Sound.mapStart(); Sound.mapMeet(); advance(800);
    const v = Sound.mapVoices;
    assert.strictEqual(v.b.detune.target, 70, "the reveal's own step still plays");
    Sound.mapStop(1.5);                           // end of the reveal
    Sound.mapStop();                              // then closeMap(): idempotent
    assert.ok(v.a.stopped && v.b.stopped);
    Sound.mapStart();
    assert.ok(Sound.mapVoices && Sound.mapVoices !== v);
    Sound.mapStop();
  },
  "map: muted, no tones start, and every close is safe"() {
    fresh();
    Sound.toggle();                               // mute
    Sound.mapStart(); Sound.mapMeet();
    assert.strictEqual(Sound.mapVoices, null);
    Sound.mapStop(); advance(1000);
    Sound.toggle();                               // unmute after closing: nothing left to hear
    assert.strictEqual(Sound.mapVoices, null);
    assert.strictEqual(Sound.mapTimer, null);
  },
  "behind the other door: the room is held low, through mute, and released after"() {
    fresh();
    Sound.hush(true);
    assert.ok(Math.abs(room() - Sound.ROOM * 0.3) < 1e-9, "held low");
    Sound.toggle(); Sound.toggle();               // mute and unmute keep it
    assert.ok(Math.abs(room() - Sound.ROOM * 0.3) < 1e-9);
    Sound.toggle(); Sound.hush(false); Sound.toggle();
    assert.strictEqual(room(), Sound.ROOM, "released while muted, restored on unmute");
    Sound.hush(true); Sound.reset();
    assert.strictEqual(Sound.want.hush, false, "reset clears it");
  },
  "erase ending: one late, quiet knock; none if reset first"() {
    fresh();
    let knocks = 0; const k = Sound.knock; Sound.knock = function (...a) { knocks++; return k.apply(this, a); };
    Sound.ending("erase"); advance(11000);
    assert.strictEqual(knocks, 1);
    Sound.ending("erase"); Sound.reset(); advance(11000);
    assert.strictEqual(knocks, 1, "a new night never hears the old one's knock");
    Sound.knock = k;
  },
  "the central line: the room at its lowest, then held low again, then released"() {
    fresh();
    Sound.hush(true); Sound.hush("deep");
    assert.ok(Math.abs(room() - Sound.ROOM * 0.12) < 1e-9, "lowest");
    Sound.hush(true);
    assert.ok(Math.abs(room() - Sound.ROOM * 0.3) < 1e-9);
    Sound.hush(false);
    assert.strictEqual(room(), Sound.ROOM);
  },
  "erase ending: opening the map or the evidence first cancels the late knock"() {
    fresh();
    let knocks = 0; const k = Sound.knock; Sound.knock = function (...a) { knocks++; return k.apply(this, a); };
    Sound.ending("erase"); advance(3000); Sound.cancelLateKnock(); advance(11000);
    assert.strictEqual(knocks, 0);
    Sound.knock = k;
  },
  "just before the central line the room is near silence"() {
    fresh();
    Sound.hush("deep"); Sound.hush("silent");
    assert.ok(Math.abs(room() - Sound.ROOM * 0.04) < 1e-9);
    Sound.hush(false);
    assert.strictEqual(room(), Sound.ROOM);
  },
  "crossing 1 is discovered (no pen); crossing 2 is written (pen)"() {
    fresh();
    let pens = 0; const p = Sound.pen; Sound.pen = function (...a) { pens++; return p.apply(this, a); };
    Sound.crossing(1, "pocket", 0, 0);
    assert.strictEqual(pens, 0);
    Sound.crossing(2, "key07", 1.0, 2.2);
    assert.strictEqual(pens, 1);
    Sound.pen = p;
  },
};
let failed = 0;
for (const [name, fn] of Object.entries(cases)) {
  try { fn(); console.log("ok   " + name); } catch (e) { failed++; console.log("FAIL " + name + ": " + e.message); }
}
process.exit(failed ? 1 : 0);
