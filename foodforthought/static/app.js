(() => {
  document.addEventListener("submit", (event) => {
    const source = event.submitter && event.submitter.dataset.confirm ? event.submitter : event.target;
    const question = source.dataset.confirm;
    if (question && !window.confirm(question)) event.preventDefault();
  });

  document.addEventListener("click", (event) => {
    if (event.target.closest("[data-print]")) window.print();
  });

  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-pick]");
    if (!button) return;
    const picker = button.parentElement.querySelector(".date-picker");
    const typed = document.getElementById(button.dataset.pick).value.trim().match(/^(\d{1,2})[\/.\-](\d{1,2})[\/.\-](\d{4})$/);
    picker.value = typed ? `${typed[3]}-${typed[2].padStart(2, "0")}-${typed[1].padStart(2, "0")}` : "";
    try { picker.showPicker(); } catch (err) { /* typing the date still works */ }
  });

  let pickWhenReady = null;

  const pickFirst = (input) => {
    const list = document.querySelector(input.dataset.pickFirst);
    if (!list || list.dataset.q !== input.value.trim()) return false;
    const first = list.querySelector(".choice");
    if (first) first.click();
    return true;
  };

  document.addEventListener("keydown", (event) => {
    const input = event.target;
    if (!input.dataset || !input.dataset.pickFirst || event.key !== "Enter") return;
    event.preventDefault();
    if (!input.value.trim() || pickFirst(input)) return;
    pickWhenReady = input;
    if (window.htmx) window.htmx.trigger(input, "search");
  });

  document.addEventListener("input", (event) => {
    if (event.target === pickWhenReady) pickWhenReady = null;
  });

  document.addEventListener("htmx:afterSettle", () => {
    if (!pickWhenReady) return;
    const input = pickWhenReady;
    if (pickFirst(input) || !input.isConnected) pickWhenReady = null;
  });

  document.addEventListener("htmx:afterSwap", () => {
    const field = document.querySelector("#entry-form [autofocus]");
    if (field && document.activeElement !== field) field.focus();
  });

  document.addEventListener("change", (event) => {
    if (event.target.classList.contains("date-picker") && event.target.value) {
      const [year, month, day] = event.target.value.split("-");
      event.target.parentElement.querySelector("input[type=text]").value = `${day}/${month}/${year}`;
    }
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
