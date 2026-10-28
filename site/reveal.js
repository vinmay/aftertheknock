/* When a crossing stage has finished revealing itself.

   revealGate(last, fallbackMs, onReady, names) calls onReady exactly once: when `last` (the stage's last
   revealed element) finishes its own reveal animation, or after `fallbackMs` if that event never comes
   (a background tab, an interrupted animation). animationend events bubbling up from other elements, or
   from other animations on `last`, are ignored. fire() readies at once (a skip); cancel() drops it all
   (a scene change or reset). */
(function (root) {
  function revealGate(last, fallbackMs, onReady, names = ["in"]) {
    let done = false;
    const off = () => { clearTimeout(timer); if (last) last.removeEventListener("animationend", onEnd); };
    const fire = () => { if (done) return; done = true; off(); onReady(); };
    function onEnd(e) {
      if (e.target !== last || !names.includes(e.animationName)) return;
      fire();
    }
    if (last) last.addEventListener("animationend", onEnd);
    const timer = setTimeout(fire, fallbackMs);
    return { fire, cancel() { done = true; off(); }, get done() { return done; } };
  }

  if (typeof module !== "undefined" && module.exports) module.exports = { revealGate };
  else root.revealGate = revealGate;
})(typeof window !== "undefined" ? window : globalThis);
