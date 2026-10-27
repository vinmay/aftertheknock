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
async function boot() {
  try {
    const st = await api("/api/status");
    if (st.static) {
      $("#start").hidden = true;
      $("#status-line").textContent = "";
      $("#static-note").hidden = false;
    } else if (!st.live) {
      fallBackToReplays("The live model isn't available right now, so this page is showing recorded runs.");
    } else if (st.hosted) {
      $("#status-line").textContent = `Live: shadow timelines are generated now by ${st.model}, an open-weight Gemma model, through ${st.provider}.`;
    } else {
      $("#status-line").textContent = `Live: shadow timelines are generated now, on this machine, by ${st.model}.`;
    }
    const list = await api("/api/replays");
    if (list.length) {
      $("#replays").hidden = false;
      $("#replay-list").innerHTML = list.map((r) =>
        `<li><button data-id="${esc(r.id)}">${esc(r.pocket)} · ${esc(r.path)} · seed ${esc(r.seed)}</button></li>`).join("");
      $("#replay-list").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => startReplay(b.dataset.id)));
    }
  } catch (e) {
    $("#status-line").textContent = "Could not reach the server.";
  }
}

$("#start").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  if (S.busy) return;
  Sound.init();
  S.busy = true; $("#begin").disabled = true; $("#begin").textContent = "…";
  try {
    const d = await api("/api/new", { pocket: $("#pocket").value });
    S.game = d.game; S.replay = null; S.pocket = d.pocket;
    setMode(`<span class="tag live">live</span> ${esc(d.model || "")} seed ${esc(d.seed)}`);
    show(d.view);
    Sound.knock();
  } catch (e) {
    if (e.fallback === "replay") fallBackToReplays(`${e.message} Showing recorded runs instead.`);
    else $("#status-line").textContent = e.message;
  } finally {
    S.busy = false; $("#begin").disabled = false; $("#begin").textContent = "Begin";
  }
});

async function startReplay(id) {
  Sound.init();
  const d = await api("/api/replay/start", { id });
  S.game = d.game; S.replay = d.replay; S.step = 0; S.pocket = d.pocket;
  setMode(`<span class="tag replay">replay</span> ${esc(REPLAY_LABEL)} · ${esc(d.replay.model.model)} · seed ${esc(d.seed)}`);
  show(d.view);
  Sound.knock();
}

function fallBackToReplays(why) {
  $("#start").hidden = true;
  $("#status-line").textContent = why;
  $("#static-note").hidden = false;
}

function setMode(html) { $("#mode").innerHTML = html; }

/* ---------------------------------------------------------------- scenes */
function paragraphs(text) {
  return text.split(/\n\s*\n/).map((p) => p.trim()).filter(Boolean);
}

function show(v) {
  S.view = v;
  $("#title").hidden = true;
  $("#scene").hidden = false;
  const scene = $("#scene");
  scene.className = "screen " + (v.kind || "");
  const text = $("#text");
  let html = "";
  if (v.kind === "crossing") {
    const c = v.crossing;
    const mark = c.inscription;
    html = paragraphs(v.text).map((p) => {
      const t = esc(p).replace(esc(mark), `<button class="ink" id="ink" title="Where did this come from?">${esc(mark)}</button>`);
      return `<p>${t}</p>`;
    }).join("") + `<p><button class="trace-hint" id="trace-hint">where did this come from? ↗</button></p>`;
    stageCrossing(c);
  } else {
    html = paragraphs(v.text).map((p, i) => `<p style="animation-delay:${i * 0.35}s">${esc(p)}</p>`).join("");
  }
  if (v.kind === "ending" && v.verified !== undefined) {
    const how = STATIC ? "Verified when this site was built: re-running" : "Replay verified: re-running";
    html += `<p class="small muted">${v.verified ? `${how} the recorded model outputs through the engine reproduced every timeline's state hash.` : "Replay did not reproduce the recorded state."}</p>`;
  }
  text.innerHTML = html;
  const ink = $("#ink"), hint = $("#trace-hint");
  if (ink) ink.addEventListener("click", () => openTrace(v.trace));
  if (hint) hint.addEventListener("click", () => openTrace(v.trace));

  const n = v.kind === "crossing" ? 1 : paragraphs(v.text).length;
  const ch = $("#choices");
  ch.style.animationDelay = `${Math.min(n * 0.35 + 0.4, 3)}s`;
  ch.innerHTML = "";
  ch.style.animation = "none"; void ch.offsetWidth; ch.style.animation = "";
  const recorded = S.replay ? S.replay.choices[S.step] : null;
  v.choices.forEach((label, i) => {
    const b = document.createElement("button");
    b.textContent = label;
    if (S.replay) { if (i === recorded) b.classList.add("recorded"); else b.disabled = true; }
    b.addEventListener("click", () => choose(i));
    ch.appendChild(b);
  });
  if (v.kind === "ending") {
    [["Open the map", () => openMap()], ["Begin again", () => location.reload()]].forEach(([label, fn]) => {
      const b = document.createElement("button"); b.textContent = label; b.addEventListener("click", fn); ch.appendChild(b);
    });
  }
  if (v.kind === "map") setTimeout(openMap, 1200);
  window.scrollTo({ top: 0, behavior: "smooth" });
  if (!$("#evidence").hidden) loadEvidence();
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
    S.step++;
    show(d.view);
  } catch (e) {
    $("#choices").insertAdjacentHTML("beforeend", `<p class="small bad">${esc(e.message)}</p>`);
  } finally {
    clearTimeout(wait);
    S.busy = false;
  }
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
  const close = () => { el.hidden = true; document.body.classList.remove("staged"); $("#ink")?.focus(); };
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

