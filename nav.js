(async function () {
  const placeholder = document.getElementById("nav-placeholder");
  if (!placeholder) return;

  try {
    const res = await fetch("assets/nav.html", { cache: "no-store" });
    if (!res.ok) return;
    placeholder.outerHTML = await res.text();
  } catch (e) {
    return; // page still works without nav if this fails - just no nav bar
  }

  const current = location.pathname.split("/").pop() || "index.html";
  document.querySelectorAll("nav.site-nav a").forEach((a) => {
    if (a.getAttribute("href") === current) a.classList.add("active");
  });
})();
