"use strict";

/* ---------------------------------------------------------------- sound */
/* Synthesized sound. Presentation only: cues follow committed state (choice ids, scene fields,
   crossing records) and never touch the engine or its logs. One master gain into a limiter;
   every method fails silently.

   Two kinds of sound. One-shot cues (knock, bolt, pen, crossings) play only while sound is on.
   Lasting state (is the power on, which other night is in focus, which ending, which scene) lives
   in `want`; setting it always updates `want`, muted or not, and `apply()` makes the layers match.
   Muting only zeroes the master gain, so unmuting simply lets the current state be heard. */
const Sound = {
  ctx: null, master: null, noise: null, layers: {}, live: new Set(), mapVoices: null, pens: new Set(),
  mix: { restore: null, power: null },          // one pending room restore; a new cue cancels it
  want: { power: true, focused: null, ending: null, scene: null },
  on: (() => { try { return localStorage.getItem("knock.sound") !== "0"; } catch { return true; } })(),
  ROOM: 0.035, HUM: 0.007, ROOM_LP: 6000,       // ROOM_LP: the room's filter wide open; a duck closes it

  guard(fn) { try { if (this.ctx && this.on) fn.call(this, this.ctx, this.ctx.currentTime); } catch { /* silent */ } },

  /* Make the layers match `want`. Runs muted or not; safe to call any time. */
  apply(tau = 0.5) { this.applyPower(); this.applyMix(tau); },
  applyPower() {
    try {
      const c = this.ctx, room = this.layers.room; if (!c || !room) return;
      if (this.want.power) this.set(room.hum.gain, this.HUM, c, 0.15);
      else { room.hum.gain.cancelScheduledValues(c.currentTime); room.hum.gain.setValueAtTime(0.0001, c.currentTime); }
    } catch { /* silent */ }
  },
  applyMix(tau = 0.5) {
    try {
      const c = this.ctx, room = this.layers.room, w = this.want; if (!c) return;
      if (room) {
        clearTimeout(this.mix.restore); this.mix.restore = null;
        this.set(room.gain.gain, w.focused ? this.ROOM * 0.35 : this.ROOM, c, tau);
        this.set(room.lp.frequency, this.ROOM_LP, c, tau);
      }
      for (const id of ["B", "D"]) {
        const level = w.focused === id ? 0.045 : w.ending === "open" ? 0.012 : 0;
        if (level) {
          const L = this.shadowLayer(id); clearTimeout(L.release); clearTimeout(L.bleed); L.bleeding = false;
          this.set(L.gain.gain, level, c, tau);
        } else if (this.layers[id] && !this.layers[id].bleeding) {
          this.set(this.layers[id].gain.gain, 0.0001, c, tau); this.releaseLayer(id, 2.5);
        }
      }
    } catch { /* silent */ }
  },

  init() {
    try {
      if (!this.ctx) {
        // iOS 17+: play as media, so the ring/silent switch doesn't silence the night.
        try { if (navigator.audioSession) navigator.audioSession.type = "playback"; } catch { /* older browsers */ }
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
      if (!this.layers.room) { this.startRoom(); this.apply(1.2); }
    } catch { /* no audio: the story works without it */ }
  },
  resume() {
    try {
      if (this.ctx && this.ctx.state === "suspended") Promise.resolve(this.ctx.resume()).then(() => this.apply(0.3), () => {});
    } catch { /* silent */ }
  },

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
  duck({ gain = 1, lp = this.ROOM_LP }, holdMs, restoreTau = 1.5, attackTau = 0.3) {
    const c = this.ctx, room = this.layers.room;
    if (!room) return;
    clearTimeout(this.mix.restore);
    this.set(room.gain.gain, this.ROOM * gain, c, attackTau);
    this.set(room.lp.frequency, lp, c, attackTau);
    this.mix.restore = setTimeout(() => {
      this.mix.restore = null;
      try { this.set(room.gain.gain, this.want.focused ? this.ROOM * 0.35 : this.ROOM, c, restoreTau); this.set(room.lp.frequency, this.ROOM_LP, c, restoreTau); } catch { /* silent */ }
    }, holdMs);
  },

  /* ---- layers: room tone (rain + mains hum), and one abstract texture per shadow night */
  /* Rain on the window, from inside: a soft wash that breathes with the wind, many small ticks on
     the glass at random moments (each a different pitch, weight and place), and now and then a
     heavier drip from the sill. Drops are scheduled a quarter second ahead, a few at a time. */
  startRoom() {
    const c = this.ctx;
    const rg = c.createGain(); rg.gain.value = 0.0001;
    const lp = c.createBiquadFilter(); lp.type = "lowpass"; lp.frequency.value = this.ROOM_LP;
    rg.connect(lp).connect(this.master);
    const bed = this.noiseSrc(c);
    const hp = c.createBiquadFilter(); hp.type = "highpass"; hp.frequency.value = 350;
    const soft = c.createBiquadFilter(); soft.type = "lowpass"; soft.frequency.value = 2400;
    const bg = c.createGain(); bg.gain.value = 0.12;
    bed.connect(hp).connect(soft).connect(bg).connect(rg);
    const wind = c.createOscillator(); wind.frequency.value = 0.06;          // a slow swell, about every 17 s
    const wd = c.createGain(); wd.gain.value = 0.06; wind.connect(wd).connect(bg.gain); wind.start();
    const drops = c.createGain(); drops.connect(rg);
    const hg = c.createGain(); hg.gain.value = 0.0001;
    const h1 = c.createOscillator(); h1.frequency.value = 60;
    const h2 = c.createOscillator(); h2.frequency.value = 120;
    const h2g = c.createGain(); h2g.gain.value = 0.45;
    h1.connect(hg); h2.connect(h2g).connect(hg); hg.connect(this.master);
    h1.start(); h2.start();
    const room = { srcs: [bed, wind, h1, h2], gain: rg, lp, hum: hg, drops, tick: 0, drip: 0 };
    room.timer = setInterval(() => this.rain(), 100);
    this.layers.room = room;
  },
  rain() {
    try {
      const c = this.ctx, room = this.layers.room;
      if (!c || !room || !this.on || c.state !== "running") return;     // muted or suspended: nothing to schedule
      const now = c.currentTime, ahead = now + 0.25;
      room.tick = Math.max(room.tick, now);
      while (room.tick < ahead) {                                       // ~26 ticks a second, at random
        this.drop(c, room.drops, room.tick, false);
        room.tick += -Math.log(1 - Math.random()) / 26;
      }
      room.drip = Math.max(room.drip, now + 0.1);
      while (room.drip < ahead) {                                       // a drip every half second to two seconds
        this.drop(c, room.drops, room.drip, true);
        room.drip += 0.45 + Math.random() * 1.5;
      }
    } catch { /* silent */ }
  },
  drop(c, bus, t, heavy) {
    let out = bus;
    if (c.createStereoPanner) {
      out = c.createStereoPanner();
      out.pan.value = heavy ? -0.45 + Math.random() * 0.25 : Math.random() * 1.6 - 0.8;
      out.connect(bus);
    }
    const s = c.createBufferSource(); s.buffer = this.noise;
    const f = c.createBiquadFilter(); f.type = "bandpass";
    if (!heavy) {                                                       // a tick on the glass: a few ms, never the same twice
      const dur = 0.003 + Math.random() * 0.01;
      f.frequency.value = 2200 + Math.random() * 4500; f.Q.value = 1.5 + Math.random() * 6;
      s.connect(f).connect(this.env(c, t, 0.35 + Math.random() ** 3 * 2.2, 0.001, 0, dur)).connect(out);
      s.start(t, Math.random() * 1.8); this.track(s, t + dur + 0.03);
      return;
    }
    // a drip from the sill: a soft hit, then a short rising "plip" as the water closes
    f.frequency.value = 1100 + Math.random() * 600; f.Q.value = 1.2;
    s.connect(f).connect(this.env(c, t, 1.4, 0.001, 0, 0.012)).connect(out);
    s.start(t, Math.random() * 1.8); this.track(s, t + 0.05);
    const o = c.createOscillator(), f0 = 520 + Math.random() * 480;
    o.frequency.setValueAtTime(f0, t + 0.004); o.frequency.exponentialRampToValueAtTime(f0 * 2.3, t + 0.04);
    o.connect(this.env(c, t + 0.004, 0.9 + Math.random() * 0.5, 0.002, 0.004, 0.045)).connect(out);
    o.start(t + 0.004); this.track(o, t + 0.1);
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
      if (this.want.focused === id) return;      // already in focus: louder than a bleed
      const L = this.shadowLayer(id); clearTimeout(L.release); clearTimeout(L.bleed);
      L.bleeding = true;
      this.set(L.gain.gain, level * 0.14, c, 0.6);
      L.bleed = setTimeout(() => { L.bleeding = false; this.applyMix(0.8); }, seconds * 1000);
    });
  },
  focusShadow(id) {                             // a source memory is open: that night forward, ours back
    if ((id || null) === this.want.focused) return;
    this.want.focused = id || null;
    this.applyMix(id ? 0.5 : 0.8);
  },
  scene(name) {                                 // a scene change never carries the pen with it
    this.want.scene = name || null;
    this.stopPen();
  },

  /* ---- story cues */
  /* Three knuckle knocks on a wooden door, heard from inside the hall: a low panel thump that
     drops in pitch, the wood's own resonances, a dull click, and a short hallway echo. */
  knock() {
    this.guard((c, now) => {
      const hall = this.hall(c);
      let t = now + 0.3;
      for (let i = 0; i < 3; i++) {
        this.knuckle(c, t, 1 + (Math.random() - 0.5) * 0.08, hall);
        t += 0.31 + Math.random() * 0.05;
      }
    });
  },
  knuckle(c, t, v, hall) {
    const out = c.createGain(); out.connect(this.master); if (hall) out.connect(hall);
    const o = c.createOscillator();
    o.frequency.setValueAtTime(140 * v, t); o.frequency.exponentialRampToValueAtTime(62 * v, t + 0.09);
    o.connect(this.env(c, t, 0.75 * v, 0.002, 0.01, 0.17)).connect(out);
    o.start(t); this.track(o, t + 0.3);
    for (const [freq, q, peak, dur] of [[180, 3, 2.4, 0.13], [380, 6, 1.6, 0.1], [760, 4, 0.5, 0.04], [2600, 1, 0.12, 0.008]]) {
      const s = c.createBufferSource(); s.buffer = this.noise;
      const f = c.createBiquadFilter(); f.type = "bandpass"; f.frequency.value = freq * v; f.Q.value = q;
      s.connect(f).connect(this.env(c, t, peak * v, 0.001, 0, dur)).connect(out);
      s.start(t, Math.random() * 1.8); this.track(s, t + dur + 0.05);
    }
  },
  hall(c) {                                     // a short, dark hallway echo, made once
    try {
      if (!this.verb && c.createConvolver) {
        const len = Math.floor(c.sampleRate * 0.7), ir = c.createBuffer(2, len, c.sampleRate);
        for (let ch = 0; ch < 2; ch++) {
          const d = ir.getChannelData(ch); let lowp = 0;
          for (let i = 0; i < len; i++) { lowp += 0.25 * ((Math.random() * 2 - 1) - lowp); d[i] = lowp * Math.exp(-i / (c.sampleRate * 0.14)); }
        }
        const conv = c.createConvolver(); conv.buffer = ir;
        const wet = c.createGain(); wet.gain.value = 0.5;
        conv.connect(wet).connect(this.master);
        this.verb = conv;
      }
    } catch { this.verb = null; }
    return this.verb;
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
      this.duck({ gain: 1.4, lp: 12000 }, 5000, 2, 0.6);
    });
  },
  power(on) {                                   // scene field power = "out" cuts the hum at once
    clearTimeout(this.mix.power); this.mix.power = null;
    this.want.power = Boolean(on);
    this.applyPower();
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
  /* erase: the other night fades out entirely, then the power and the room return.
     open: both nights stay, quietly mixed. */
  ending(which) {
    this.want.ending = which;
    this.want.focused = null;
    Object.values(this.layers).forEach((L) => { if (L.bleeding) { clearTimeout(L.bleed); L.bleeding = false; } });
    this.applyMix(which === "erase" ? 0.6 : 2);
    if (which === "erase") {
      clearTimeout(this.mix.power);
      this.mix.power = setTimeout(() => this.power(true), 1800);
    }
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
    else { this.resume(); this.apply(0.3); }                      // unmute: the latest state, not a stale one
    try { document.querySelector("#sound-toggle")?.setAttribute("aria-pressed", String(this.on)); } catch { /* no page */ }
  },
  reset() {                                     // a new night: stop and release everything
    this.mapStop(); this.stopPen();
    clearTimeout(this.mix.restore); this.mix.restore = null;
    clearTimeout(this.mix.power); this.mix.power = null;
    try { this.live.forEach((n) => { try { n.stop(); n.disconnect(); } catch { /* already stopped */ } }); } catch { /* silent */ }
    this.live.clear();
    Object.values(this.layers).forEach((L) => { clearTimeout(L.release); clearTimeout(L.bleed); clearInterval(L.timer); try { L.srcs?.forEach((x) => { x.stop(); x.disconnect(); }); } catch { /* silent */ } });
    this.layers = {};
    this.want = { power: true, focused: null, ending: null, scene: null };
  },
};
if (typeof document !== "undefined") {
  ["pointerdown", "keydown", "touchstart"].forEach((ev) => document.addEventListener(ev, () => Sound.resume(), { passive: true }));
}
if (typeof module !== "undefined" && module.exports) module.exports = { Sound };