function openTrace(tr) {
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
  const lanes = { B: 80, S: 190, A: 190, C: 190, D: 300 };
  const X = (seq) => 40 + seq * 72;
  const maxSeq = Math.max(...d.events.map((e) => e.seq));
  const W = X(maxSeq) + 200, H = 360;
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("width", W); svg.setAttribute("height", H);
  const pos = {};
  d.events.forEach((e) => (pos[e.event_id] = [X(e.seq), lanes[e.branch]]));
  const origins = new Set(d.intrusions.map((x) => x.origin_event_id));
  let out = "";
  const br = Object.fromEntries(d.branches.map((b) => [b.id, b]));
  // lanes and fork connectors
  for (const b of d.branches) {
    const evs = d.events.filter((e) => e.branch === b.id);
    if (!evs.length) continue;
    const y = lanes[b.id], x0 = X(evs[0].seq), x1 = X(evs[evs.length - 1].seq);
    const cls = b.kind === "shadow" ? "lane shadow" : "lane";
    if (b.parent) {
      const px = X(b.fork_seq - 1), py = lanes[b.parent];
      out += `<path class="${cls}" d="M${px},${py} C${px + 30},${py} ${x0 - 30},${y} ${x0},${y}"/>`;
    }
    out += `<path class="${cls}" d="M${x0},${y} L${x1},${y}"/>`;
    const ly = b.kind === "shadow" ? (b.id === "B" ? y - 24 : y + 34) : y + (b.id === "A" ? 52 : 36);
    out += `<text class="lane-label" x="${x0 - 6}" y="${ly}">${esc(d.labels[b.id] || b.id)}</text>`;
  }
  // intrusion threads
  for (const x of d.intrusions) {
    const [ox, oy] = pos[x.origin_event_id], [mx, my] = pos[x.manifestation_event_id];
    out += `<path class="thread" d="M${ox},${oy} C${ox + 20},${(oy + my) / 2} ${mx - 20},${(oy + my) / 2} ${mx},${my}"/>`;
  }
  // nodes
  for (const e of d.events) {
    const [x, y] = pos[e.event_id];
    const kind = br[e.branch].kind === "shadow" ? "shadow" : "canonical";
    const cls = ["node", kind, e.source === "fallback" ? "fallback" : "", e.source === "intrusion" ? "intrusion" : "", origins.has(e.event_id) ? "origin" : ""].join(" ");
    const r = e.source === "story" ? 4 : 6.5;
    out += `<g class="${cls}" tabindex="0" data-id="${esc(e.event_id)}"><circle cx="${x}" cy="${y}" r="${r}"/>` +
      `<text x="${x}" y="${y + (kind === "shadow" && e.branch === "B" ? -12 : 20)}" text-anchor="middle">${esc(e.event_id)}</text>` +
      `<title>${esc(e.desc)}</title></g>`;
  }
  svg.innerHTML = out;
  svg.querySelectorAll(".node").forEach((n) => {
    const fn = () => openEvent(n.dataset.id);
    n.addEventListener("click", fn);
    n.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); fn(); } });
  });
}

function openEvent(id) {
  const d = S.mapData, e = d.events.find((x) => x.event_id === id);
  const x = d.intrusions.find((i) => i.manifestation_event_id === id || i.origin_event_id === id);
  let html = `<p class="kicker">${esc(e.event_id)} · ${esc(d.labels[e.branch] || e.branch)}</p>
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

boot().then(() => {
  const rid = new URLSearchParams(location.search).get("replay");
  if (rid) startReplay(rid).catch((e) => ($("#status-line").textContent = e.message));
});
