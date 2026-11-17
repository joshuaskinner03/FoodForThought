(() => {
  const check = async () => {
    try {
      const response = await fetch("/api/status", { cache: "no-store" });
      if (response.ok) window.location.replace("/");
    } catch (err) {
      /* still not running */
    }
  };
  setInterval(check, 3000);
})();
