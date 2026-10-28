"use strict";

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const S = { game: null, view: null, replay: null, step: 0, labels: {}, mapData: null, busy: false };

const STATIC = Boolean(window.KNOCK_STATIC);
const REPLAY_LABEL = "Replay of a real recorded run — the model is not running live";

/* Static site: answer the same API calls from files exported at build time. */
const Static = { data: null, step: 0, index: null };
async function staticApi(path, body) {
  const route = path.split("?")[0];
  const index = async () => (Static.index ??= await (await fetch("replays/index.json")).json());
  if (route === "/api/status") return { live: false, static: true, replays: (await index()).length };
  if (route === "/api/replays") return index();
  if (route === "/api/replay/start") {
    const r = await fetch(`replays/${encodeURIComponent(body.id)}.json`);
    if (!r.ok) throw new Error("no such recorded run");
    const d = (Static.data = await r.json());
    Static.step = 0;
    return { game: d.id, seed: d.seed, pocket: d.pocket, view: d.steps[0].view,
             replay: { choices: d.choices, model: d.model, recorded_run: d.run_id } };
  }
  const d = Static.data;
  if (!d) throw new Error("start a recorded run first");
  if (route === "/api/choose") {
    if (body.index !== d.choices[Static.step]) throw new Error("A replay can only follow its recorded choices.");
    Static.step++;
    return { view: d.steps[Static.step].view };
  }
  if (route === "/api/map") return d.map;
  if (route === "/api/evidence") {
    const seen = d.steps.slice(0, Static.step + 1).filter((st) => st.view.kind === "crossing").length;
    return { log: d.log.slice(0, d.steps[Static.step].log_len), ops: d.ops, model: d.model, seed: d.seed,
             crossings: d.map.intrusions.slice(0, seen).map((x) => x.memory) };
  }
  if (route === "/api/verify") return { verified: true, hashes: d.state_hashes, replayed: d.state_hashes, against: "recording, when this site was built" };
  throw new Error("not available in the static replay");
}

async function api(path, body) {
  if (STATIC) return staticApi(path, body);
  const opt = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const r = await fetch(path, opt);
  const d = await r.json();
  if (!r.ok) throw Object.assign(new Error(d.error || r.statusText), { fallback: d.fallback });
  return d;
}

/* ---------------------------------------------------------------- sound */
/* Synthesized sound. Presentation only: cues follow committed state (choice ids, scene fields,
   crossing records) and never touch the engine or its logs. One master gain into a limiter;
   every method fails silently. */
