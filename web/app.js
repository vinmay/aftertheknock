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
const Sound = {
  ctx: null, on: true, rain: null,
  init() {
    if (this.ctx) return;
    try { this.ctx = new (window.AudioContext || window.webkitAudioContext)(); } catch { return; }
    const c = this.ctx, len = c.sampleRate * 2, buf = c.createBuffer(1, len, c.sampleRate), d = buf.getChannelData(0);
    for (let i = 0; i < len; i++) d[i] = Math.random() * 2 - 1;
    const src = c.createBufferSource(); src.buffer = buf; src.loop = true;
    const bp = c.createBiquadFilter(); bp.type = "lowpass"; bp.frequency.value = 900;
    const g = c.createGain(); g.gain.value = 0;
    src.connect(bp).connect(g).connect(c.destination); src.start();
    this.rain = g; this.noise = buf; this.setRain(0.035);
  },
  setRain(v) { if (this.rain) this.rain.gain.setTargetAtTime(this.on ? v : 0, this.ctx.currentTime, 1.5); },
  thump(t, f = 68) {
    const c = this.ctx, o = c.createOscillator(), g = c.createGain();
    o.frequency.setValueAtTime(f, t); o.frequency.exponentialRampToValueAtTime(40, t + 0.25);
    g.gain.setValueAtTime(0.0001, t); g.gain.exponentialRampToValueAtTime(0.5, t + 0.01); g.gain.exponentialRampToValueAtTime(0.0001, t + 0.3);
    o.connect(g).connect(c.destination); o.start(t); o.stop(t + 0.32);
  },
  knock() { if (!this.ctx || !this.on) return; const t = this.ctx.currentTime + 0.3; [0, 0.38, 0.76].forEach((d) => this.thump(t + d)); },
  crossing() {
    if (!this.ctx || !this.on) return;
    const c = this.ctx, t = c.currentTime, g = c.createGain();
    g.gain.setValueAtTime(0.0001, t); g.gain.exponentialRampToValueAtTime(0.12, t + 1.2); g.gain.exponentialRampToValueAtTime(0.0001, t + 4);
    g.connect(c.destination);
    [110, 116.5, 164.8].forEach((f) => { const o = c.createOscillator(); o.type = "sine"; o.frequency.value = f; o.connect(g); o.start(t); o.stop(t + 4.1); });
    this.setRain(0.005); setTimeout(() => this.setRain(0.035), 3500);
  },
  tick() {
    if (!this.ctx || !this.on) return;
    const c = this.ctx, t = c.currentTime, o = c.createOscillator(), g = c.createGain();
    o.frequency.value = 1800; g.gain.setValueAtTime(0.02, t); g.gain.exponentialRampToValueAtTime(0.0001, t + 0.05);
    o.connect(g).connect(c.destination); o.start(t); o.stop(t + 0.06);
  },
  pen(n) {
    if (!this.ctx || !this.on) return;
    const c = this.ctx, t0 = c.currentTime + 0.5;
    for (let i = 0; i < n * 3; i++) {
      const t = t0 + i * 0.09 + Math.random() * 0.03;
      const src = c.createBufferSource(); src.buffer = this.noise;
      const hp = c.createBiquadFilter(); hp.type = "bandpass"; hp.frequency.value = 2500 + Math.random() * 2500; hp.Q.value = 3;
      const g = c.createGain(); g.gain.setValueAtTime(0.0001, t); g.gain.exponentialRampToValueAtTime(0.09, t + 0.01); g.gain.exponentialRampToValueAtTime(0.0001, t + 0.07);
      src.connect(hp).connect(g).connect(c.destination); src.start(t, Math.random()); src.stop(t + 0.08);
    }
    this.setRain(0.01); setTimeout(() => this.setRain(0.035), 3500);
  },
  toggle() { this.on = !this.on; this.setRain(0.035); $("#sound-toggle").setAttribute("aria-pressed", String(this.on)); },
};

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
    <p class="small">Code, tests and evaluation: <a href="${LINKS.repo}">${LINKS.repo.replace("https://", "")}</a>${st.static ? "" : ` · Replays: <a href="${LINKS.replay}">${LINKS.replay.replace("https://", "")}</a>`}</p>`);
}
$("#how").addEventListener("click", howItWorks);
["mouseenter", "focus"].forEach((ev) => $("#begin").addEventListener(ev, () => $(".door")?.classList.add("lit")));
["mouseleave", "blur"].forEach((ev) => $("#begin").addEventListener(ev, () => $(".door")?.classList.remove("lit")));

$("#start").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  if (S.busy) return;
  Sound.init();
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
  Sound.init();
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
  S.view = v;
  $("#title").hidden = true;
  $("#scene").hidden = false;
  $("#scene").className = "screen kind-" + (v.kind || "scene");
  setClock(v.clock);
  if (v.power === "out" && !instantText()) {
    document.body.classList.add("flicker");
    setTimeout(() => document.body.classList.remove("flicker"), 1000);
  }
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
  if (v.kind === "map") setTimeout(openMap, 900);
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
    const d = await api("/api/choose", { game: S.game, index: i });
    clearTimeout(wait);
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
    if (b && !b.disabled) { ev.preventDefault(); b.click(); }
  } else if (ev.key === "Enter" && buttons.length === 1 && document.activeElement === document.body) {
    ev.preventDefault(); buttons[0].click();
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
function propSvg(c) {
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
  if (second) Sound.crossing();
  Sound.pen(c.inscription.length);
  const close = () => { el.hidden = true; document.body.classList.remove("staged"); $("#current .ink")?.focus(); };
  $("#stage-go").addEventListener("click", close);
  const ready = () => { el.classList.add("ready"); $("#stage-go")?.focus(); };
  if (instantText()) ready();
  else {
    const t = setTimeout(ready, 3100);              // the mark has finished writing itself
    el.addEventListener("click", (ev) => {           // click anywhere: finish now
      if (ev.target.closest("#stage-go")) return;
      clearTimeout(t); el.classList.add("instant"); ready();
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
  return `<h3>Validator</h3><ul class="attempts">` + attempts.map((a) => {
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
      ${o.summary && o.source === "model" ? `<li>the model's own summary: "${esc(o.summary)}"</li>` : ""}
      ${x.post_fork.map((f) => `<li>crossed: ${factLine(f)} <span class="muted">(created after the fork)</span></li>`).join("")}
      ${m.state_delta.map((f) => `<li>appeared: ${factLine(f)}</li>`).join("")}
    </ul>
    ${attemptsHtml(attempts)}`;
}

function openTrace(tr) {
  const evidence = !$("#evidence").hidden;
  openDrawer(memoryHtml(tr.memory) + proofHtml(crossingProof(tr.origin, tr.intrusion, tr.manifestation, tr.attempts), evidence));
}

function openDrawer(html) {
  if ($("#drawer").hidden) S.drawerTrigger = document.activeElement;
  $("#drawer-body").innerHTML = html;
  $("#drawer").hidden = false;
  const body = $("#drawer-body");
  body.setAttribute("tabindex", "-1");
  body.focus();                                      // screen readers start at the memory, before the proof
  body.scrollTop = 0;
}
function closeDrawer() {
  $("#drawer").hidden = true;
  const t = S.drawerTrigger;
  if (t && document.contains(t)) t.focus();
}
$("#drawer-close").addEventListener("click", closeDrawer);

/* ---------------------------------------------------------------- verification */
async function openVerify() {
  let d;
  try { d = await api(`/api/verify?game=${encodeURIComponent(S.game)}`); }
  catch (e) { openDrawer(`<p class="bad">${esc(e.message)}</p>`); return; }
  const rows = Object.keys(d.hashes).map((b) =>
    `<li>timeline ${esc(b)} · ${esc(d.hashes[b])} ${d.replayed[b] === d.hashes[b] ? `<span class="ok">matches</span>` : `<span class="bad">differs: ${esc(d.replayed[b])}</span>`}</li>`).join("");
  openDrawer(`<p class="kicker">verify this run</p>
    <h3>${d.verified ? "Reproduced exactly." : "Did not reproduce."}</h3>
    <p>Every model output from this night was fed back through the engine, which re-validated and re-committed every event from the start. The resulting state of every timeline was compared with the ${esc(d.against)}.</p>
    <ul class="facts">${rows}</ul>
    <p class="small muted">The same check runs on every recorded night in the test suite, and fails if a single recorded model output is changed.</p>`);
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
  $("#map").classList.remove("revealing");
  $("#map-intro").hidden = true;
  $("#map").querySelectorAll("[data-stage]").forEach((el) => el.classList.add("on"));
  if (S.mapData && S.mapData.intrusions.length) selectCrossing(0, false);
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
  at(1800, () => { $("#map-intro").hidden = true; stage(1); });
  at(2600, () => stage(2));
  at(3400, () => stage(3));
  at(4200, () => { stage(4); Sound.crossing(); });
  at(5400, () => stage(5));
  at(6600, showAll);
}
$("#map-skip").addEventListener("click", showAll);
function closeMap() {
  showAll();
  $("#map").hidden = true;
  (S.mapTrigger && document.contains(S.mapTrigger) ? S.mapTrigger : $("#choices button") || document.body).focus?.();
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
  svg.toggleAttribute("hidden", narrow);
  $("#map-list").hidden = !narrow;
  $("#map-detail").hidden = narrow;
  drawEventList(d);
  if (narrow) return drawMapList(d);
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
  const step = Math.max(84, (box - 180) / Math.max(maxSeq, 1));
  const W = 90 + maxSeq * step + 90, H = Math.max(460, Math.min(window.innerHeight - 330, 620));
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
    n.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); selectNode(id); } });
  });
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
  const d = S.mapData, e = d.events.find((x) => x.event_id === id);
  const x = d.intrusions.find((i) => i.manifestation_event_id === id || i.origin_event_id === id);
  const evidence = !$("#evidence").hidden;
  const raw = `<ul class="facts">
      <li>event <b>${esc(e.event_id)}</b> · ${esc({ model: "proposed by the model, validated, committed", fallback: "hand-written fallback after rejected model proposals", story: "hand-written story event", intrusion: "a crossing from another timeline" }[e.source])}</li>
      ${e.summary && e.source === "model" ? `<li>the model's own summary: "${esc(e.summary)}"</li>` : ""}
      ${e.preconditions.length ? `<li>preconditions: ${esc(e.preconditions.join("; "))}</li>` : ""}
      ${e.evidence_refs.length ? `<li>evidence: ${esc(e.evidence_refs.join(", "))}</li>` : ""}
      ${e.state_delta.map((f) => `<li>${factLine(f)}</li>`).join("")}
    </ul>${attemptsHtml(e.attempts)}`;
  if (x) {
    const o = d.events.find((y) => y.event_id === x.origin_event_id);
    const m = d.events.find((y) => y.event_id === x.manifestation_event_id);
    openDrawer(memoryHtml(x.memory, x.origin_event_id === id ? "the other night" : "your night") +
      proofHtml(crossingProof(o, x, m, o.attempts), evidence));
    return;
  }
  openDrawer(`<p class="kicker">${esc(d.labels[e.branch] || e.branch)}</p>
    <div class="quote ${e.branch === "B" || e.branch === "D" ? "shadowq" : ""}">${esc(e.desc)}</div>` + proofHtml(raw, evidence));
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
  $("#evidence-body").innerHTML = summary + `<p class="small muted">model ${esc(d.model.model)} ${esc(d.model.quantization || "")} · seed ${esc(d.seed)} · ${d.log.length} records</p>` +
    d.log.map((r) => {
      if (r.kind === "proposal") {
        const dec = r.decision || {};
        const verdict = dec.accepted ? `<span class="ok">COMMITTED ${esc(r.event_id || "")}</span>` : `<span class="bad">REJECTED</span> ${esc((dec.violations || []).map((v) => v.rule + ": " + v.detail).join("; "))}`;
        return `<div class="ev"><div class="head">${esc(r.branch)} step ${r.step + 1} · attempt ${r.attempt + 1} · prompt ${esc(r.prompt_hash)} · ${((r.meta || {}).latency_s || 0).toFixed(2)} s</div>${verdict}<pre>${esc(JSON.stringify(r.proposal))}</pre></div>`;
      }
      if (r.kind === "fallback") return `<div class="ev"><div class="head">${esc(r.branch)} step ${r.step + 1}</div><span class="fb">FALLBACK COMMITTED ${esc(r.event_id)}</span><pre>${esc(JSON.stringify(r.proposal))}</pre></div>`;
      if (r.kind === "narration") return `<div class="ev"><div class="head">narration ${esc(r.intrusion)} · attempt ${r.attempt + 1}</div>${r.problems.length ? `<span class="bad">FAILED CHECKS</span> ${esc(r.problems.join("; "))}` : `<span class="ok">PASSED CHECKS</span>`}<pre>${esc(r.raw ?? r.text)}</pre></div>`;
      if (r.kind === "narration_fallback") return `<div class="ev"><div class="head">narration ${esc(r.intrusion)}</div><span class="fb">FALLBACK TEXT</span><pre>${esc(r.text)}</pre></div>`;
      return `<div class="ev"><div class="head">${esc(r.kind)}</div><pre>${esc(JSON.stringify(r))}</pre></div>`;
    }).join("");
}
function toggleEvidence() {
  const el = $("#evidence");
  el.hidden = !el.hidden;
  if (!$("#map").hidden && S.mapData) drawMap(S.mapData);
  $("#evidence-toggle").setAttribute("aria-pressed", String(!el.hidden));
  if (!el.hidden) loadEvidence();
}
$("#evidence-toggle").addEventListener("click", toggleEvidence);
$("#evidence-close").addEventListener("click", toggleEvidence);
$("#sound-toggle").addEventListener("click", () => { Sound.init(); Sound.toggle(); });
$("#instant-toggle").addEventListener("click", () => { INSTANT = !INSTANT; Prefs.set("instant", INSTANT ? "1" : "0"); applyInstant(); });
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
