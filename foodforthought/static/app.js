(() => {
  document.addEventListener("submit", (event) => {
    const source = event.submitter && event.submitter.dataset.confirm ? event.submitter : event.target;
    const question = source.dataset.confirm;
    if (question && !window.confirm(question)) event.preventDefault();
  });

  document.addEventListener("click", (event) => {
    if (event.target.closest("[data-print]")) window.print();
  });

  document.addEventListener("change", (event) => {
    const target = event.target.dataset.optionsFor;
    if (target) document.getElementById(target).hidden = !["choice", "multi"].includes(event.target.value);
  });

  if (document.body.dataset.authed !== "1") return;

  const TOUCH_EVERY_MS = 30000;
  const POLL_MS = 10000;
  let active = false;
  let lastTouch = Date.now();

  const mark = () => { active = true; };
  ["keydown", "pointerdown", "input", "scroll"].forEach((name) =>
    window.addEventListener(name, mark, { passive: true, capture: true })
  );

  const toLockScreen = () => { window.location.href = "/unlock?msg=auto"; };

  const tick = async () => {
    try {
      if (active && Date.now() - lastTouch >= TOUCH_EVERY_MS) {
        active = false;
        lastTouch = Date.now();
        const touched = await fetch("/api/touch", { method: "POST" });
        if (touched.status === 401) return toLockScreen();
      }
      const status = await (await fetch("/api/status")).json();
      if (!status.unlocked) toLockScreen();
    } catch (err) {
      /* server stopped; nothing to do */
    }
  };

  setInterval(tick, POLL_MS);
})();