const Sound = {
  ctx: null, master: null, noise: null, layers: {}, live: new Set(), mapVoices: null, pens: new Set(),
  mix: { restore: null },                       // one pending room restore; a new cue cancels it
  on: (() => { try { return localStorage.getItem("knock.sound") !== "0"; } catch { return true; } })(),
  ROOM: 0.035, HUM: 0.007,

  guard(fn) { try { if (this.ctx && this.on) fn.call(this, this.ctx, this.ctx.currentTime); } catch { /* silent */ } },

  init() {
    try {
      if (!this.ctx) {
        this.ctx = new (window.AudioContext || window.webkitAudioContext)();
        const c = this.ctx;
        const limiter = c.createDynamicsCompressor();
        limiter.threshold.value = -12; limiter.knee.value = 6; limiter.ratio.value = 12;
        limiter.attack.value = 0.003; limiter.release.value = 0.25;
        this.master = c.createGain();
        this.master.gain.value = this.on ? 1 : 0;
        this.master.connect(limiter).connect(c.destination);
        const len = c.sampleRate * 2, buf = c.createBuffer(1, len, c.sampleRate), d = buf.getChannelData(0);
        for (let i = 0; i < len; i++) d[i] = Math.random() * 2 - 1;
        this.noise = buf;
      }
      this.resume();
      if (!this.layers.room) this.startRoom();
    } catch { /* no audio: the story works without it */ }
  },
  resume() { try { if (this.ctx && this.ctx.state === "suspended") this.ctx.resume(); } catch { /* silent */ } },

  /* ---- building blocks */
  noiseSrc(c, offset = Math.random()) {
    const s = c.createBufferSource(); s.buffer = this.noise; s.loop = true;
    s.start(c.currentTime, offset * 1.9); return s;
  },
  track(node, stopAt) {
    this.live.add(node);
    node.onended = () => this.live.delete(node);
    if (stopAt !== undefined) node.stop(stopAt);
    return node;
  },
  env(c, t, peak, attack, hold, release) {
    const g = c.createGain();
    g.gain.setValueAtTime(0.0001, t);
    g.gain.exponentialRampToValueAtTime(peak, t + attack);
    g.gain.setValueAtTime(peak, t + attack + hold);
    g.gain.exponentialRampToValueAtTime(0.0001, t + attack + hold + release);
    return g;
  },
  burst(c, t, freq, q, peak, dur) {            // a short bandpassed noise transient
    const s = c.createBufferSource(); s.buffer = this.noise;
    const f = c.createBiquadFilter(); f.type = "bandpass"; f.frequency.value = freq; f.Q.value = q;
    s.connect(f).connect(this.env(c, t, peak, 0.002, 0, dur)).connect(this.master);
    s.start(t, Math.random() * 1.8); this.track(s, t + dur + 0.05);
    return s;
  },
  tone(c, t, freq, peak, attack, hold, release, type = "sine") {
    const o = c.createOscillator(); o.type = type; o.frequency.value = freq;
    o.connect(this.env(c, t, peak, attack, hold, release)).connect(this.master);
    o.start(t); this.track(o, t + attack + hold + release + 0.05);
    return o;
  },
  set(param, v, c, tau = 0.8) { param.cancelScheduledValues(c.currentTime); param.setTargetAtTime(v, c.currentTime, tau); },

  /* The room mixer: every duck goes through here. Starting a new duck cancels the previous cue's
     pending restore, so the rain can never jump back up in the middle of a later cue. */
  duck({ gain = 1, lp = 900 }, holdMs, restoreTau = 1.5, attackTau = 0.3) {
    const c = this.ctx, room = this.layers.room;
    if (!room) return;
    clearTimeout(this.mix.restore);
    this.set(room.gain.gain, this.ROOM * gain, c, attackTau);
    this.set(room.lp.frequency, lp, c, attackTau);
    this.mix.restore = setTimeout(() => {
      this.mix.restore = null;
      try { this.set(room.gain.gain, this.focused ? this.ROOM * 0.35 : this.ROOM, c, restoreTau); this.set(room.lp.frequency, 900, c, restoreTau); } catch { /* silent */ }
    }, holdMs);
  },

  /* ---- layers: room tone (rain + mains hum), and one abstract texture per shadow night */
  startRoom() {
    const c = this.ctx;
    const rain = this.noiseSrc(c), lp = c.createBiquadFilter(); lp.type = "lowpass"; lp.frequency.value = 900;
    const rg = c.createGain(); rg.gain.value = 0.0001;
    rain.connect(lp).connect(rg).connect(this.master);
    const hg = c.createGain(); hg.gain.value = 0.0001;
    const h1 = c.createOscillator(); h1.frequency.value = 60;
    const h2 = c.createOscillator(); h2.frequency.value = 120;
    const h2g = c.createGain(); h2g.gain.value = 0.45;
    h1.connect(hg); h2.connect(h2g).connect(hg); hg.connect(this.master);
    h1.start(); h2.start();
    this.layers.room = { srcs: [rain, h1, h2], gain: rg, lp, hum: hg };
    this.set(rg.gain, this.ROOM, c, 1.2);
    this.set(hg.gain, this.HUM, c, 1.2);
  },
  shadowLayer(id) {
    const c = this.ctx;
    if (this.layers[id]) return this.layers[id];
    const g = c.createGain(); g.gain.value = 0.0001; g.connect(this.master);
    const srcs = [];
    if (id === "B") {                           // differently filtered rain, with sparse ticking
      const n = this.noiseSrc(c), bp = c.createBiquadFilter(); bp.type = "bandpass"; bp.frequency.value = 2200; bp.Q.value = 0.6;
      n.connect(bp).connect(g); srcs.push(n);
      const tick = c.createBuffer(1, c.sampleRate * 1.7, c.sampleRate), td = tick.getChannelData(0);
      [0.21, 0.83, 1.38].forEach((at) => { const i = Math.floor(at * c.sampleRate); for (let k = 0; k < 220; k++) td[i + k] = (Math.random() * 2 - 1) * Math.exp(-k / 40); });
      const ts = c.createBufferSource(); ts.buffer = tick; ts.loop = true;
      const tg = c.createGain(); tg.gain.value = 0.5; ts.connect(tg).connect(g); ts.start(); srcs.push(ts);
    } else {                                    // low wind and a pipe resonance
      const n = this.noiseSrc(c), lp = c.createBiquadFilter(); lp.type = "lowpass"; lp.frequency.value = 380;
      const pipe = c.createBiquadFilter(); pipe.type = "bandpass"; pipe.frequency.value = 196; pipe.Q.value = 14;
      const pg = c.createGain(); pg.gain.value = 2.2;
      n.connect(lp).connect(g); n.connect(pipe).connect(pg).connect(g); srcs.push(n);
    }
    srcs.forEach((x) => this.live.add(x));
    return (this.layers[id] = { srcs, gain: g, release: null });
  },
  releaseLayer(id, after = 2.5) {
    const L = this.layers[id];
    if (!L) return;
    clearTimeout(L.release);
    L.release = setTimeout(() => {
      try { L.srcs.forEach((x) => { x.stop(); x.disconnect(); this.live.delete(x); }); L.gain.disconnect(); } catch { /* silent */ }
      if (this.layers[id] === L) delete this.layers[id];
    }, after * 1000);
  },
  bleed(id, level = 0.25, seconds = 4) {        // the other night leaks in for a few seconds
    this.guard((c) => {
      const L = this.shadowLayer(id); clearTimeout(L.release); clearTimeout(L.bleed);
      this.set(L.gain.gain, level * 0.14, c, 0.6);
      L.bleed = setTimeout(() => { if (this.focused !== id) { this.set(L.gain.gain, 0.0001, c, 0.8); this.releaseLayer(id, 4); } }, seconds * 1000);
    });
  },
  focusShadow(id) {                             // a source memory is open: that night forward, ours back
    this.guard((c) => {
      const room = this.layers.room;
      if (id) {
        this.focused = id;
        const L = this.shadowLayer(id); clearTimeout(L.release);
        this.set(L.gain.gain, 0.045, c, 0.5);
        clearTimeout(this.mix.restore); this.mix.restore = null;
        if (room) this.set(room.gain.gain, this.ROOM * 0.35, c, 0.5);
      } else if (this.focused) {
        const prev = this.focused; this.focused = null;
        if (this.layers[prev]) { this.set(this.layers[prev].gain.gain, 0.0001, c, 0.5); this.releaseLayer(prev, 2.5); }
        if (room) this.set(room.gain.gain, this.ROOM, c, 0.8);
      }
    });
  },

  /* ---- story cues */
  knock() {
    this.guard((c, now) => {
      let t = now + 0.3;
      for (let i = 0; i < 3; i++) {
        const v = 1 + (Math.random() - 0.5) * 0.12;
        const o = this.tone(c, t, 68 * v, 0.5 * v, 0.008, 0, 0.28);
        o.frequency.setValueAtTime(68 * v, t); o.frequency.exponentialRampToValueAtTime(40, t + 0.25);
        this.burst(c, t, 300 + Math.random() * 600, 1.2, 0.35 * v, 0.03 + Math.random() * 0.03);
        t += 0.34 + Math.random() * 0.08;
      }
    });
  },
  deadbolt() {                                  // committed Lock: a heavy bolt, then the wrong silence
    this.guard((c, now) => {
      this.burst(c, now + 0.05, 2400, 4, 0.4, 0.035);
      this.burst(c, now + 0.17, 1500, 5, 0.5, 0.05);
      const body = this.tone(c, now + 0.17, 92, 0.35, 0.005, 0.02, 0.22);
      body.frequency.exponentialRampToValueAtTime(55, now + 0.4);
      this.duck({ gain: 0.18 }, 4500, 2.5, 0.25);
      this.tone(c, now + 0.6, 73.4, 0.012, 1.2, 2.5, 2.5);
    });
  },
  latch() {                                     // committed Open: a softer latch, the room opens up
    this.guard((c, now) => {
      this.burst(c, now + 0.05, 1900, 3, 0.22, 0.03);
      this.burst(c, now + 0.14, 2600, 3, 0.15, 0.025);
      this.duck({ gain: 1.4, lp: 1700 }, 5000, 2, 0.6);
    });
  },
  power(on) {                                   // scene field power = "out" cuts the hum at once
    this.guard((c) => {
      const room = this.layers.room; if (!room) return;
      if (on) this.set(room.hum.gain, this.HUM, c, 0.15);
      else { room.hum.gain.cancelScheduledValues(c.currentTime); room.hum.gain.setValueAtTime(0.0001, c.currentTime); }
    });
  },
  powerCut() {                                  // the fake-out: everything drops for a breath, nothing comes
    this.guard((c) => {
      this.duck({ gain: 0.1 }, 2400, 1.5, 0.08);
    });
  },
  crossing(n, object, scratchDelay, scratchFor) {
    this.guard((c, now) => {
      this.stopPen();                                 // a new crossing never stacks on an old one
      this.duck({ gain: 0.3 }, 3600, 2);
      if (n === 1) {
        const p = this.tone(c, now, 46, 0.32, 0.25, 0.1, 0.9);                       // low pressure pulse
        p.frequency.exponentialRampToValueAtTime(38, now + 1.2);
        this.bleed("B");
      } else {
        [110, 117.3, 155.6].forEach((f) => this.tone(c, now, f, 0.05, 1.2, 0.8, 2));  // detuned, more dissonant
        if (object === "key07") [0.15, 0.32, 0.41].forEach((d) => {                   // a small metal tag rattle
          this.burst(c, now + scratchDelay + d, 3800 + Math.random() * 1500, 8, 0.12, 0.04);
          this.tone(c, now + scratchDelay + d, 2900 + Math.random() * 400, 0.015, 0.002, 0, 0.12, "triangle");
        });
        this.bleed("D");
      }
      this.pen(scratchDelay, scratchFor);
    });
  },
  pen(delay = 0.4, dur = 2.2) {                  // scratches spread over the mark's reveal
    this.guard((c, now) => {
      const strokes = Math.max(6, Math.round(dur / 0.09));
      for (let i = 0; i < strokes; i++) {
        const t = now + delay + (i / strokes) * dur + Math.random() * 0.03;
        const src = this.burst(c, t, 2500 + Math.random() * 2500, 3, 0.09, 0.06);
        if (src) { this.pens.add(src); src.addEventListener("ended", () => this.pens.delete(src)); }
      }
    });
  },
  stopPen() {                                    // skip, instant, "Go on", a new scene: silence the pen now
    try { this.pens.forEach((n) => { try { n.stop(); n.disconnect(); } catch { /* already done */ } this.live.delete(n); }); } catch { /* silent */ }
    this.pens.clear();
  },
  tick() { this.guard((c, now) => this.tone(c, now, 1800, 0.02, 0.002, 0, 0.05)); },

  /* ---- the map: two voices drift apart as the lanes draw, and meet at each crossing */
  mapStart() {
    this.guard((c, now) => {
      this.mapStop();
      const g = c.createGain(); g.gain.setValueAtTime(0.0001, now); g.gain.exponentialRampToValueAtTime(0.02, now + 1); g.connect(this.master);
      const a = c.createOscillator(), b = c.createOscillator();
      a.frequency.value = 147; b.frequency.value = 147; a.connect(g); b.connect(g); a.start(); b.start();
      this.live.add(a); this.live.add(b);
      this.mapVoices = { a, b, g };
    });
  },
  mapApart(cents) { this.guard((c) => { const v = this.mapVoices; if (v) this.set(v.b.detune, cents, c, 0.6); }); },
  mapMeet() {
    this.guard((c, now) => {
      const v = this.mapVoices; if (!v) return;
      this.set(v.b.detune, 0, c, 0.12);
      setTimeout(() => this.mapApart(70), 700);
    });
  },
  mapStop(fade = 0) {
    const v = this.mapVoices; this.mapVoices = null;
    if (!v) return;
    try {
      const c = this.ctx, t = c.currentTime;
      v.g.gain.cancelScheduledValues(t);
      if (fade) v.g.gain.setTargetAtTime(0.0001, t, fade / 3); else v.g.gain.setValueAtTime(0, t);
      [v.a, v.b].forEach((o) => { o.stop(t + fade + 0.05); this.live.delete(o); });
    } catch { /* silent */ }
  },

  /* ---- endings, per their meaning */
  ending(which) {
    this.guard((c) => {
      this.focusShadow(null);
      if (which === "erase") {                  // the other night fades out entirely; the power and room return
        ["B", "D"].forEach((id) => { if (this.layers[id]) { this.set(this.layers[id].gain.gain, 0.0001, c, 0.6); this.releaseLayer(id, 3); } });
        setTimeout(() => this.power(true), 1800);
      } else {                                  // both nights stay, quietly mixed
        ["B", "D"].forEach((id) => { const L = this.shadowLayer(id); clearTimeout(L.release); this.set(L.gain.gain, 0.012, c, 2); });
      }
    });
  },

  /* ---- hygiene */
  toggle() {
    this.on = !this.on;
    try { localStorage.setItem("knock.sound", this.on ? "1" : "0"); } catch { /* per page only */ }
    try {
      if (this.master) {
        const t = this.ctx.currentTime;
        this.master.gain.cancelScheduledValues(t);
        this.master.gain.setValueAtTime(this.on ? 1 : 0, t);     // mute stops output immediately
      }
    } catch { /* silent */ }
    if (!this.on) this.mapStop();
    $("#sound-toggle").setAttribute("aria-pressed", String(this.on));
  },
  reset() {                                     // a new night: stop and release everything
    this.mapStop(); this.stopPen();
    clearTimeout(this.mix.restore); this.mix.restore = null;
    try { this.live.forEach((n) => { try { n.stop(); n.disconnect(); } catch { /* already stopped */ } }); } catch { /* silent */ }
    this.live.clear();
    Object.values(this.layers).forEach((L) => { clearTimeout(L.release); try { L.srcs?.forEach((x) => { x.stop(); x.disconnect(); }); } catch { /* silent */ } });
    this.layers = {}; this.focused = null;
  },
};
["pointerdown", "keydown", "touchstart"].forEach((ev) => document.addEventListener(ev, () => Sound.resume(), { passive: true }));

/* ---------------------------------------------------------------- start */
const LINKS = {
  live: "https://knock-live.onrender.com/",
  replay: "https://knock-replay.onrender.com/",
  repo: "https://github.com/vinmay/aftertheknock",
};
const article = (w) => (/^(two|three|four|five|six|seven|eight|nine|ten|some|few|several)\b/i.test(w) || (/s$/i.test(w) && !/(ss|us|is)$/i.test(w)) ? w : (/^[aeiou]/i.test(w) ? "an " : "a ") + w);
/* "lock / roof / erase" -> plain words. Shown only after a card is opened (no spoilers in the list). */
function pathWords(path) {
  const [f1, f2, end] = path.split(" / ");
  const a = { lock: "kept the door locked", open: "opened the door" }[f1] || f1;
  const b = { basement: "went down to the basement", roof: "went up to the roof" }[f2] || f2;
  const c = { erase: "erased the other night", open: "let the other night in" }[end] || end;
  return `Someone ${a}, ${b}, and ${c}.`;
}

async function boot() {
  try {
    const st = (S.status = await api("/api/status"));
    // A quiet, true line about where the typed text goes (not a modal).
    $("#pocket-note").textContent = st.hosted
      ? "Live play sends what you type to a hosted Gemma model. Keep it fictional. Best with sound."
      : "Live play runs a Gemma model on this machine; what you type stays here. Best with sound.";
    if (st.static) {
      $("#start").hidden = true;
      $("#play-live").hidden = false;
      $("#static-note").hidden = false;
    } else if (!st.live) {
      fallBackToReplays("The live model isn't available right now. These are replays of real recorded runs.");
    }
    const list = await api("/api/replays");
    if (list.length) {
      $("#replays").hidden = false;
      $("#replays-head").textContent = st.static || !st.live ? "Watch a night" : "Or watch a recorded night";
      $("#replay-list").innerHTML = list.map((r, i) => `
        <li class="card-night">
          <button class="card-head" aria-expanded="false" aria-controls="night-${i}">
            <span class="card-title">${esc(r.title || `Night ${i + 1}`)}${r.recommended ? ` <span class="rec-badge">Recommended first watch</span>` : ""}
              <span class="card-sub">${esc(article(r.pocket))} \u00b7 Recorded run</span></span>
          </button>
          <div class="card-body" id="night-${i}" hidden>
            <p>${esc(pathWords(r.path))}</p>
            <button class="primary watch" data-id="${esc(r.id)}">Watch this night</button>
            <details class="tech"><summary>Technical details</summary><p class="small muted">Seed ${esc(r.seed)} · recorded with ${esc(r.model)} · re-verified against its recorded state</p></details>
          </div>
        </li>`).join("");
      $("#replay-list").querySelectorAll(".card-head").forEach((h) => h.addEventListener("click", () => {
        const body = h.nextElementSibling, open = body.hidden;
        body.hidden = !open; h.setAttribute("aria-expanded", String(open));
      }));
      $("#replay-list").querySelectorAll(".watch").forEach((b) => b.addEventListener("click", () => startReplay(b.dataset.id)));
    }
  } catch (e) {
    $("#status-line").textContent = "Could not reach the server.";
  }
}

