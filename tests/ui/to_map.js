(async () => {
  const post = (p, b) => fetch(p, {method: "POST", body: JSON.stringify(b)}).then((r) => r.json());
  const d = await post("/api/new", {pocket: "a bus ticket"});
  let v = d.view; const plan = window.__plan || {fork1: 1, fork2: 0, final: 1};
  while (v.kind !== "map") v = (await post("/api/choose", {game: d.game, index: plan[v.kind] ?? 0})).view;
  S.game = d.game; S.mapSeen = true; show(v); await openMap(); return "ok";
})()
