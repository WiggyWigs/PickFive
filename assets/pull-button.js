// "Pull now" button: starts the site's pull workflow on GitHub right away,
// instead of waiting for a scheduled run.
//
// The GitHub key is never in the site's code. It's pasted once per browser
// and kept in that browser's storage, which Pick Five and The Hook share
// (both are wiggywigs.github.io). Use a fine-grained key limited to the
// PickFive and TheHook repos with only "Actions: Read and write".
//
// The lock check here only greys the button out; the workflow enforces the
// same lock itself (PULL_SOURCE=button), so a press can never take the
// Tuesday line early.
(function () {
  const STORE = "wiggyPullKey";
  const OWNER = "WiggyWigs";

  function getKey() { try { return localStorage.getItem(STORE); } catch (e) { return null; } }
  function setKey(v) { try { localStorage.setItem(STORE, v); } catch (e) { /* not kept - asked again next time */ } }
  function forgetKey() { try { localStorage.removeItem(STORE); } catch (e) { /* nothing kept */ } }

  // Weekday (0 = Sunday), hour and calendar date in Eastern time
  function eastern(d) {
    const parts = {};
    new Intl.DateTimeFormat("en-US", {
      timeZone: "America/New_York", weekday: "short", hour: "numeric", hourCycle: "h23",
      year: "numeric", month: "2-digit", day: "2-digit",
    }).formatToParts(d).forEach((p) => { parts[p.type] = p.value; });
    return {
      day: ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].indexOf(parts.weekday),
      hour: Number(parts.hour),
      date: new Date(Date.UTC(Number(parts.year), Number(parts.month) - 1, Number(parts.day))),
    };
  }

  // Monday on or after the Eastern date (same as week_id_for in the pull scripts)
  function weekId(d) {
    const e = eastern(d);
    const monday = new Date(e.date);
    monday.setUTCDate(monday.getUTCDate() + ((8 - e.day) % 7));
    return monday.toISOString().slice(0, 10);
  }

  // First two rows of a CSV, quotes handled
  function csvHead(text) {
    const rows = [[]];
    let cell = "", quoted = false;
    for (let i = 0; i < text.length && rows.length <= 2; i++) {
      const c = text[i];
      if (quoted) {
        if (c === '"' && text[i + 1] === '"') { cell += '"'; i++; }
        else if (c === '"') quoted = false;
        else cell += c;
      } else if (c === '"') quoted = true;
      else if (c === ",") { rows[rows.length - 1].push(cell); cell = ""; }
      else if (c === "\n" || c === "\r") {
        if (c === "\r" && text[i + 1] === "\n") i++;
        rows[rows.length - 1].push(cell); cell = ""; rows.push([]);
      } else cell += c;
    }
    if (cell || rows[rows.length - 1].length) rows[rows.length - 1].push(cell);
    return rows;
  }

  // Lock rules, one per site. Each returns null (unlocked) or the reason.
  const LOCKS = {
    // Tuesday until 12 PM Eastern
    hook: async () => {
      const e = eastern(new Date());
      return e.day === 2 && e.hour < 12 ? "Locked until 12 PM ET Tuesday" : null;
    },
    // Tuesday until this week's Splash lines are in the sheet (their Copied At)
    splash: async () => {
      const now = new Date();
      if (eastern(now).day !== 2) return null;
      const waiting = "Locked until this week's Splash lines are pasted";
      try {
        const cur = await (await fetch("data/splits/current.json", { cache: "no-store" })).json();
        if (!cur.splash_csv_url) return waiting;
        const rows = csvHead(await (await fetch(cur.splash_csv_url, { cache: "no-store" })).text());
        const col = rows[0].findIndex((h) => h.trim().toLowerCase() === "copied at");
        const copiedAt = col >= 0 && rows[1] ? rows[1][col] : "";
        return copiedAt && weekId(new Date(copiedAt)) === weekId(now) ? null : waiting;
      } catch (e) {
        return waiting;
      }
    },
  };

  async function dispatch(repo, workflow, key) {
    return fetch(`https://api.github.com/repos/${OWNER}/${repo}/actions/workflows/${workflow}/dispatches`, {
      method: "POST",
      headers: {
        Accept: "application/vnd.github+json",
        Authorization: `Bearer ${key}`,
        "X-GitHub-Api-Version": "2022-11-28",
      },
      body: JSON.stringify({ ref: "main", inputs: { source: "button" } }),
    });
  }

  // root: element holding .pull-now-btn, .pull-now-msg and .pull-now-forget
  function mount(root, { repo, workflow, lock }) {
    const btn = root.querySelector(".pull-now-btn");
    const msg = root.querySelector(".pull-now-msg");
    const forget = root.querySelector(".pull-now-forget");
    const say = (t) => { msg.textContent = t; };
    const showForget = () => { forget.hidden = !getKey(); };

    async function refreshLock() {
      const reason = await LOCKS[lock]();
      btn.disabled = !!reason;
      if (reason) say(reason);
      else if (msg.dataset.state !== "sent") say("");
      return reason;
    }

    btn.addEventListener("click", async () => {
      if (await refreshLock()) return;
      let key = getKey();
      if (!key) {
        key = (window.prompt("Paste your GitHub key (starts with github_pat_). It's saved in this browser only.") || "").trim();
        if (!key) return;
      }
      btn.disabled = true;
      say("Starting…");
      let res;
      try {
        res = await dispatch(repo, workflow, key);
      } catch (e) {
        say("Couldn't reach GitHub - check your connection and try again.");
        btn.disabled = false;
        return;
      }
      if (res.status === 204) {
        setKey(key);
        msg.dataset.state = "sent";
        say("Pull started - refresh in about 2 minutes.");
      } else if (res.status === 401) {
        forgetKey();
        say("GitHub didn't accept that key (wrong, expired or deleted). Tap again to paste a new one.");
      } else if (res.status === 403 || res.status === 404) {
        forgetKey();
        say(`That key can't run ${repo}'s workflows - it needs ${repo} with "Actions: Read and write". Tap again to paste a new one.`);
      } else {
        say(`GitHub said ${res.status} - try again in a minute.`);
      }
      btn.disabled = false;
      showForget();
    });

    forget.addEventListener("click", (e) => {
      e.preventDefault();
      forgetKey();
      showForget();
      say("Key removed from this browser.");
    });

    showForget();
    refreshLock();
    setInterval(refreshLock, 60 * 1000); // unlock without a reload once the lock ends
  }

  window.PullButton = { mount, _weekId: weekId, _csvHead: csvHead };
})();