function howItWorks() {
  if (toggledShut("how")) return;
  const st = S.status || {};
  const mode = st.static
    ? `<p>This page plays <b>replays of real recorded runs</b>. The model is not running here. Each night was generated by Gemma 4 E2B on a consumer GTX 1660 Ti and recorded; when this site was built, every recording was re-run through the engine and checked against its recorded state. To play a night that is generated as you play, <a href="${LINKS.live}">play live</a>.</p>`
    : st.hosted
      ? `<p>When you press Begin, the other night is generated <b>live</b> by ${esc(st.model)}, an open-weight Gemma model, through ${esc(st.provider)}. If the model is unavailable, this page shows replays of real recorded runs instead, and says so.</p>`
      : `<p>When you press Begin, the other night is generated <b>live, on this machine</b>, by ${esc(st.model || "a local Gemma model")}.</p>`;
  openDrawer(`<p class="kicker">how this works</p>
    <h3>The night you didn't choose keeps going</h3>
    <p>Every choice splits the night in two. You live one side. The other side keeps happening without you: a small open model proposes what the people there do next, one event at a time, and a strict rule engine decides whether each event is possible before it becomes fact.</p>
    <p>Twice, something from that other night crosses into yours. Each crossing points to the exact event that made it. Follow the thread at the end, or switch on <b>evidence</b> at the top to see every proposal, rejection and commit as it happens.</p>
    <h3>What's running</h3>
    ${mode}
    <h3>Run it yourself</h3>
    <pre>scripts/setup          # needs uv and Ollama
uv run knock-server    # http://localhost:8047</pre>
    <p class="small">Code, tests and evaluation: <a href="${LINKS.repo}">${LINKS.repo.replace("https://", "")}</a>${st.static ? "" : ` · Replays: <a href="${LINKS.replay}">${LINKS.replay.replace("https://", "")}</a>`}</p>`, null, "how");
}
$("#how").addEventListener("click", howItWorks);
$("#how-menu").addEventListener("click", () => { setSettings(false); howItWorks(); });
/* The peephole warms while there is something in the pocket field, and cools when it's cleared. */
$("#pocket").addEventListener("input", (ev) => $(".door")?.classList.toggle("warm", ev.target.value.trim() !== ""));
["mouseenter", "focus"].forEach((ev) => $("#begin").addEventListener(ev, () => $(".door")?.classList.add("lit")));
["mouseleave", "blur"].forEach((ev) => $("#begin").addEventListener(ev, () => $(".door")?.classList.remove("lit")));

$("#start").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  if (S.busy) return;
  Sound.reset(); Sound.init();
  S.busy = true; $("#begin").disabled = true; $("#begin").textContent = "…";
  try {
    const d = await api("/api/new", { pocket: $("#pocket").value });
    S.game = d.game; S.replay = null; S.pocket = d.pocket;
    setMode(`<span class="tag live">live</span>`, d.seed, `${esc(d.model || "")} generating the other night now`);
    show(d.view);
    Sound.knock();
  } catch (e) {
    if (e.fallback === "replay") fallBackToReplays(`${e.message} These are replays of real recorded runs; the model is not running live.`);
    else $("#status-line").textContent = e.message;
  } finally {
    S.busy = false; $("#begin").disabled = false; $("#begin").textContent = "Begin your night";
  }
});

async function startReplay(id) {
  Sound.reset(); Sound.init();
  const d = await api("/api/replay/start", { id });
  S.game = d.game; S.replay = d.replay; S.step = 0; S.pocket = d.pocket;
  const liveHref = STATIC ? LINKS.live : location.pathname;
  setMode(`<span class="tag replay">recorded night</span>`, d.seed,
    `following a path someone already took · ${esc(REPLAY_LABEL.split("— ")[1] || REPLAY_LABEL)} · <a href="${liveHref}">Start a live night</a>`);
  show(d.view);
  Sound.knock();
}

function fallBackToReplays(why) {
  $("#start").hidden = true;
  $("#static-why").textContent = why;
  $("#static-note").hidden = false;
  $("#replays").hidden = false;
}

function setMode(chip, seed, detail) {
  $("#mode").innerHTML = `<button class="chip" id="chip" aria-expanded="false" title="What is running">${chip}</button>` +
    `<span class="seed">seed ${esc(seed)}</span><span class="detail">${detail}</span>`;
  $("#chip").addEventListener("click", () => {
    const open = $("#mode").classList.toggle("open");
    $("#chip").setAttribute("aria-expanded", String(open));
  });
}


/* ---------------------------------------------------------------- scenes */
function paragraphs(text) {
  return text.split(/\n\s*\n/).map((p) => p.trim()).filter(Boolean);
}

