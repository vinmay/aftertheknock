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
    assert.deepStrictEqual(Sound.want, { power: true, focused: null, ending: null, scene: null });
    assert.deepStrictEqual(Object.keys(Sound.layers), []);
    assert.strictEqual(Sound.mix.power, null);
    assert.strictEqual(timers.length, 0, "no timer survives a reset");
    Sound.init();
    assert.strictEqual(hum(), Sound.HUM);
    assert.strictEqual(room(), Sound.ROOM);
  },
};
let failed = 0;
for (const [name, fn] of Object.entries(cases)) {
  try { fn(); console.log("ok   " + name); } catch (e) { failed++; console.log("FAIL " + name + ": " + e.message); }
}
process.exit(failed ? 1 : 0);
