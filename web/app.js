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
  if (route === "/api/evidence") return { log: d.log.slice(0, d.steps[Static.step].log_len), ops: d.ops, model: d.model, seed: d.seed };
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
const article = (w) => (/^[aeiou]/i.test(w) ? "an " : "a ") + w;

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
      $("#replay-list").innerHTML = list.map((r, i) =>
        `<li><button data-id="${esc(r.id)}">Night ${i + 1} · ${esc(article(r.pocket))}</button></li>`).join("");
      $("#replay-list").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => startReplay(b.dataset.id)));
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
    S.busy = false; $("#begin").disabled = false; $("#begin").textContent = "Begin";
  }
});

async function startReplay(id) {
  Sound.init();
  const d = await api("/api/replay/start", { id });
  S.game = d.game; S.replay = d.replay; S.step = 0; S.pocket = d.pocket;
  setMode(`<span class="tag replay">replay</span>`, d.seed, `${esc(REPLAY_LABEL)} · recorded with ${esc(d.replay.model.model)}`);
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

const TYPE_MS = 14;              // per character; click, tap or Space skips
const Typing = { timer: null, finish: null };

function sceneHtml(v) {
  if (v.kind === "crossing") {
    const mark = v.crossing.inscription;
    return paragraphs(v.text).map((p) => {
      const t = esc(p).replace(esc(mark), `<button class="ink" title="Where did this come from?">${esc(mark)}</button>`);
      return `<p>${t}</p>`;
    }).join("") + `<p><button class="trace-hint">where did this come from? ↗</button></p>`;
  }
  let html = paragraphs(v.text).map((p) => `<p>${esc(p)}</p>`).join("");
  if (v.kind === "ending" && v.verified !== undefined) {
    const how = STATIC ? "Verified when this site was built: re-running" : "Replay verified: re-running";
    html += `<p class="small muted">${v.verified ? `${how} the recorded model outputs through the engine reproduced every timeline's state hash.` : "Replay did not reproduce the recorded state."}</p>`;
  }
  return html;
}

/* Reveal text nodes character by character. Resolves when done or skipped. */
function typeOut(root) {
  const nodes = [];
  const walk = (n) => n.childNodes.forEach((c) => (c.nodeType === 3 ? nodes.push([c, c.textContent]) : walk(c)));
  walk(root);
  if (REDUCED) return Promise.resolve();
  nodes.forEach(([n]) => (n.textContent = ""));
  root.classList.add("typing");
  return new Promise((resolve) => {
    let i = 0, j = 0;
    const done = () => {
      clearInterval(Typing.timer); Typing.timer = null; Typing.finish = null;
      nodes.forEach(([n, t]) => (n.textContent = t));
      root.classList.remove("typing");
      resolve();
    };
    Typing.finish = done;
    Typing.timer = setInterval(() => {
      for (let k = 0; k < 2; k++) {
        if (i >= nodes.length) return done();
        const [n, t] = nodes[i];
        n.textContent = t.slice(0, ++j);
        if (j >= t.length) { i++; j = 0; }
      }
    }, TYPE_MS);
  });
}

function skipTyping() { if (Typing.finish) { Typing.finish(); return true; } return false; }

async function show(v) {
  S.view = v;
  $("#title").hidden = true;
  $("#scene").hidden = false;
  $("#scene").className = "screen kind-" + (v.kind || "scene");
  setClock(v.clock);
  if (v.power === "out" && !REDUCED) {
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

  if (v.kind === "crossing") stageCrossing(v.crossing);
  else await typeOut(text);
  if (S.view !== v) return;

  const recorded = S.replay ? S.replay.choices[S.step] : null;
  v.choices.forEach((label, i) => {
    const b = document.createElement("button");
    b.innerHTML = `<span class="num">${i + 1}</span>${esc(label)}`;
    b.dataset.index = i;
    if (S.replay) { if (i === recorded) b.classList.add("recorded"); else b.disabled = true; }
    b.addEventListener("click", () => choose(i));
    ch.appendChild(b);
  });
  if (v.kind === "ending") {
    [["Play again — it won't be the same night", () => (location.href = location.pathname)],
     ["See how this was made", () => howItWorks()],
     ["Open the map", () => openMap()]].forEach(([label, fn]) => {
      const b = document.createElement("button"); b.textContent = label; b.addEventListener("click", fn); ch.appendChild(b);
    });
  }
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

/* Click or tap the text to skip typing; Space skips; 1-9 choose; Enter takes a single choice. */
document.addEventListener("click", (ev) => {
  if (Typing.finish && ev.target.closest("#current") && !ev.target.closest("button")) skipTyping();
});
document.addEventListener("keydown", (ev) => {
  if (ev.target.tagName === "INPUT" || ev.target.tagName === "TEXTAREA") return;
  if (!$("#crossing-stage").hidden || !$("#map").hidden || !$("#drawer").hidden) return;
  if (ev.key === " " && skipTyping()) { ev.preventDefault(); return; }
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

function stageCrossing(c) {
  const el = $("#crossing-stage");
  const voice = c.sense === "voice";
  const kind = voice ? "voice" : c.object === "key07" ? "tag" : "paper";
  const label = voice ? "" : c.object === "key07" ? "BASEMENT 07" : esc(S.pocket || "");
  el.innerHTML = `
    <div class="stage-inner">
      <p class="stage-before">${esc(c.before)}</p>
      <div class="card ${kind}">
        ${label ? `<span class="card-label">${label}</span>` : ""}
        <svg class="hand" viewBox="0 0 400 90" role="img" aria-label="${esc(c.inscription)}">
          <text x="200" y="60" text-anchor="middle">${esc(c.inscription)}</text>
        </svg>
      </div>
      <p class="stage-after">${esc(`${c.after} ${c.who}`.trim())}</p>
      ${c.color ? `<p class="stage-color">${esc(c.color)}</p>` : ""}
      <button class="primary stage-go" id="stage-go">Go on</button>
    </div>`;
  el.hidden = false;
  el.classList.toggle("instant", REDUCED);
  document.body.classList.add("staged");
  if (!REDUCED) {
    document.body.classList.add("flicker");
    setTimeout(() => document.body.classList.remove("flicker"), 1000);
  }
  voice ? Sound.crossing() : Sound.pen(c.inscription.length);
  const close = () => { el.hidden = true; document.body.classList.remove("staged"); $("#current .ink")?.focus(); };
  $("#stage-go").addEventListener("click", close);
  setTimeout(() => $("#stage-go")?.focus(), REDUCED ? 0 : 3200);
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

function fullRecordUnlocked() {
  return S.mapSeen || !$("#evidence").hidden;
}

function openTrace(tr) {
  if (!fullRecordUnlocked()) {
    openDrawer(`<p class="kicker">where it came from</p>
      <div class="quote shadowq tease">${esc(tr.teaser)}</div>
      <p class="small muted">The full record opens with the map, at the end of the night. (Judges: the <b>evidence</b> switch at the top shows everything now.)</p>`);
    return;
  }
  const o = tr.origin, x = tr.intrusion, m = tr.manifestation;
  const body = `
    <p class="kicker">intrusion ${esc(x.intrusion_id)} · from timeline ${esc(x.origin_branch)} into ${esc(x.target_branch)}</p>
    <h3>It came from here</h3>
    <div class="quote shadowq">${esc(o.desc)}</div>
    ${o.summary && o.source === "model" ? `<p class="small muted">The model's own words: "${esc(o.summary)}"</p>` : ""}
    <ul class="facts">
      <li>event <b>${esc(o.event_id)}</b> in timeline ${esc(o.branch)} (the night you didn't choose)</li>
      <li>${esc(o.actor)} · ${esc(o.action)} · ${esc(o.target)}${o.text ? ` · "${esc(o.text)}"` : ""}</li>
      <li>proposed by ${o.source === "model" ? "the model" : "a hand-written fallback"}; committed by the engine</li>
      <li>preconditions: ${esc((o.preconditions || []).join("; ") || "none")}</li>
      <li>evidence: ${esc((o.evidence_refs || []).join(", ") || "none")}</li>
    </ul>
    ${attemptsHtml(tr.attempts)}
    <h3>What crossed</h3>
    <ul class="facts">${x.post_fork.map((f) => `<li>${factLine(f)} <span class="muted">(created after the fork)</span></li>`).join("")}</ul>
    <h3>Where it appeared</h3>
    <ul class="facts"><li>event <b>${esc(m.event_id)}</b> in your timeline, citing ${esc(m.origin)}</li>
    ${m.state_delta.map((f) => `<li>${factLine(f)}</li>`).join("")}</ul>`;
  openDrawer(body);
}

function openDrawer(html) {
  $("#drawer-body").innerHTML = html;
  $("#drawer").hidden = false;
  $("#drawer-close").focus();
}
$("#drawer-close").addEventListener("click", () => ($("#drawer").hidden = true));

/* ---------------------------------------------------------------- map */
async function openMap() {
  try {
    S.mapData = await api(`/api/map?game=${encodeURIComponent(S.game)}`);
  } catch (e) { return; }
  drawMap(S.mapData);
  $("#map").hidden = false;
}
$("#map-close").addEventListener("click", () => ($("#map").hidden = true));

function drawMap(d) {
  const svg = $("#map-svg");
  const box = $("#map .map-scroll").clientWidth || window.innerWidth;
  const vertical = box < 640;
  const evidence = !$("#evidence").hidden;
  const br = Object.fromEntries(d.branches.map((b) => [b.id, b]));
  // Causal x order: shadows start right after their fork; a crossing lands after its origin.
  const col = {}, lastCol = {};
  const byBranch = (id) => d.events.filter((e) => e.branch === id).sort((a, b) => a.seq - b.seq);
  for (const id of ["S", "B", "A", "D", "C"]) {
    const b = br[id];
    if (!b) continue;
    let c = b.parent ? lastColAt(b.parent, b.fork_seq) : -1;
    for (const e of byBranch(id)) {
      c += 1;
      if (e.origin && col[e.origin] !== undefined) c = Math.max(c, col[e.origin] + 1);
      col[e.event_id] = c;
    }
    lastCol[id] = c;
  }
  function lastColAt(parent, forkSeq) {
    const evs = d.events.filter((e) => [parent, br[parent].parent].includes(e.branch) && e.seq < forkSeq);
    return Math.max(-1, ...evs.map((e) => col[e.event_id] ?? -1));
  }
  const maxSeq = Math.max(...Object.values(col));
  const lane = vertical ? { B: 0.22, S: 0.55, A: 0.55, C: 0.55, D: 0.86 } : { B: 0.2, S: 0.5, A: 0.5, C: 0.5, D: 0.8 };
  let W, H, P;
  if (vertical) {
    const step = 64;
    W = Math.max(320, box); H = 90 + maxSeq * step + 220;
    P = (seq, b) => [W * lane[b], 70 + seq * step];
  } else {
    const step = Math.max(76, (box - 160) / Math.max(maxSeq, 1));
    W = 80 + maxSeq * step + 80; H = 420;
    P = (seq, b) => [80 + seq * step, H * lane[b]];
  }
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("width", W); svg.setAttribute("height", H);
  const pos = {};
  d.events.forEach((e) => (pos[e.event_id] = P(col[e.event_id], e.branch)));
  const origins = new Set(d.intrusions.map((x) => x.origin_event_id));
  const curve = ([x1, y1], [x2, y2]) => vertical
    ? `M${x1},${y1} C${x1},${(y1 + y2) / 2} ${x2},${(y1 + y2) / 2} ${x2},${y2}`
    : `M${x1},${y1} C${(x1 + x2) / 2},${y1} ${(x1 + x2) / 2},${y2} ${x2},${y2}`;
  let out = "";
  for (const b of d.branches) {
    const evs = d.events.filter((e) => e.branch === b.id);
    if (!evs.length) continue;
    const cls = br[b.id].kind === "shadow" ? "lane shadow" : "lane";
    const first = pos[evs[0].event_id], last = pos[evs[evs.length - 1].event_id];
    if (b.parent) out += `<path class="${cls}" d="${curve(P(lastColAt(b.parent, b.fork_seq), b.parent), first)}"/>`;
    out += `<path class="${cls}" d="M${first[0]},${first[1]} L${last[0]},${last[1]}"/>`;
    const label = esc(d.labels[b.id] || b.id);
    if (vertical && b.id === "S") continue;
    out += vertical
      ? `<text class="lane-label" transform="translate(${first[0] - 16},${first[1] + 14}) rotate(90)">${label}</text>`
      : `<text class="lane-label" x="${first[0] - 8}" y="${first[1] + (b.id === "B" ? -26 : b.id === "D" ? 38 : b.id === "A" ? 58 : 38)}">${label}</text>`;
  }
  d.intrusions.forEach((x, i) => {
    out += `<path class="thread" style="--i:${i}" pathLength="1" d="${curve(pos[x.origin_event_id], pos[x.manifestation_event_id])}"/>`;
  });
  for (const e of d.events) {
    const [x, y] = pos[e.event_id];
    const kind = br[e.branch].kind === "shadow" ? "shadow" : "canonical";
    const cls = ["node", kind, e.source, origins.has(e.event_id) ? "origin" : ""].join(" ");
    const r = e.source === "story" ? 5 : 9;
    out += `<g class="${cls}" tabindex="0" role="button" aria-label="${esc(e.desc)}" data-id="${esc(e.event_id)}">` +
      `<circle cx="${x}" cy="${y}" r="14" class="hit"/><circle cx="${x}" cy="${y}" r="${r}" class="dot"/>` +
      (evidence ? `<text x="${x}" y="${y + (vertical ? 4 : 26)}" dx="${vertical ? 16 : 0}" text-anchor="${vertical ? "start" : "middle"}" class="eid">${esc(e.event_id)}</text>` : "") +
      `</g>`;
  }
  svg.innerHTML = out;
  svg.classList.toggle("draw", !REDUCED);
  const tip = $("#map-tip");
  svg.querySelectorAll(".node").forEach((n) => {
    const e = d.events.find((x) => x.event_id === n.dataset.id);
    const show = () => {
      const r = n.getBoundingClientRect(), host = $("#map").getBoundingClientRect();
      tip.textContent = e.desc;
      tip.hidden = false;
      tip.style.left = `${Math.min(r.left - host.left, host.width - 260)}px`;
      tip.style.top = `${r.bottom - host.top + 8}px`;
    };
    n.addEventListener("mouseenter", show);
    n.addEventListener("focus", show);
    n.addEventListener("mouseleave", () => (tip.hidden = true));
    n.addEventListener("blur", () => (tip.hidden = true));
    n.addEventListener("click", () => { tip.hidden = true; openEvent(n.dataset.id); });
    n.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); openEvent(n.dataset.id); } });
  });
}

function openEvent(id) {
  const d = S.mapData, e = d.events.find((x) => x.event_id === id);
  const x = d.intrusions.find((i) => i.manifestation_event_id === id || i.origin_event_id === id);
  const ids = !$("#evidence").hidden;
  let html = `<p class="kicker">${ids ? esc(e.event_id) + " · " : ""}${esc(d.labels[e.branch] || e.branch)}</p>
    <div class="quote ${e.branch === "B" || e.branch === "D" ? "shadowq" : ""}">${esc(e.desc)}</div>
    <ul class="facts">
      <li>source: ${esc({ model: "proposed by the model, validated, committed", fallback: "hand-written fallback (model proposals were rejected)", story: "hand-written story event", intrusion: "intrusion from another timeline" }[e.source])}</li>
      ${e.summary && e.source === "model" ? `<li>the model's own words: "${esc(e.summary)}"</li>` : ""}
      ${e.preconditions.length ? `<li>preconditions: ${esc(e.preconditions.join("; "))}</li>` : ""}
      ${e.evidence_refs.length ? `<li>evidence: ${esc(e.evidence_refs.join(", "))}</li>` : ""}
      ${e.state_delta.map((f) => `<li>${factLine(f)}</li>`).join("")}
    </ul>`;
  html += attemptsHtml(e.attempts);
  if (x) {
    html += `<h3>Crossing ${esc(x.intrusion_id)}</h3><ul class="facts">
      <li>${esc(x.origin_event_id)} (timeline ${esc(x.origin_branch)}) → ${esc(x.manifestation_event_id)} (timeline ${esc(x.target_branch)})</li>
      ${x.post_fork.map((f) => `<li>${factLine(f)} <span class="muted">(created after the fork)</span></li>`).join("")}</ul>`;
    const nar = d.narration[x.origin_branch];
    if (nar && x.manifestation_event_id === id) {
      html += `<h3>Narration</h3><div class="quote">${esc(nar.text)}</div><p class="small muted">${nar.source === "model" ? "Written by the model from released facts only; passed the rule checks." : "The model's text failed the rule checks twice; this is the hand-written fallback."}</p>`;
    }
  }
  openDrawer(html);
}

/* ---------------------------------------------------------------- evidence */
async function loadEvidence() {
  if (!S.game) { $("#evidence-body").innerHTML = `<p class="small muted">Start a night first.</p>`; return; }
  const d = await api(`/api/evidence?game=${encodeURIComponent(S.game)}`);
  $("#evidence-body").innerHTML = `<p class="small muted">model ${esc(d.model.model)} ${esc(d.model.quantization || "")} · seed ${esc(d.seed)} · ${d.log.length} records</p>` +
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
document.addEventListener("keydown", (ev) => {
  if (ev.target.tagName === "INPUT") return;
  if (ev.key === "e" || ev.key === "E") toggleEvidence();
  if (ev.key === "Escape") {
    if (!$("#drawer").hidden) $("#drawer").hidden = true;
    else if (!$("#evidence").hidden) toggleEvidence();
    else $("#map").hidden = true;
  }
});

window.addEventListener("resize", () => { if (!$("#map").hidden && S.mapData) drawMap(S.mapData); });

boot().then(() => {
  const rid = new URLSearchParams(location.search).get("replay");
  if (rid) startReplay(rid).catch((e) => ($("#status-line").textContent = e.message));
});