/* Reading preferences. "Show text immediately" turns off every fade and slow reveal. */
const Prefs = {
  get(k) { try { return localStorage.getItem("knock." + k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem("knock." + k, v); } catch { /* private mode: keep for this page only */ } },
};
let INSTANT = Prefs.get("instant") === "1";
const instantText = () => INSTANT || REDUCED;
function applyInstant() {
  document.body.classList.toggle("instant-text", instantText());
  if (instantText()) Sound.stopPen();
  const b = $("#instant-toggle");
  if (b) b.setAttribute("aria-pressed", String(INSTANT));
}
const FADE_MS = 250;
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

function sceneHtml(v) {
  if (v.kind === "crossing") {
    const mark = v.crossing.inscription;
    return paragraphs(v.text).map((p) => {
      const t = esc(p).replace(esc(mark), `<button class="ink" title="Where did this come from?">${esc(mark)}</button>`);
      return `<p>${t}</p>`;
    }).join("") + `<p><button class="trace-hint">where did this come from? ↗</button></p>`;
  }
  return paragraphs(v.text).map((p) => `<p>${esc(p)}</p>`).join("");
}

async function show(v) {
  Sound.stopPen();                                  // a scene change never carries the pen with it
  S.view = v;
  $("#title").hidden = true;
  $("#scene").hidden = false;
  $("#scene").className = "screen kind-" + (v.kind || "scene");
  setClock(v.clock);
  if (v.power === "out") {
    // The lights go: the hum cuts, the room drops for a breath, the screen darkens. Nothing appears.
    Sound.power(false); Sound.powerCut();
    if (!instantText()) {
      document.body.classList.add("blackout");
      setTimeout(() => document.body.classList.remove("blackout"), 1300);
    }
  }
  if (v.kind === "ending") Sound.ending(v.ending);
  const cur = $("#current");
  cur.classList.remove("fade-in"); void cur.offsetWidth; cur.classList.add("fade-in");
  const text = $("#text");
  text.innerHTML = sceneHtml(v);
  text.querySelectorAll(".ink, .trace-hint").forEach((b) => b.addEventListener("click", (ev) => { ev.stopPropagation(); openTrace(v.trace); }));
  const ch = $("#choices");
  ch.innerHTML = "";
  ch.hidden = true;
  if (v.kind === "map" || v.kind === "final" || v.kind === "ending") S.mapSeen = true;
  if (!$("#evidence").hidden) loadEvidence();
  if (S.history?.length) cur.scrollIntoView({ behavior: REDUCED ? "auto" : "smooth", block: "start" });
  else window.scrollTo({ top: 0 });

  if (v.kind === "crossing") stageCrossing(v.crossing, v.scene === "crossing2");
  else if (!instantText()) await wait(FADE_MS);
  if (S.view !== v) return;

  const recorded = S.replay ? S.replay.choices[S.step] : null;
  if (S.replay && recorded != null && v.choices.length) {
    // A replay follows its recording: show the decision that was made, and name the one that wasn't.
    const b = document.createElement("button");
    b.className = "recorded";
    b.innerHTML = `<span class="num">${recorded + 1}</span><span><span class="rec-k">Recorded choice</span> ${esc(v.choices[recorded])}</span>`;
    b.dataset.index = recorded;
    b.addEventListener("click", () => choose(recorded));
    ch.appendChild(b);
    const others = v.choices.filter((_, i) => i !== recorded);
    if (others.length) ch.insertAdjacentHTML("beforeend", `<p class="not-taken-note">Not taken in this recording: ${others.map(esc).join(", ")}</p>`);
  } else {
    v.choices.forEach((label, i) => {
      const b = document.createElement("button");
      b.innerHTML = `<span class="num">${i + 1}</span>${esc(label)}`;
      b.dataset.index = i;
      b.addEventListener("click", () => choose(i));
      ch.appendChild(b);
    });
  }
  if (v.kind === "ending") {
    ch.classList.add("afterword");
    [["Explore the nights you didn't live", () => openMap()],
     ["Verify this run", () => openVerify()],
     ["Begin another night", () => (location.href = location.pathname)]].forEach(([label, fn], i) => {
      const b = document.createElement("button");
      b.innerHTML = `<span class="num">${i + 1}</span>${esc(label)}`;
      b.dataset.index = i;
      b.addEventListener("click", fn); ch.appendChild(b);
    });
  } else ch.classList.remove("afterword");
  ch.hidden = false;
  if (v.kind === "map") {
    // The map waits for the player: read the scene first, then open it yourself.
    const b = document.createElement("button");
    b.className = "cta map-open";
    b.textContent = "Look at the map";
    b.addEventListener("click", () => openMap());
    ch.prepend(b);
  }
}

/* Move the finished scene into the history, keeping the option you didn't take. */
function archive(chosen) {
  const v = S.view;
  if (!v) return;
  const entry = document.createElement("div");
  entry.className = "past";
  entry.innerHTML = sceneHtml(v);
  entry.querySelectorAll(".trace-hint").forEach((b) => b.parentElement.remove());
  entry.querySelectorAll(".ink").forEach((b) => b.addEventListener("click", () => openTrace(v.trace)));
  if (v.choices.length > 1) {
    entry.insertAdjacentHTML("beforeend", `<ul class="past-choices">${v.choices.map((c, i) =>
      `<li class="${i === chosen ? "taken" : "not-taken"}">${esc(c)}</li>`).join("")}</ul>`);
  } else if (v.choices.length === 1) {
    entry.insertAdjacentHTML("beforeend", `<ul class="past-choices"><li class="taken">${esc(v.choices[0])}</li></ul>`);
  }
  $("#history").appendChild(entry);
  (S.history ||= []).push(v.scene);
}

async function choose(i) {
  if (S.busy) return;
  S.busy = true;
  Sound.tick();
  $("#choices").querySelectorAll("button").forEach((b) => (b.disabled = true));
  const wait = setTimeout(() => {
    $("#choices").insertAdjacentHTML("beforeend", `<p class="waiting">…</p>`);
  }, 500);
  try {
    const prev = S.view;
    const d = await api("/api/choose", { game: S.game, index: i });
    clearTimeout(wait);
    if (prev && prev.kind === "fork1") {         // the committed choice, by id
      const id = (prev.choice_ids || [])[i];
      if (id === "lock") Sound.deadbolt(); else if (id === "open") Sound.latch();
    }
    S.step++;
    archive(i);
    S.busy = false;
    await show(d.view);
  } catch (e) {
    clearTimeout(wait);
    $("#choices").insertAdjacentHTML("beforeend", `<p class="small bad">${esc(e.message)}</p>`);
    $("#choices").querySelectorAll("button").forEach((b) => (b.disabled = false));
  } finally {
    S.busy = false;
  }
}

/* 1-9 choose; Enter takes a single choice. */
document.addEventListener("keydown", (ev) => {
  if (ev.target.tagName === "INPUT" || ev.target.tagName === "TEXTAREA") return;
  if (!$("#crossing-stage").hidden || !$("#map").hidden || !$("#drawer").hidden) return;
  if ($("#scene").hidden || $("#choices").hidden) return;
  const buttons = [...$("#choices").querySelectorAll("button[data-index]:not([disabled])")];
  const n = parseInt(ev.key, 10);
  if (n >= 1) {
    const b = $("#choices").querySelector(`button[data-index="${n - 1}"]`);
    if (b && !b.disabled) { ev.preventDefault(); b.focus(); b.click(); }
  } else if (ev.key === "Enter" && buttons.length === 1 && document.activeElement === document.body) {
    ev.preventDefault(); buttons[0].focus(); buttons[0].click();
  }
});

function setClock(t) {
  if (t) { $("#clock").textContent = t; $("#clock").hidden = false; }
}

/* ---------------------------------------------------------------- the crossing */
const REDUCED = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/* Props: SVG only. The inscription is drawn last, in a hand, over the printed object. */
function inscriptionSize(text, room = 380) {
  return Math.max(22, Math.min(64, Math.round(room / Math.max(text.length, 5))));
}
/* Props by material (decided by the server from what the player typed): paper is written on,
   metal is engraved, small hard things are scratched, anything else is an honest close-up. */
function propSvg(c) {
  if (c.object === "key07") return paperSvg(c);
  const m = c.material || "paper";
  if (m === "metal") return metalSvg(c);
  if (m === "small") return smallSvg(c);
  if (m === "unknown") return unknownSvg(c);
  return paperSvg(c);
}
function noun() { return (S.pocket || "").toLowerCase().split(" ").pop(); }
function fitSize(text, room, max = 52, min = 16) { return Math.max(min, Math.min(max, Math.round(room / Math.max(text.length, 3)))); }

function metalSvg(c) {
  const mark = esc(c.inscription), n = noun(), obj = esc(S.pocket || "object");
  const defs = `<defs>
    <radialGradient id="mg" cx="0.35" cy="0.3" r="0.8"><stop offset="0" stop-color="#f6e7c4"/><stop offset="0.35" stop-color="#c7a764"/><stop offset="0.75" stop-color="#7d6232"/><stop offset="1" stop-color="#3b2d15"/></radialGradient>
    <linearGradient id="glint" x1="0" x2="1"><stop offset="0" stop-color="#fff" stop-opacity="0"/><stop offset="0.5" stop-color="#fff" stop-opacity="0.65"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></linearGradient>
  </defs>`;
  if (/^(ring|rings|band|bracelet)$/.test(n)) {
    // A band seen at an angle; the engraving follows the curve of its face.
    return `<svg class="prop" viewBox="0 0 420 220" role="img" aria-label="Your ${obj}: a metal band. Engraved around it: ${mark}">${defs}
      <ellipse cx="210" cy="118" rx="150" ry="72" fill="none" stroke="url(#mg)" stroke-width="42"/>
      <ellipse cx="210" cy="118" rx="150" ry="72" fill="none" stroke="#1d150b" stroke-opacity="0.35" stroke-width="2"/>
      <path d="M95,82 Q140,52 190,48" stroke="url(#glint)" stroke-width="5" fill="none" stroke-linecap="round"/>
      <path id="arc" d="M66,128 C92,218 328,218 354,128" fill="none"/>
      <text class="engrave" style="font-size:${fitSize(c.inscription, 640, 44, 18)}px"><textPath href="#arc" startOffset="50%" text-anchor="middle">${mark}</textPath></text>
    </svg>`;
  }
  if (/^(key|keys)$/.test(n)) {
    return `<svg class="prop" viewBox="0 0 420 220" role="img" aria-label="Your ${obj}: a metal key. Engraved on it: ${mark}">${defs}
      <circle cx="110" cy="110" r="62" fill="url(#mg)"/><circle cx="110" cy="110" r="16" fill="#0e0d0c"/>
      <path d="M168,96 L380,96 L380,124 L352,124 L352,144 L330,144 L330,124 L312,124 L312,140 L292,140 L292,124 L168,124 Z" fill="url(#mg)"/>
      <path d="M70,72 Q100,56 132,58" stroke="url(#glint)" stroke-width="4" fill="none" stroke-linecap="round"/>
      <text x="268" y="119" text-anchor="middle" class="engrave" style="font-size:${fitSize(c.inscription, 300, 34, 14)}px">${mark}</text>
    </svg>`;
  }
  // Coins, medals, lockets, watches, pins, pennies: a disc, the engraving along its rim.
  return `<svg class="prop" viewBox="0 0 420 220" role="img" aria-label="Your ${obj}: a small metal disc. Engraved on its face: ${mark}">${defs}
    <circle cx="210" cy="110" r="96" fill="url(#mg)"/>
    <circle cx="210" cy="110" r="84" fill="none" stroke="#3b2d15" stroke-opacity="0.45" stroke-width="2"/>
    <path d="M150,48 Q190,26 238,30" stroke="url(#glint)" stroke-width="5" fill="none" stroke-linecap="round"/>
    <path id="rim" d="M140,118 A72,72 0 0 0 280,118" fill="none"/>
    ${c.inscription.length <= 5
      ? `<text x="210" y="126" text-anchor="middle" class="engrave" style="font-size:${fitSize(c.inscription, 200, 48, 20)}px">${mark}</text>`
      : `<text class="engrave" style="font-size:${fitSize(c.inscription, 300, 24, 11)}px"><textPath href="#rim" startOffset="50%" text-anchor="middle">${mark}</textPath></text>`}
  </svg>`;
}

function smallSvg(c) {
  const mark = esc(c.inscription), n = noun(), obj = esc(S.pocket || "object");
  // The mark is scratched across the surface and deliberately overruns it: impossible writing.
  const fs = fitSize(c.inscription, 520, 84, 30);       // larger than the object on purpose
  const scratch = (y) => `<text x="210" y="${y}" text-anchor="middle" class="scratch" style="font-size:${fs}px">${mark}</text>`;
  let shape, what;
  if (n === "pick") { shape = `<path d="M150,40 C200,24 280,30 300,64 C312,96 250,170 214,196 C196,206 186,200 176,186 C150,140 116,96 120,70 C122,54 132,46 150,40 Z" fill="#7a2e1f"/><path d="M160,56 C200,44 252,48 272,66" stroke="#f1c9b9" stroke-opacity="0.35" stroke-width="3" fill="none"/>`; what = "a small plastic pick"; }
  else if (n === "lighter") { shape = `<rect x="150" y="60" width="120" height="150" rx="10" fill="#273b52"/><rect x="150" y="30" width="120" height="38" rx="6" fill="#9aa3ab"/><circle cx="180" cy="48" r="9" fill="#5e676f"/><rect x="160" y="76" width="8" height="120" fill="#fff" fill-opacity="0.12"/>`; what = "a lighter"; }
  else if (/^buttons?$/.test(n)) { shape = `<circle cx="210" cy="112" r="88" fill="#6b4a2e"/><circle cx="210" cy="112" r="70" fill="none" stroke="#3e2a18" stroke-width="3"/>${[[190, 92], [230, 92], [190, 132], [230, 132]].map(([x, y]) => `<circle cx="${x}" cy="${y}" r="8" fill="#0e0d0c"/>`).join("")}`; what = "a button"; }
  else if (/^dice?$/.test(n)) { shape = `<rect x="130" y="34" width="160" height="160" rx="22" fill="#e8e2d4"/>${[[170, 74], [250, 154], [210, 114]].map(([x, y]) => `<circle cx="${x}" cy="${y}" r="12" fill="#1a1612"/>`).join("")}`; what = "a die"; }
  else if (/^(pen|pens|lipstick|usb|chalk|crayon)$/.test(n)) { shape = `<rect x="60" y="88" width="300" height="48" rx="24" fill="#2c2a33"/><rect x="300" y="88" width="60" height="48" rx="20" fill="#8c8a93"/><rect x="74" y="96" width="210" height="6" rx="3" fill="#fff" fill-opacity="0.15"/>`; what = `a ${esc(n)}`; }
  else { shape = `<path d="M110,120 C104,70 170,40 230,46 C300,52 330,96 320,140 C310,184 250,200 196,194 C140,188 114,160 110,120 Z" fill="#5d5a55"/><path d="M150,72 C190,56 240,58 270,72" stroke="#fff" stroke-opacity="0.18" stroke-width="4" fill="none"/>`; what = `a small hard ${esc(n || "object")}`; }
  return `<svg class="prop" viewBox="0 0 420 220" role="img" aria-label="Your ${obj}: ${what}. Scratched across its surface: ${mark}">${shape}${scratch(124)}</svg>`;
}

function unknownSvg(c) {
  // We don't know what it looks like, so we don't pretend: a dark close-up, in the player's own words.
  const mark = esc(c.inscription), obj = esc(S.pocket || "object");
  return `<svg class="prop" viewBox="0 0 420 220" role="img" aria-label="A dark close-up of your ${obj}. Marked across it: ${mark}">
    <defs><radialGradient id="dim" cx="0.5" cy="0.45" r="0.65"><stop offset="0" stop-color="#2a2622"/><stop offset="1" stop-color="#0b0a09"/></radialGradient></defs>
    <rect x="10" y="10" width="400" height="200" rx="6" fill="url(#dim)" stroke="#3a3631"/>
    <text x="398" y="30" text-anchor="end" class="printed own">${esc((S.pocket || "").toUpperCase())}</text>
    <text x="210" y="128" text-anchor="middle" class="scratch chalk" style="font-size:${fitSize(c.inscription, 420, 60, 24)}px">${mark}</text>
  </svg>`;
}

function paperSvg(c) {
  const mark = esc(c.inscription), fs = inscriptionSize(c.inscription, c.object === "key07" ? 300 : 380);
  const pocket = (S.pocket || "").toLowerCase();
  if (c.object === "key07") {
    return `<svg class="prop" viewBox="0 0 420 210" role="img" aria-label="The brass key's paper tag. Printed: BASEMENT 07. Handwritten: ${mark}">
      <path d="M118,104 C80,96 52,70 44,48" fill="none" stroke="#b9975a" stroke-width="2.5"/>
      <circle cx="40" cy="40" r="18" fill="none" stroke="#c9a96a" stroke-width="5"/>
      <circle cx="40" cy="40" r="18" fill="none" stroke="#f3dfae" stroke-width="1" stroke-dasharray="6 40"/>
      <path d="M160,34 L396,34 L396,186 L160,186 L118,146 L118,74 Z" fill="#cdb07a" stroke="#9c8050" stroke-width="1.5"/>
      <circle cx="140" cy="110" r="11" fill="none" stroke="#9c8050" stroke-width="5"/>
      <circle cx="140" cy="110" r="7" fill="#0e0d0c"/>
      <text x="282" y="70" text-anchor="middle" class="printed">BASEMENT 07</text>
      <line x1="178" y1="84" x2="384" y2="84" stroke="#8a6f43" stroke-width="1"/>
      <text x="280" y="150" text-anchor="middle" class="ink-hand" style="font-size:${fs}px">${mark}</text>
    </svg>`;
  }
  if (/ticket|stub|pass|transfer|fare/.test(pocket)) {
    const holes = Array.from({ length: 15 }, (_, i) => `<circle cx="14" cy="${22 + i * 12}" r="4.2" fill="#0e0d0c"/>`).join("");
    const tear = Array.from({ length: 15 }, (_, i) => `<circle cx="96" cy="${22 + i * 12}" r="2" fill="#0e0d0c" opacity="0.55"/>`).join("");
    return `<svg class="prop" viewBox="0 0 420 210" role="img" aria-label="Your ${esc(S.pocket)}. Handwritten on it: ${mark}">
      <rect x="10" y="12" width="400" height="186" rx="3" fill="#ece4d1"/>
      ${holes}${tear}
      <text transform="translate(60,105) rotate(-90)" text-anchor="middle" class="printed small">VALID TONIGHT ONLY</text>
      <text x="112" y="44" class="printed route">NIGHT BUS \u00b7 ROUTE 14 \u00b7 2.75</text>
      <line x1="112" y1="54" x2="398" y2="54" stroke="#b9ad93" stroke-width="1"/>
      <path d="M300,12 L262,198" stroke="#000" stroke-opacity="0.07" stroke-width="3"/>
      <path d="M302,12 L264,198" stroke="#fff" stroke-opacity="0.35" stroke-width="1"/>
      <text x="398" y="188" text-anchor="end" class="printed serial">N\u00ba 0047193</text>
      <text x="256" y="138" text-anchor="middle" class="ink-hand" style="font-size:${fs}px">${mark}</text>
    </svg>`;
  }
  if (/receipt/.test(pocket)) {
    // Decorative only: a fictional store and items the story never refers to.
    const row = (y, a, b) => `<text x="128" y="${y}" class="printed faded">${a}</text><text x="292" y="${y}" text-anchor="end" class="printed faded">${b}</text>`;
    return `<svg class="prop" viewBox="0 0 420 210" role="img" aria-label="Your ${esc(S.pocket)}. Handwritten on it: ${mark}">
      <path d="M110,8 L310,8 L310,196 L300,202 L290,196 L280,202 L270,196 L260,202 L250,196 L240,202 L230,196 L220,202 L210,196 L200,202 L190,196 L180,202 L170,196 L160,202 L150,196 L140,202 L130,196 L120,202 L110,196 Z" fill="#efe9dc"/>
      <path d="M110,104 L310,100" stroke="#000" stroke-opacity="0.08" stroke-width="3"/>
      <text x="210" y="30" text-anchor="middle" class="printed">ALL-NITE CORNER STORE</text>
      <text x="210" y="44" text-anchor="middle" class="printed faded">OPEN 24 HRS \u00b7 10:52 PM</text>
      <line x1="124" y1="52" x2="296" y2="52" stroke="#b9ad93" stroke-dasharray="3 3"/>
      ${row(68, "COFFEE", "1.80")}${row(82, "AA BATTERIES", "4.99")}
      <line x1="124" y1="164" x2="296" y2="164" stroke="#b9ad93" stroke-dasharray="3 3"/>
      ${row(180, "TOTAL", "6.79")}
      <text x="210" y="134" text-anchor="middle" class="ink-hand" style="font-size:${Math.min(fs, 50)}px">${mark}</text>
    </svg>`;
  }
  return `<svg class="prop" viewBox="0 0 420 210" role="img" aria-label="Your ${esc(S.pocket)}. Handwritten on it: ${mark}">
    <path d="M14,16 L404,12 L408,196 L18,200 Z" fill="#e9e1cf"/>
    <path d="M210,14 L212,198" stroke="#000" stroke-opacity="0.08" stroke-width="3"/>
    <path d="M212,14 L214,198" stroke="#fff" stroke-opacity="0.4" stroke-width="1"/>
    <text x="392" y="40" text-anchor="end" class="printed small">${esc((S.pocket || "").toUpperCase())}</text>
    <text x="210" y="128" text-anchor="middle" class="ink-hand" style="font-size:${fs}px">${mark}</text>
  </svg>`;
}

function stageCrossing(c, second = false) {
  const el = $("#crossing-stage");
  el.classList.remove("ready", "instant");
  el.innerHTML = `
    <div class="stage-inner">
      <p class="stage-before">${esc(c.before)}</p>
      <div class="prop-wrap${c.object === "key07" ? " keytag" : ""}">${propSvg(c)}</div>
      <p class="stage-after">${esc(`${c.after} ${c.who}`.trim())}</p>
      ${c.color ? `<p class="stage-color">${esc(c.color)}</p>` : ""}
      <button class="primary stage-go" id="stage-go">Go on</button>
    </div>`;
  el.hidden = false;
  el.classList.toggle("instant", instantText());
  document.body.classList.add("staged");
  if (!instantText()) {
    document.body.classList.add("flicker");
    setTimeout(() => document.body.classList.remove("flicker"), 1000);
  }
  // The second crossing escalates: a low swell under the pen, not the pen alone.
  // The mark is drawn from 0.4 s to 2.6 s; the scratches follow it (and still play, faster, without animation).
  if (instantText()) Sound.crossing(second ? 2 : 1, c.object, 0.05, 0.8);
  else Sound.crossing(second ? 2 : 1, c.object, 0.4, 2.2);
  const close = () => { Sound.stopPen(); el.hidden = true; document.body.classList.remove("staged"); $("#current .ink")?.focus(); };
  $("#stage-go").addEventListener("click", close);
  const ready = () => { el.classList.add("ready"); $("#stage-go")?.focus(); };
  if (instantText()) ready();
  else {
    const t = setTimeout(ready, 3100);              // the mark has finished writing itself
    el.addEventListener("click", (ev) => {           // click anywhere: finish now
      if (ev.target.closest("#stage-go")) return;
      clearTimeout(t); el.classList.add("instant"); Sound.stopPen(); ready();
    }, { once: true });
  }
}

/* ---------------------------------------------------------------- trace */
function factLine(f) {
  const [op, path, value] = f;
  return `${esc(path)} ${op === "append" ? "+=" : "="} ${esc(JSON.stringify(value))}`;
}

function attemptsHtml(attempts) {
  if (!attempts || !attempts.length) return "";
  return `<h3>Validator</h3><p class="small muted">Each line is an original model proposal, shown unmodified, and what the validator decided. Only accepted proposals become events; the engine derives their effects itself.</p><ul class="attempts">` + attempts.map((a) => {
    if (a.kind === "fallback") return `<li><span class="fb">FALLBACK</span> hand-written event committed after three rejected proposals<pre>${esc(JSON.stringify(a.proposal))}</pre></li>`;
    const d = a.decision || {};
    const verdict = d.accepted ? `<span class="ok">ACCEPTED</span>` : `<span class="bad">REJECTED</span> ` +
      (d.violations || []).map((v) => `${esc(v.rule)}: ${esc(v.detail)}`).join("; ");
    return `<li>attempt ${a.attempt + 1}: ${verdict}${a.latency_s ? ` · ${a.latency_s.toFixed(2)} s` : ""}<pre>${esc(JSON.stringify(a.proposal))}</pre></li>`;
  }).join("") + `</ul>`;
}

/* Story -> memory -> proof. The memory and labels are built by the engine from committed events;
   raw ids, state paths and the model's own words stay inside "View proof". */
function memoryHtml(mem, kicker = "where it came from") {
  // A flashback rendered from the shadow's committed events; the plain sentence if no scene.
  const scene = mem.scene && mem.scene.length
    ? `<p class="flash-night">${esc(mem.night[0].toUpperCase() + mem.night.slice(1))}:</p>
       <div class="flashback">${mem.scene.map((l) => `<p>${esc(l.line)}</p>`).join("")}</div>
       <p class="flash-close">${esc(mem.closing)}</p>`
    : `<div class="quote shadowq memory">${esc(mem.memory)}</div>`;
  return `<p class="kicker">${esc(kicker)}</p>${scene}
    <ol class="route">
      <li><span class="route-k">Written there</span> ${esc(mem.source.replace(/^Written there: /, ""))}</li>
      <li><span class="route-k">Appeared here</span> ${esc(mem.dest.replace(/^Appeared here: /, ""))}</li>
    </ol>`;
}

function proofHtml(rows, open = false) {
  return `<details class="proof"${open ? " open" : ""}><summary>View proof</summary>${rows}</details>`;
}

function crossingProof(o, x, m, attempts) {
  return `
    <ul class="facts">
      <li>intrusion <b>${esc(x.intrusion_id)}</b>: event <b>${esc(o.event_id)}</b> (timeline ${esc(o.branch)}) → <b>${esc(m.event_id)}</b> (timeline ${esc(m.branch)})</li>
      <li>${esc(o.actor)} · ${esc(o.action)} · ${esc(o.target)}${o.text ? ` · "${esc(o.text)}"` : ""}</li>
      <li>proposed by ${o.source === "model" ? "the model" : "a hand-written fallback"}; committed by the engine</li>
      <li>preconditions: ${esc((o.preconditions || []).join("; ") || "none")}</li>
      <li>evidence: ${esc((o.evidence_refs || []).join(", ") || "none")}</li>
      ${o.summary && o.source === "model" ? `<li>Original model proposal, shown unmodified: "${esc(o.summary)}" <span class="muted">(the structured event, not this sentence, determines the committed world state)</span></li>` : ""}
      ${x.post_fork.map((f) => `<li>crossed: ${factLine(f)} <span class="muted">(created after the fork)</span></li>`).join("")}
      ${m.state_delta.map((f) => `<li>appeared: ${factLine(f)}</li>`).join("")}
    </ul>
    ${attemptsHtml(attempts)}`;
}

function openTrace(tr) {
  if (toggledShut("trace:" + tr.intrusion.intrusion_id)) return;
  const evidence = !$("#evidence").hidden;
  openDrawer(memoryHtml(tr.memory) + proofHtml(crossingProof(tr.origin, tr.intrusion, tr.manifestation, tr.attempts), evidence),
    tr.intrusion.origin_branch, "trace:" + tr.intrusion.intrusion_id);
}

/* Every link that opens the drawer also closes it: the same opener clicked again shuts it.
   A different opener switches the drawer to its own content. */
function toggledShut(key) {
  if (!$("#drawer").hidden && S.drawerKey === key) { closeDrawer(); return true; }
  return false;
}
function openDrawer(html, shadow = null, key = null) {
  Sound.focusShadow(shadow);
  const a = document.activeElement;
  if (a && a !== document.body && !$("#drawer").contains(a)) {
    S.drawerTrigger?.setAttribute?.("aria-expanded", "false");
    S.drawerTrigger = a;
    if (key) a.setAttribute("aria-expanded", "true");
  } else if ($("#drawer").hidden) S.drawerTrigger = a;
  S.drawerKey = key;
  document.body.classList.add("drawer-open");
  // Phones: the sheet covers the lower part of the page; keep an in-story opener visible above it,
  // so tapping it again closes the drawer.
  if (key && window.matchMedia("(max-width: 40rem)").matches && S.drawerTrigger && $("#scene").contains(S.drawerTrigger)) {
    const top = S.drawerTrigger.getBoundingClientRect().top;
    window.scrollBy({ top: top - 76, behavior: "instant" });
  }
  $("#drawer-body").innerHTML = html;
  $("#drawer").hidden = false;
  const body = $("#drawer-body");
  body.setAttribute("tabindex", "-1");
  body.focus();                                      // screen readers start at the memory, before the proof
  body.scrollTop = 0;
}
function closeDrawer() {
  Sound.focusShadow(null);
  $("#drawer").hidden = true;
  document.body.classList.remove("drawer-open");
  S.drawerKey = null;
  S.drawerTrigger?.setAttribute?.("aria-expanded", "false");
  const t = S.drawerTrigger;
  if (t && document.contains(t)) t.focus();
}
$("#drawer-close").addEventListener("click", closeDrawer);

/* ---------------------------------------------------------------- verification */
async function openVerify() {
  if (toggledShut("verify")) return;
  let d;
  try { d = await api(`/api/verify?game=${encodeURIComponent(S.game)}`); }
  catch (e) { openDrawer(`<p class="bad">${esc(e.message)}</p>`); return; }
  const rows = Object.keys(d.hashes).map((b) =>
    `<li>timeline ${esc(b)} · ${esc(d.hashes[b])} ${d.replayed[b] === d.hashes[b] ? `<span class="ok">matches</span>` : `<span class="bad">differs: ${esc(d.replayed[b])}</span>`}</li>`).join("");
  openDrawer(`<p class="kicker">verify this run</p>
    <h3>${d.verified ? "Reproduced exactly." : "Did not reproduce."}</h3>
    <p>Every model output from this night was fed back through the engine, which re-validated and re-committed every event from the start. The resulting state of every timeline was compared with the ${esc(d.against)}.</p>
    <ul class="facts">${rows}</ul>
    <p class="small muted">The same check runs on every recorded night in the test suite, and fails if a single recorded model output is changed.</p>`, null, "verify");
}

/* ---------------------------------------------------------------- map */
async function openMap() {
  if (!$("#map").hidden && $("#map").classList.contains("revealing")) return;
  try {
    S.mapData = await api(`/api/map?game=${encodeURIComponent(S.game)}`);
  } catch (e) { return; }
  S.mapTrigger = document.activeElement;
  $("#map").hidden = false;
  drawMap(S.mapData);
  if (!S.mapRevealed && !instantText()) { S.mapRevealed = true; reveal(); }
  else { S.mapRevealed = true; showAll(); }
}

/* The reveal: a line of text, then your path, each rejected branch at its fork, then the threads,
   then the first crossing is lit and explained. Committed data only; skippable; instant under
   reduced motion or "show text immediately". */
const Reveal = { timers: [] };
function showAll() {
  Reveal.timers.forEach(clearTimeout); Reveal.timers = [];
  Sound.mapStop();                                  // skipped: silent at once
  $("#map").classList.remove("revealing");
  $("#map-intro").hidden = true;
  $("#map").querySelectorAll("[data-stage]").forEach((el) => el.classList.add("on"));
  if (S.mapData && S.mapData.intrusions.length) selectCrossing(0, false);
  // Keyboard users land on the first crossing's "Follow this thread" (or the first night card on phones).
  if (!$("#map").hidden) ($("#map-detail:not([hidden]) .cta") || $("#map-list .ev-row.crossing") || $("#map-close")).focus();
}
function reveal() {
  if (REDUCED) return showAll();
  const map = $("#map");
  map.classList.add("revealing");
  map.querySelectorAll("[data-stage]").forEach((el) => el.classList.remove("on"));
  $("#map-intro").hidden = false;
  $("#map-skip").focus();
  const at = (ms, fn) => Reveal.timers.push(setTimeout(fn, ms));
  const stage = (n) => map.querySelectorAll(`[data-stage="${n}"]`).forEach((el) => el.classList.add("on"));
  at(1800, () => { $("#map-intro").hidden = true; stage(1); Sound.mapStart(); });
  at(2600, () => { stage(2); Sound.mapApart(45); });
  at(3400, () => { stage(3); Sound.mapApart(90); });
  at(4200, () => { stage(4); Sound.mapMeet(); });
  at(5400, () => { stage(5); Sound.mapMeet(); });
  at(6600, () => { Sound.mapStop(1.5); showAll(); });
}
$("#map-skip").addEventListener("click", showAll);
$("#map-graph-toggle").addEventListener("click", () => { S.mapGraph = !S.mapGraph; drawMap(S.mapData); showAll(); });
function closeMap() {
  Reveal.timers.forEach(clearTimeout); Reveal.timers = [];
  $("#map").classList.remove("revealing");
  $("#map").hidden = true;
  const t = S.mapTrigger;
  const back = t && t !== document.body && document.contains(t) ? t : $("#choices button");
  back?.focus();
}
$("#map-close").addEventListener("click", closeMap);

const ORDINAL = ["FIRST CROSSING", "SECOND CROSSING"];
function laneTitle(label) {
  if (!label) return "";
  const m = label.match(/^(Your night|The other night): (.*)$/);
  if (!m) return label.toUpperCase().replace(/, BEFORE THE CHOICE$/, "") + " — before the choice";
  const rest = m[2][0].toUpperCase() + m[2].slice(1);
  return `${m[1] === "Your night" ? "YOUR NIGHT" : "OTHER NIGHT"} — ${rest}`;
}

/* The detail card under the graph: hover previews, click/Enter/Space selects (announced). */
function detailFor(id) {
  const d = S.mapData, e = d.events.find((x) => x.event_id === id);
  const i = d.intrusions.findIndex((x) => x.origin_event_id === id || x.manifestation_event_id === id);
  if (i >= 0) return crossingDetail(i, d.intrusions[i].origin_event_id === id ? "source" : "dest");
  return `<p class="kicker">${esc(laneTitle(d.labels[e.branch]))}</p><p class="detail-title">${esc(e.desc)}</p>
    <button class="linkish" data-open="${esc(id)}">Details</button>`;
}
function crossingDetail(i, end = "source") {
  const x = S.mapData.intrusions[i], m = x.memory;
  return `<p class="kicker">${ORDINAL[i] || "CROSSING"}</p>
    <p class="detail-title"><span class="ink-text">“${esc(m.inscription)}”</span> on ${esc(m.object)}</p>
    <p class="detail-route"><b>Written there</b> ${esc(m.source.replace(/^Written there: /, ""))}<br>
    <b>Appeared here</b> ${esc(m.dest.replace(/^Appeared here: /, ""))}</p>
    <button class="cta small" data-open="${esc(end === "source" ? x.origin_event_id : x.manifestation_event_id)}">See what happened there</button>`;
}
function setDetail(html, announce) {
  const card = $("#map-detail");
  card.innerHTML = html;
  card.querySelectorAll("[data-open]").forEach((b) => b.addEventListener("click", () => openEvent(b.dataset.open)));
  if (announce) $("#map-announce").textContent = card.textContent.replace(/\s+/g, " ").trim();
}
function selectNode(id) {
  S.mapSelected = id;
  $("#map-svg").querySelectorAll(".node, .thread").forEach((n) => n.classList.remove("selected", "lit"));
  const n = $("#map-svg").querySelector(`.node[data-id="${CSS.escape(id)}"]`);
  if (n) n.classList.add("selected");
  const i = S.mapData.intrusions.findIndex((x) => x.origin_event_id === id || x.manifestation_event_id === id);
  if (i >= 0) $("#map-svg").querySelectorAll(".thread")[i]?.classList.add("lit");
  setDetail(detailFor(id), true);
}
function selectCrossing(i, announce = true) {
  const x = S.mapData.intrusions[i];
  selectNode(x.origin_event_id);
  setDetail(`<p class="kicker">${ORDINAL[i]}</p>
    <p class="detail-title"><span class="ink-text">“${esc(x.memory.inscription)}”</span> was written there. It appeared here.</p>
    <p class="detail-route"><b>Written there</b> ${esc(x.memory.source.replace(/^Written there: /, ""))}<br>
    <b>Appeared here</b> ${esc(x.memory.dest.replace(/^Appeared here: /, ""))}</p>
    <button class="cta small" data-open="${esc(x.origin_event_id)}">Follow this thread</button>
    ${S.mapData.intrusions.length > 1 ? `<button class="linkish" id="next-crossing">${ORDINAL[1 - i]?.toLowerCase() || ""} →</button>` : ""}`, announce);
  $("#next-crossing")?.addEventListener("click", () => selectCrossing(1 - i));
}

function drawMap(d) {
  const svg = $("#map-svg");
  const box = $("#map .map-scroll").clientWidth || window.innerWidth;
  const narrow = window.matchMedia("(max-width: 40rem)").matches;
  const graph = !narrow || S.mapGraph;            // phones: the card list first; the graph on request
  svg.toggleAttribute("hidden", !graph);
  $("#map-list").hidden = graph;
  $("#map-detail").hidden = !graph;
  $("#map-graph-toggle").hidden = !narrow;
  $("#map-graph-toggle").textContent = S.mapGraph ? "View as a list" : "View as a graph";
  $("#map .map-scroll").classList.toggle("wide", narrow && graph);
  drawEventList(d);
  if (!graph) return drawMapList(d);
  const evidence = !$("#evidence").hidden;
  const br = Object.fromEntries(d.branches.map((b) => [b.id, b]));
  // Causal x order: shadows start right after their fork; a crossing lands after its origin.
  const col = {};
  const byBranch = (id) => d.events.filter((e) => e.branch === id).sort((a, b) => a.seq - b.seq);
  function lastColAt(parent, forkSeq) {
    const evs = d.events.filter((e) => [parent, br[parent].parent].includes(e.branch) && e.seq < forkSeq);
    return Math.max(-1, ...evs.map((e) => col[e.event_id] ?? -1));
  }
  for (const id of ["S", "B", "A", "D", "C"]) {
    const b = br[id];
    if (!b) continue;
    let c = b.parent ? lastColAt(b.parent, b.fork_seq) : -1;
    for (const e of byBranch(id)) {
      c += 1;
      if (e.origin && col[e.origin] !== undefined) c = Math.max(c, col[e.origin] + 1);
      col[e.event_id] = c;
    }
  }
  S.mapCol = col;
  const maxSeq = Math.max(...Object.values(col));
  const lane = { B: 0.17, S: 0.5, A: 0.5, C: 0.5, D: 0.83 };
  // Fit the graph to the space it has: full width, height from the viewport, so the overlay is the
  // only scroll region (phones asking for the graph get a wide, horizontally scrolling view instead).
  const W = narrow ? Math.max(720, maxSeq * 64 + 180) : box;
  const step = (W - 180) / Math.max(maxSeq, 1);
  const H = Math.max(320, Math.min(window.innerHeight - 340, 560));
  const P = (seq, b) => [90 + seq * step, H * lane[b]];
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("width", W); svg.setAttribute("height", H);
  const pos = {};
  d.events.forEach((e) => (pos[e.event_id] = P(col[e.event_id], e.branch)));
  const curve = ([x1, y1], [x2, y2]) => `M${x1},${y1} C${(x1 + x2) / 2},${y1} ${(x1 + x2) / 2},${y2} ${x2},${y2}`;
  let out = "";
  const stageOf = { S: 1, A: 1, C: 1, B: 2, D: 3 };
  const forkAt = Object.fromEntries((d.forks || []).map((f) => [f.at === "S" ? "B" : "D", f]));
  for (const b of d.branches) {
    const evs = d.events.filter((e) => e.branch === b.id);
    if (!evs.length) continue;
    const cls = br[b.id].kind === "shadow" ? "lane shadow" : "lane";
    out += `<g data-stage="${stageOf[b.id]}">`;
    const first = pos[evs[0].event_id], last = pos[evs[evs.length - 1].event_id];
    if (b.parent) out += `<path class="${cls}" d="${curve(P(lastColAt(b.parent, b.fork_seq), b.parent), first)}"/>`;
    out += `<path class="${cls}" d="M${first[0]},${first[1]} L${last[0]},${last[1]}"/>`;
    const dy = b.id === "B" ? -30 : b.id === "D" ? 42 : b.id === "A" ? 64 : 42;
    out += `<text class="lane-label${br[b.id].kind === "shadow" ? " other" : ""}" x="${first[0] - 10}" y="${first[1] + dy}">${esc(laneTitle(d.labels[b.id]))}</text>`;
    const f = forkAt[b.id];
    if (f) {
      const [fx, fy] = P(lastColAt(b.parent, b.fork_seq), b.parent);
      out += `<text class="fork-label" x="${fx}" y="${fy + (b.id === "B" ? -18 : 26)}" text-anchor="middle">“${esc(f.not_taken)}”</text>`;
    }
    out += `</g>`;
  }
  d.intrusions.forEach((x, i) => {
    const [a, b] = [pos[x.origin_event_id], pos[x.manifestation_event_id]];
    out += `<g data-stage="${4 + i}" class="thread-g"><path class="thread" pathLength="1" d="${curve(a, b)}"/>` +
      ((a[0] + b[0]) / 2 > W - 180
        ? `<text class="cross-label" x="${(a[0] + b[0]) / 2 - 14}" y="${(a[1] + b[1]) / 2}" text-anchor="end">${ORDINAL[i] || ""}</text></g>`
        : `<text class="cross-label" x="${(a[0] + b[0]) / 2 + 12}" y="${(a[1] + b[1]) / 2}">${ORDINAL[i] || ""}</text></g>`);
  });
  const anchors = new Set(d.intrusions.flatMap((x) => [x.origin_event_id, x.manifestation_event_id]));
  for (const e of d.events) {
    const [x, y] = pos[e.event_id];
    const kind = br[e.branch].kind === "shadow" ? "shadow" : "canonical";
    const anchor = anchors.has(e.event_id);
    const cls = ["node", kind, e.source, anchor ? "anchor" : ""].join(" ");
    const r = anchor ? 8 : e.source === "story" ? 4 : 5.5;
    const xi = d.intrusions.findIndex((i) => i.origin_event_id === e.event_id || i.manifestation_event_id === e.event_id);
    const role = xi >= 0 ? ` ${ORDINAL[xi].toLowerCase()}: “${d.intrusions[xi].memory.inscription}” ${d.intrusions[xi].origin_event_id === e.event_id ? "was written here" : "appeared here"}.` : "";
    const name = `${laneTitle(d.labels[e.branch])}. ${e.desc}${role}`;
    out += `<g class="${cls}" data-stage="${stageOf[e.branch]}" tabindex="0" role="button" aria-label="${esc(name)}" data-id="${esc(e.event_id)}">` +
      `<circle cx="${x}" cy="${y}" r="20" class="hit"/><circle cx="${x}" cy="${y}" r="${r}" class="dot"/>` +
      (evidence ? `<text x="${x}" y="${y + 30}" text-anchor="middle" class="eid">${esc(e.event_id)}</text>` : "") + `</g>`;
  }
  svg.innerHTML = out;
  svg.querySelectorAll(".node").forEach((n) => {
    const id = n.dataset.id;
    n.addEventListener("mouseenter", () => { if (S.mapSelected !== id) setDetail(detailFor(id), false); });
    n.addEventListener("mouseleave", () => { if (S.mapSelected && S.mapSelected !== id) setDetail(detailFor(S.mapSelected), false); });
    n.addEventListener("click", () => selectNode(id));
    n.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); selectNode(id); $("#map-detail [data-open]")?.focus(); }
    });
  });
  // A redraw (resize, graph/list toggle) keeps the current selection lit.
  if (S.mapSelected) {
    svg.querySelector(`.node[data-id="${CSS.escape(S.mapSelected)}"]`)?.classList.add("selected");
    const i = d.intrusions.findIndex((x) => x.origin_event_id === S.mapSelected || x.manifestation_event_id === S.mapSelected);
    if (i >= 0) svg.querySelectorAll(".thread")[i]?.classList.add("lit");
  }
}

