// Weekly photo strip: shows every image in a folder of the repo
// (images/weekly-lines/ on Weekly Lines, images/live-lines/ on Live Lines).
// Photos are uploaded by hand on GitHub (photos.html links to the upload
// pages) and the Clear Weekly Photos workflow empties both folders Tuesday.
(function () {
  const API = "https://api.github.com/repos/wiggywigs/PickFive/contents/";
  const IMAGE_EXTENSIONS = /\.(jpe?g|png|gif|webp)$/i;
  const esc = (s) =>
    String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // Image files in the folder; [] if it's missing, empty or GitHub can't be reached
  async function listPhotos(folder) {
    try {
      const res = await fetch(API + folder, { cache: "no-store" });
      if (!res.ok) return [];
      const files = await res.json();
      return Array.isArray(files) ? files.filter((f) => f.type === "file" && IMAGE_EXTENSIONS.test(f.name)) : [];
    } catch (e) {
      return [];
    }
  }

  // Fill the element with linked thumbnails; hide it when there are none
  async function showPhotos(folder, elId) {
    const el = document.getElementById(elId);
    const files = await listPhotos(folder);
    el.innerHTML = files.map((f) => `
      <a href="${esc(f.download_url)}" target="_blank" rel="noopener"><img src="${esc(f.download_url)}" alt="${esc(f.name)}"></a>`).join("");
    el.style.display = files.length ? "flex" : "none";
    return files;
  }

  window.PickFivePhotos = { listPhotos, showPhotos };
})();
