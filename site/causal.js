/* Causal order of a night's events, shared by the map graph and the event list.

   Pure: takes the map data, returns { col, forkCol, order }.
   - a fork's parent is placed before its children; a child starts one column after the fork point;
   - events keep their order within a branch;
   - every crossing's origin comes before its manifestation (a branch waits for the branches its
     crossings come from, and the manifestation lands at least one column after the origin);
   - ties break by branch rank (S, B, A, D, C, then by id), then by sequence. */
(function (root) {
  const RANK = ["S", "B", "A", "D", "C"];

  function causalOrder(d) {
    const br = Object.fromEntries(d.branches.map((b) => [b.id, b]));
    const rank = (id) => (RANK.includes(id) ? RANK.indexOf(id) : RANK.length) + ":" + id;
    const byRank = (a, b) => (rank(a) < rank(b) ? -1 : rank(a) > rank(b) ? 1 : 0);
    const branchOf = Object.fromEntries(d.events.map((e) => [e.event_id, e.branch]));
    const events = (id) => d.events.filter((e) => e.branch === id).sort((a, b) => a.seq - b.seq);
    const needs = (id) => {
      const deps = new Set(br[id].parent ? [br[id].parent] : []);
      for (const e of events(id)) if (e.origin && branchOf[e.origin] && branchOf[e.origin] !== id) deps.add(branchOf[e.origin]);
      return [...deps].filter((x) => br[x]);
    };
    // Branches in dependency order; the rank breaks ties (and any cycle, which the engine never makes).
    const placed = [], left = d.branches.map((b) => b.id).sort(byRank);
    while (left.length) {
      const i = Math.max(0, left.findIndex((id) => needs(id).every((x) => placed.includes(x))));
      placed.push(left.splice(i, 1)[0]);
    }
    const col = {}, forkCol = {};
    const ancestors = (id) => { const out = []; for (let p = br[id].parent; p; p = br[p]?.parent) out.push(p); return out; };
    for (const id of placed) {
      const b = br[id];
      const up = ancestors(id);
      const before = d.events.filter((e) => up.includes(e.branch) && e.seq < b.fork_seq);
      let c = b.parent ? Math.max(-1, ...before.map((e) => col[e.event_id] ?? -1)) : -1;
      forkCol[id] = c;
      for (const e of events(id)) {
        c += 1;
        if (e.origin && col[e.origin] !== undefined) c = Math.max(c, col[e.origin] + 1);
        col[e.event_id] = c;
      }
    }
    const order = [...d.events].sort((a, b) => col[a.event_id] - col[b.event_id] || byRank(a.branch, b.branch) || a.seq - b.seq);
    return { col, forkCol, order };
  }

  if (typeof module !== "undefined" && module.exports) module.exports = { causalOrder };
  else root.causalOrder = causalOrder;
})(typeof window !== "undefined" ? window : globalThis);