/* Screen readers: the same night as a chronological list. */
function drawEventList(d) {
  const order = [...d.events].sort((a, b) => (S.mapCol?.[a.event_id] ?? a.seq) - (S.mapCol?.[b.event_id] ?? b.seq) || a.branch.localeCompare(b.branch));
  $("#map-events").innerHTML = `<h3>Every event, in order</h3><ol>${order.map((e) => {
    const i = d.intrusions.findIndex((x) => x.origin_event_id === e.event_id || x.manifestation_event_id === e.event_id);
    const role = i >= 0 ? ` (${ORDINAL[i].toLowerCase()}, ${d.intrusions[i].origin_event_id === e.event_id ? "written there" : "appeared here"})` : "";
    return `<li><button data-open="${esc(e.event_id)}">${esc(laneTitle(d.labels[e.branch]))}: ${esc(e.desc)}${role}</button></li>`;
  }).join("")}</ol>`;
  $("#map-events").querySelectorAll("[data-open]").forEach((b) => b.addEventListener("click", () => openEvent(b.dataset.open)));
}

/* Phones: the same data as a vertical list of nights, linked by crossing markers. */
function drawMapList(d) {
  const by = (id) => d.events.filter((e) => e.branch === id && e.source !== "story" || (e.branch === id && e.source === "story" && e.seq === 0));
  const forks = Object.fromEntries((d.forks || []).map((f) => [f.at, f]));
  const evBtn = (e) => {
    const xo = d.intrusions.find((i) => i.origin_event_id === e.event_id);
    const xm = d.intrusions.find((i) => i.manifestation_event_id === e.event_id);
    const xi = d.intrusions.findIndex((i) => i.origin_event_id === e.event_id || i.manifestation_event_id === e.event_id);
    const mark = xo ? `<span class="xmark">${ORDINAL[xi]} \u00b7 written there \u2198</span>` : xm ? `<span class="xmark">${ORDINAL[xi]} \u00b7 appeared here</span>` : "";
    return `<li><button class="ev-row${xo || xm ? " crossing" : ""}" data-id="${esc(e.event_id)}" data-stage="1">${esc(e.desc)}${mark}</button></li>`;
  };
  const card = (id, title, cls) => {
    const evs = d.events.filter((e) => e.branch === id && (e.source !== "story" || id === "S"));
    if (!evs.length) return "";
    return `<section class="night-card ${cls}" data-stage="1"><h3>${esc(title)}</h3><ol>${evs.map(evBtn).join("")}</ol></section>`;
  };
  const fork = (f) => f ? `<p class="fork-note" data-stage="1">You chose \u201c${esc(f.taken)}\u201d. Somewhere else, someone chose \u201c${esc(f.not_taken)}\u201d.</p>` : "";
  const L = d.labels;
  $("#map-list").innerHTML = card("S", "Before the choice", "") + fork(forks.S) +
    card("B", L.B || "The other night", "other") + card("A", L.A || "Your night", "yours") + fork(forks.A) +
    card("D", L.D || "The other night", "other") + card("C", L.C || "Your night", "yours");
  $("#map-list").querySelectorAll(".ev-row").forEach((b) => b.addEventListener("click", () => openEvent(b.dataset.id)));
}

