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
const article = (w) => (/^[aeiou]/i.test(w) ? "an " : "a ") + w;
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
            <span class="card-title">Night ${i + 1} · ${esc(article(r.pocket))}</span><span class="card-meta">Recorded run</span>
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
  return paragraphs(v.text).map((p) => `<p>${esc(p)}</p>`).join("");
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

/* Story -> memory -> proof. The memory and labels are built by the engine from committed events;
   raw ids, state paths and the model's own words stay inside "View proof". */
function memoryHtml(mem, kicker = "where it came from") {
  return `<p class="kicker">${esc(kicker)}</p>
    <div class="quote shadowq memory">${esc(mem.memory)}</div>
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
  $("#drawer-body").innerHTML = html;
  $("#drawer").hidden = false;
  $("#drawer-close").focus();
}
$("#drawer-close").addEventListener("click", () => ($("#drawer").hidden = true));

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
  $("#map").hidden = false;
  drawMap(S.mapData);
  if (!S.mapRevealed) { S.mapRevealed = true; reveal(); }
  else showAll();
}

/* The reveal: a line of text, then your path, each rejected branch at its fork, then the threads.
   A presentation of committed data only; skippable, and instant under reduced motion. */
const Reveal = { timers: [] };
function showAll() {
  Reveal.timers.forEach(clearTimeout); Reveal.timers = [];
  $("#map").classList.remove("revealing");
  $("#map-intro").hidden = true;
  $("#map").querySelectorAll("[data-stage]").forEach((el) => el.classList.add("on"));
}
function reveal() {
  if (REDUCED) return showAll();
  const map = $("#map");
  map.classList.add("revealing");
  map.querySelectorAll("[data-stage]").forEach((el) => el.classList.remove("on"));
  $("#map-intro").hidden = false;
  const at = (ms, fn) => Reveal.timers.push(setTimeout(fn, ms));
  const stage = (n) => map.querySelectorAll(`[data-stage="${n}"]`).forEach((el) => el.classList.add("on"));
  at(2200, () => { $("#map-intro").hidden = true; stage(1); });
  at(3200, () => stage(2));
  at(4400, () => stage(3));
  at(5600, () => { stage(4); Sound.crossing(); });
  at(7400, () => { stage(5); });
  at(9000, showAll);
}
$("#map-skip").addEventListener("click", showAll);
$("#map-close").addEventListener("click", () => ($("#map").hidden = true));

function drawMap(d) {
  const svg = $("#map-svg");
  const box = $("#map .map-scroll").clientWidth || window.innerWidth;
  const narrow = window.matchMedia("(max-width: 40rem)").matches;
  svg.toggleAttribute("hidden", narrow);
  $("#map-list").hidden = !narrow;
  if (narrow) return drawMapList(d);
  const vertical = false;
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
    const label = esc(d.labels[b.id] || b.id);
    if (vertical && b.id === "S") continue;
    out += vertical
      ? `<text class="lane-label" transform="translate(${first[0] - 16},${first[1] + 14}) rotate(90)">${label}</text>`
      : `<text class="lane-label" x="${first[0] - 8}" y="${first[1] + (b.id === "B" ? -26 : b.id === "D" ? 38 : b.id === "A" ? 58 : 38)}">${label}</text>`;
    const f = forkAt[b.id];
    if (f && !vertical) {
      const [fx, fy] = P(lastColAt(b.parent, b.fork_seq), b.parent);
      out += `<text class="fork-label" x="${fx}" y="${fy + (b.id === "B" ? -16 : 22)}" text-anchor="middle">\u201c${esc(f.not_taken)}\u201d</text>`;
    }
    out += `</g>`;
  }
  d.intrusions.forEach((x, i) => {
    out += `<path class="thread" data-stage="${4 + i}" pathLength="1" d="${curve(pos[x.origin_event_id], pos[x.manifestation_event_id])}"/>`;
  });
  for (const e of d.events) {
    const [x, y] = pos[e.event_id];
    const kind = br[e.branch].kind === "shadow" ? "shadow" : "canonical";
    const cls = ["node", kind, e.source, origins.has(e.event_id) ? "origin" : ""].join(" ");
    const r = e.source === "story" ? 5 : 9;
    const xo = d.intrusions.find((i) => i.origin_event_id === e.event_id);
    const xm = d.intrusions.find((i) => i.manifestation_event_id === e.event_id);
    const role = xo ? ` This is where \u201c${xo.memory.inscription}\u201d was written.` : xm ? ` \u201c${xm.memory.inscription}\u201d appeared here, from another night.` : "";
    const name = `${d.labels[e.branch] || ""}. ${e.desc}${role}`;
    out += `<g class="${cls}" data-stage="${stageOf[e.branch]}" tabindex="0" role="button" aria-label="${esc(name)}" data-id="${esc(e.event_id)}">` +
      `<circle cx="${x}" cy="${y}" r="14" class="hit"/><circle cx="${x}" cy="${y}" r="${r}" class="dot"/>` +
      (evidence ? `<text x="${x}" y="${y + (vertical ? 4 : 26)}" dx="${vertical ? 16 : 0}" text-anchor="${vertical ? "start" : "middle"}" class="eid">${esc(e.event_id)}</text>` : "") +
      `</g>`;
  }
  svg.innerHTML = out;
  const tip = $("#map-tip");
  svg.querySelectorAll(".node").forEach((n) => {
    const e = d.events.find((x) => x.event_id === n.dataset.id);
    const show = () => {
      const r = n.getBoundingClientRect(), host = $("#map").getBoundingClientRect();
      const xo = d.intrusions.find((i) => i.origin_event_id === e.event_id);
      const xm = d.intrusions.find((i) => i.manifestation_event_id === e.event_id);
      tip.textContent = e.desc + (xo ? " (written there)" : xm ? " (appeared here)" : "");
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

/* Phones: the same data as a vertical list of nights, linked by crossing markers. */
function drawMapList(d) {
  const by = (id) => d.events.filter((e) => e.branch === id && e.source !== "story" || (e.branch === id && e.source === "story" && e.seq === 0));
  const forks = Object.fromEntries((d.forks || []).map((f) => [f.at, f]));
  const evBtn = (e) => {
    const xo = d.intrusions.find((i) => i.origin_event_id === e.event_id);
    const xm = d.intrusions.find((i) => i.manifestation_event_id === e.event_id);
    const mark = xo ? `<span class="xmark">written there \u2198</span>` : xm ? `<span class="xmark">appeared here</span>` : "";
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
