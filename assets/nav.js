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

  // Pick Five's own pages. The top bar has one "Pick Five" link for all three;
  // each of them shows links to the other two under its logo.
  const PICK_FIVE = [
    ["index.html", "Weekly Lines"],
    ["current-season.html", "Current Season"],
    ["lifetime.html", "Lifetime"],
  ];
  const inPickFive = PICK_FIVE.some(([href]) => href === current);

  document.querySelectorAll("nav.site-nav a").forEach((a) => {
    const active = a.dataset.section === "pickfive" ? inPickFive : a.getAttribute("href") === current;
    if (active) a.classList.add("active");
  });

  if (inPickFive) {
    const sub = document.createElement("nav");
    sub.className = "section-nav";
    sub.setAttribute("aria-label", "Pick Five pages");
    sub.innerHTML = PICK_FIVE.filter(([href]) => href !== current)
      .map(([href, label]) => `<a href="${href}">${label}</a>`)
      .join("");
    const logo = document.querySelector("header .logo");
    if (logo) logo.insertAdjacentElement("afterend", sub);
    else document.querySelector("nav.site-nav").insertAdjacentElement("afterend", sub);
  }
})();