function openEvent(id) {
  if (toggledShut("event:" + id)) return;
  const d = S.mapData, e = d.events.find((x) => x.event_id === id);
  const x = d.intrusions.find((i) => i.manifestation_event_id === id || i.origin_event_id === id);
  const evidence = !$("#evidence").hidden;
  const raw = `<ul class="facts">
      <li>event <b>${esc(e.event_id)}</b> · ${esc({ model: "proposed by the model, validated, committed", fallback: "hand-written fallback after rejected model proposals", story: "hand-written story event", intrusion: "a crossing from another timeline" }[e.source])}</li>
      ${e.summary && e.source === "model" ? `<li>Original model proposal, shown unmodified: "${esc(e.summary)}" <span class="muted">(the structured event, not this sentence, determines the committed world state)</span></li>` : ""}
      ${e.preconditions.length ? `<li>preconditions: ${esc(e.preconditions.join("; "))}</li>` : ""}
      ${e.evidence_refs.length ? `<li>evidence: ${esc(e.evidence_refs.join(", "))}</li>` : ""}
      ${e.state_delta.map((f) => `<li>${factLine(f)}</li>`).join("")}
    </ul>${attemptsHtml(e.attempts)}`;
  if (x) {
    const o = d.events.find((y) => y.event_id === x.origin_event_id);
    const m = d.events.find((y) => y.event_id === x.manifestation_event_id);
    openDrawer(memoryHtml(x.memory, x.origin_event_id === id ? "the other night" : "your night") +
      proofHtml(crossingProof(o, x, m, o.attempts), evidence), x.origin_branch, "event:" + id);
    return;
  }
  openDrawer(`<p class="kicker">${esc(d.labels[e.branch] || e.branch)}</p>
    <div class="quote ${e.branch === "B" || e.branch === "D" ? "shadowq" : ""}">${esc(e.desc)}</div>` + proofHtml(raw, evidence), null, "event:" + id);
}

