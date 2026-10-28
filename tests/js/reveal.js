// The crossing stage's readiness gate (web/reveal.js), with fake timers and synthetic animationend events.
"use strict";
const assert = require("assert");
const path = require("path");

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
// A minimal element: listeners, and an animationend dispatch whose target may be a descendant.
function el(name) {
  const ls = [];
  return { name, addEventListener: (t, f) => ls.push([t, f]), removeEventListener: (t, f) => {
    const i = ls.findIndex(([a, b]) => a === t && b === f); if (i >= 0) ls.splice(i, 1); },
  end(animationName, target) { ls.filter(([t]) => t === "animationend").forEach(([, f]) => f({ target: target || this, animationName })); },
  listeners: () => ls.length };
}
const { revealGate } = require(path.join(__dirname, "..", "..", "web", "reveal.js"));

const cases = {
  "ready fires on the last element's own 'in' animationend, not before"() {
    let n = 0; const last = el("stage-color"), other = el("stage-after");
    revealGate(last, 6500, () => n++);
    advance(4000);
    last.end("in", other);                 // bubbled from another element
    last.end("write");                     // another animation on the stage
    assert.strictEqual(n, 0);
    last.end("in");
    assert.strictEqual(n, 1);
    last.end("in"); advance(10000);
    assert.strictEqual(n, 1, "once only; the fallback timer was cleared");
    assert.strictEqual(last.listeners(), 0, "listener removed");
  },
  "the fallback fires when no animationend arrives"() {
    let n = 0; const last = el("stage-after");
    revealGate(last, 3500, () => n++);
    advance(3499); assert.strictEqual(n, 0);
    advance(1); assert.strictEqual(n, 1);
    last.end("in"); assert.strictEqual(n, 1);
  },
  "skip readies at once, and nothing fires again"() {
    let n = 0; const last = el("stage-color");
    const g = revealGate(last, 6500, () => n++);
    advance(1000); g.fire();
    assert.strictEqual(n, 1);
    last.end("in"); advance(10000);
    assert.strictEqual(n, 1);
  },
  "a scene change or reset cancels it: never fires"() {
    let n = 0; const last = el("stage-color");
    const g = revealGate(last, 6500, () => n++);
    g.cancel(); last.end("in"); advance(10000);
    assert.strictEqual(n, 0);
    assert.strictEqual(timers.length, 0);
  },
  "the mark itself, when there is no text line: its final animation"() {
    let n = 0; const mark = el("ink-hand");
    revealGate(mark, 6500, () => n++, ["ink-in"]);
    mark.end("write"); assert.strictEqual(n, 0);
    mark.end("ink-in"); assert.strictEqual(n, 1);
  },
};
let failed = 0;
for (const [name, fn] of Object.entries(cases)) {
  timers = []; now = 0;
  try { fn(); console.log("ok   " + name); } catch (e) { failed++; console.log("FAIL " + name + ": " + e.message); }
}
process.exit(failed ? 1 : 0);
