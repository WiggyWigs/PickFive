// Every known pick slot, shown in Standings even before they've made a
// pick this season (or ever, for a brand-new roster member).
const ROSTER = ["Carlos/Patrick", "Greg", "Kevin", "Robert", "Billy"];

async function loadPicks() {
  try {
    const res = await fetch("data/picks.json", { cache: "no-store" });
    if (!res.ok) return null;
    return await res.json();
  } catch (e) {
    return null;
  }
}

function fmtPct(n) {
  if (n === null || n === undefined) return "—";
  return `${Math.round(n * 100)}%`;
}

function fmtSpread(v) {
  if (v === null || v === undefined) return "—";
  return v > 0 ? `+${v}` : `${v}`;
}

function fmtWeek(week) {
  if (week === null || week === undefined || week === "") return "—";
  return `Week ${week}`;
}

function latestYear(picks) {
  const years = picks.map((p) => p.year).filter((y) => y !== null && y !== undefined);
  return years.length ? Math.max(...years) : null;
}

function distinctYears(picks) {
  const years = [...new Set(picks.map((p) => p.year).filter((y) => y !== null && y !== undefined))];
  return years.sort((a, b) => b - a);
}

function buildStandings(picks) {
  const byPerson = {};
  for (const person of ROSTER) {
    byPerson[person] = {
      person,
      wins: 0,
      losses: 0,
      pending: 0,
      home: { w: 0, l: 0 },
      away: { w: 0, l: 0 },
      fav: { w: 0, l: 0 },
      dog: { w: 0, l: 0 },
    };
  }

  for (const p of picks) {
    // Picks from a person not in ROSTER still get their own bucket,
    // so a typo'd or newly-added name in the sheet doesn't silently vanish.
    if (!byPerson[p.person]) {
      byPerson[p.person] = {
        person: p.person,
        wins: 0,
        losses: 0,
        pending: 0,
        home: { w: 0, l: 0 },
        away: { w: 0, l: 0 },
        fav: { w: 0, l: 0 },
        dog: { w: 0, l: 0 },
      };
    }
    const row = byPerson[p.person];

    if (p.result !== "W" && p.result !== "L") {
      row.pending += 1;
      continue;
    }

    const win = p.result === "W";
    if (win) row.wins += 1;
    else row.losses += 1;

    if (p.home_away === "Home") row.home[win ? "w" : "l"] += 1;
    else if (p.home_away === "Away") row.away[win ? "w" : "l"] += 1;

    if (p.fav_dog === "Favorite") row.fav[win ? "w" : "l"] += 1;
    else if (p.fav_dog === "Underdog") row.dog[win ? "w" : "l"] += 1;
  }

  function pct(bucket) {
    const total = bucket.w + bucket.l;
    return total === 0 ? null : bucket.w / total;
  }

  return Object.values(byPerson).map((row) => ({
    person: row.person,
    wins: row.wins,
    losses: row.losses,
    win_pct: row.wins + row.losses === 0 ? null : row.wins / (row.wins + row.losses),
    home_pct: pct(row.home),
    away_pct: pct(row.away),
    fav_pct: pct(row.fav),
    dog_pct: pct(row.dog),
    pending: row.pending,
  }));
}

function renderStandings(standings, onPersonClick) {
  const tbody = document.querySelector("#standings-table tbody");
  let currentSort = { key: "win_pct", dir: -1 }; // default: best record first

  function draw(data) {
    tbody.innerHTML = "";
    for (const r of data) {
      const tr = document.createElement("tr");
      const nameClass = onPersonClick ? "team clickable-cell" : "team";
      tr.innerHTML = `
        <td class="${nameClass}">${r.person}</td>
        <td class="num">${r.wins}</td>
        <td class="num">${r.losses}</td>
        <td class="num">${fmtPct(r.win_pct)}</td>
        <td class="num">${fmtPct(r.home_pct)}</td>
        <td class="num">${fmtPct(r.away_pct)}</td>
        <td class="num">${fmtPct(r.fav_pct)}</td>
        <td class="num">${fmtPct(r.dog_pct)}</td>
        <td class="${r.pending === 0 ? 'blank' : 'num'}">${r.pending}</td>
      `;
      if (onPersonClick) {
        tr.querySelector(".clickable-cell").addEventListener("click", () => onPersonClick(r.person));
      }
      tbody.appendChild(tr);
    }
  }

  const sortedDefault = [...standings].sort((a, b) => {
    const av = a.win_pct === null ? -Infinity : a.win_pct;
    const bv = b.win_pct === null ? -Infinity : b.win_pct;
    return bv - av;
  });
  draw(sortedDefault);

  const winPctHeader = document.querySelector('#standings-table thead th[data-key="win_pct"]');
  if (winPctHeader) {
    const arrow = document.createElement("span");
    arrow.className = "arrow";
    arrow.textContent = "▼";
    winPctHeader.appendChild(arrow);
  }
  wireSort("#standings-table", standings, draw, currentSort);
}

function renderHistory(picks, showYear) {
  const tbody = document.querySelector("#history-table tbody");
  let currentSort = { key: "week_sort", dir: -1 };

  const rows = picks.map((p) => {
    const n = p.week !== null && p.week !== undefined && p.week !== "" ? Number(p.week) : NaN;
    return { ...p, week_sort: Number.isNaN(n) ? null : n };
  });

  function draw(data) {
    tbody.innerHTML = "";
    for (const r of data) {
      const tr = document.createElement("tr");
      const resultClass = r.result === "W" ? "result-w" : r.result === "L" ? "result-l" : "result-pending";
      tr.innerHTML = `
        ${showYear ? `<td class="num">${r.year ?? "—"}</td>` : ""}
        <td class="num">${fmtWeek(r.week)}</td>
        <td class="team">${r.person}</td>
        <td class="team">${r.team_picked ?? "—"}</td>
        <td class="${r.home_away ? 'num' : 'blank'}">${r.home_away ?? "—"}</td>
        <td class="${r.fav_dog ? 'num' : 'blank'}">${r.fav_dog ?? "—"}</td>
        <td class="${r.spread === null || r.spread === undefined ? 'blank' : 'num'}">${fmtSpread(r.spread)}</td>
        <td class="${resultClass}">${r.result ?? "Pending"}</td>
      `;
      tbody.appendChild(tr);
    }
  }

  draw([...rows].sort((a, b) => (b.week_sort ?? -Infinity) - (a.week_sort ?? -Infinity)));
  wireSort("#history-table", rows, draw, currentSort);
}

function wireSort(tableSelector, rows, draw, currentSort) {
  document.querySelectorAll(`${tableSelector} thead th`).forEach((th) => {
    th.addEventListener("click", () => {
      const key = th.dataset.key;
      const type = th.dataset.type;
      const dir = currentSort.key === key ? -currentSort.dir : 1;
      currentSort.key = key;
      currentSort.dir = dir;

      document.querySelectorAll(`${tableSelector} thead th .arrow`).forEach((a) => a.remove());
      const arrow = document.createElement("span");
      arrow.className = "arrow";
      arrow.textContent = dir === 1 ? "▲" : "▼";
      th.appendChild(arrow);

      const sorted = [...rows].sort((a, b) => {
        let av = a[key];
        let bv = b[key];
        if (type === "number") {
          av = av === null || av === undefined ? -Infinity : av;
          bv = bv === null || bv === undefined ? -Infinity : bv;
          return (av - bv) * dir;
        }
        av = (av ?? "").toString().toLowerCase();
        bv = (bv ?? "").toString().toLowerCase();
        return av.localeCompare(bv) * dir;
      });

      draw(sorted);
    });
  });
}