/* ---------------------------------------------------------------- evidence */
async function loadEvidence() {
  if (!S.game) { $("#evidence-body").innerHTML = `<p class="small muted">Start a night first.</p>`; return; }
  const d = await api(`/api/evidence?game=${encodeURIComponent(S.game)}`);
  const crossed = d.crossings || [];
  const summary = crossed.length
    ? `<div class="ev-summary"><p><b>${crossed.length === 1 ? "One thing has" : "Two things"} crossed into your night.</b></p><ol>${crossed.map((c) =>
        `<li>\u201c${esc(c.inscription)}\u201d on ${esc(c.object)}, from ${esc(c.source.replace(/^Written there: /, "").split(",")[0])}.</li>`).join("")}</ol></div>`
    : `<div class="ev-summary"><p><b>Nothing has crossed into your night yet.</b></p></div>`;
  const row = (r) => {
    if (r.kind === "proposal") {
      const dec = r.decision || {};
      const verdict = dec.accepted ? `<span class="ok">COMMITTED ${esc(r.event_id || "")}</span>` : `<span class="bad">REJECTED</span> ${esc((dec.violations || []).map((v) => v.rule + ": " + v.detail).join("; "))}`;
      return `<div class="ev"><div class="head">step ${r.step + 1} · attempt ${r.attempt + 1} · prompt ${esc(r.prompt_hash)} · ${((r.meta || {}).latency_s || 0).toFixed(2)} s</div>${verdict}<pre>${esc(JSON.stringify(r.proposal))}</pre></div>`;
    }
    if (r.kind === "fallback") return `<div class="ev"><div class="head">step ${r.step + 1}</div><span class="fb">FALLBACK COMMITTED ${esc(r.event_id)}</span><pre>${esc(JSON.stringify(r.proposal))}</pre></div>`;
    if (r.kind === "narration") return `<div class="ev"><div class="head">crossing ${esc(r.intrusion)} · attempt ${r.attempt + 1}</div>${r.problems.length ? `<span class="bad">FAILED CHECKS</span> ${esc(r.problems.join("; "))}` : `<span class="ok">PASSED CHECKS</span>`}<pre>${esc(r.raw ?? r.text)}</pre></div>`;
    if (r.kind === "narration_fallback") return `<div class="ev"><div class="head">crossing ${esc(r.intrusion)}</div><span class="fb">NO MODEL SENTENCE</span><pre>${esc(r.text)}</pre></div>`;
    return `<div class="ev"><div class="head">${esc(r.kind)}</div><pre>${esc(JSON.stringify(r))}</pre></div>`;
  };
  // Collapsible groups: one per other night, plus the crossing-text checks. Open state survives refreshes.
  const groups = [
    ["B", "The other night after the door", (r) => r.branch === "B" && r.kind !== "narration" && r.kind !== "narration_fallback"],
    ["D", "The other night after the stairs", (r) => r.branch === "D" && r.kind !== "narration" && r.kind !== "narration_fallback"],
    ["N", "Crossing text checks", (r) => r.kind === "narration" || r.kind === "narration_fallback"],
  ];
  S.evOpen ||= new Set();
  const body = groups.map(([key, title, pick]) => {
    const rs = d.log.filter(pick);
    if (!rs.length) return "";
    const ok = rs.filter((r) => r.kind === "proposal" && r.decision?.accepted).length;
    const bad = rs.filter((r) => r.kind === "proposal" && !r.decision?.accepted).length;
    const fb = rs.filter((r) => r.kind === "fallback").length;
    const counts = key === "N" ? `${rs.length} records` : `${ok} committed · ${bad} rejected${fb ? ` · ${fb} fallback` : ""}`;
    return `<details class="ev-group" data-key="${key}"${S.evOpen.has(key) ? " open" : ""}>
      <summary><span>${esc(title)}</span><span class="ev-counts">${counts}</span></summary>${rs.map(row).join("")}</details>`;
  }).join("");
  $("#evidence-body").innerHTML = summary + `<p class="small muted">model ${esc(d.model.model)} ${esc(d.model.quantization || "")} · seed ${esc(d.seed)} · ${d.log.length} records</p>` + body;
  $("#evidence-body").querySelectorAll(".ev-group").forEach((g) => g.addEventListener("toggle", () => {
    if (g.open) S.evOpen.add(g.dataset.key); else S.evOpen.delete(g.dataset.key);
  }));
}
function toggleEvidence(force) {
  const el = $("#evidence");
  const open = typeof force === "boolean" ? force : el.hidden;
  el.hidden = !open;
  const b = $("#evidence-toggle");
  b.setAttribute("aria-pressed", String(open));
  b.setAttribute("aria-expanded", String(open));
  b.textContent = open ? "hide evidence" : "evidence";
  if (!$("#map").hidden && S.mapData) drawMap(S.mapData);
  if (open) loadEvidence();
}
$("#evidence-toggle").addEventListener("click", () => toggleEvidence());
$("#evidence-close").addEventListener("click", () => { toggleEvidence(false); $("#evidence-toggle").focus(); });
$("#evidence-title").addEventListener("click", () => { toggleEvidence(false); $("#evidence-toggle").focus(); });
$("#sound-toggle").addEventListener("click", () => { Sound.init(); Sound.toggle(); });
$("#sound-toggle").setAttribute("aria-pressed", String(Sound.on));
$("#instant-toggle").addEventListener("click", () => { INSTANT = !INSTANT; Prefs.set("instant", INSTANT ? "1" : "0"); applyInstant(); });
/* Settings popover: reading and sound preferences, kept out of the header's way. */
function setSettings(open) {
  $("#settings").hidden = !open;
  $("#settings-toggle").setAttribute("aria-expanded", String(open));
  if (open) $("#settings button").focus();
}
$("#settings-toggle").addEventListener("click", () => setSettings($("#settings").hidden));
document.addEventListener("click", (ev) => {
  if (!$("#settings").hidden && !ev.target.closest("#settings, #settings-toggle")) setSettings(false);
});
document.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape" && !$("#settings").hidden) { ev.stopImmediatePropagation(); setSettings(false); $("#settings-toggle").focus(); }
}, true);
applyInstant();
document.addEventListener("keydown", (ev) => {
  if (ev.target.tagName === "INPUT") return;
  if (ev.key === "e" || ev.key === "E") toggleEvidence();
  if (ev.key === "Escape") {
    if (!$("#drawer").hidden) closeDrawer();
    else if (!$("#evidence").hidden) toggleEvidence();
    else if (!$("#map").hidden) { if ($("#map").classList.contains("revealing")) showAll(); else closeMap(); }
  }
});

window.addEventListener("resize", () => { if (!$("#map").hidden && S.mapData) drawMap(S.mapData); });

boot().then(() => {
  const rid = new URLSearchParams(location.search).get("replay");
  if (rid) startReplay(rid).catch((e) => ($("#status-line").textContent = e.message));
});
