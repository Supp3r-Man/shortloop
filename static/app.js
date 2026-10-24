"use strict";
const $ = (s) => document.querySelector(s);
const NS = "http://www.w3.org/2000/svg";
let csrf = "";
let registering = false;

// Tiny element builders. Everything goes through textContent/attributes, never innerHTML.
function h(tag, props = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) (k in e ? (e[k] = v) : e.setAttribute(k, v));
  e.append(...kids);
  return e;
}
function svg(tag, attrs = {}, ...kids) {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  e.append(...kids);
  return e;
}

const say = (text) => ($("#msg").textContent = text);

async function api(path, method = "GET", body) {
  const r = await fetch("/api" + path, {
    method,
    credentials: "same-origin",
    headers: { ...(body ? { "Content-Type": "application/json" } : {}), "X-CSRF-Token": csrf },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (r.status === 204) return null;
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Please check your input");
  return data;
}

function showAuth() {
  csrf = "";
  $("#auth").hidden = false;
  $("#app").hidden = true;
  $("#logout").hidden = true;
  $("#stats").hidden = true;
}

async function enter(d) {
  csrf = d.csrf;
  $("#auth").hidden = true;
  $("#app").hidden = false;
  $("#logout").hidden = false;
  say("");
  await load();
}

$("#toggle").onclick = () => {
  registering = !registering;
  $("#auth-title").textContent = registering ? "Register" : "Log in";
  $("#auth-submit").textContent = registering ? "Create account" : "Log in";
  $("#toggle").textContent = registering ? "Have an account? Log in" : "Need an account? Register";
  $("#auth-form").password.autocomplete = registering ? "new-password" : "current-password";
  say("");
};

$("#auth-form").onsubmit = async (e) => {
  e.preventDefault();
  try {
    await enter(await api(registering ? "/register" : "/login", "POST", Object.fromEntries(new FormData(e.target))));
    e.target.reset();
  } catch (err) {
    say(err.message);
  }
};

$("#logout").onclick = async () => {
  try { await api("/logout", "POST"); } catch { /* session already gone */ }
  showAuth();
};

$("#new").onsubmit = async (e) => {
  e.preventDefault();
  const f = Object.fromEntries(new FormData(e.target));
  const body = { url: f.url };
  if (f.slug) body.slug = f.slug;
  if (f.expires_at) body.expires_at = new Date(f.expires_at).toISOString();
  if (f.max_clicks) body.max_clicks = Number(f.max_clicks);
  try {
    const made = await api("/links", "POST", body);
    e.target.reset();
    await load();
    say(`Created /${made.slug}`);
  } catch (err) {
    say(err.message);
  }
};

async function load() {
  try {
    const links = await api("/links");
    $("#links").replaceChildren(...(links.length ? links.map(row) : [h("li", {}, "No links yet.")]));
  } catch (err) {
    say(err.message);
  }
}

function row(l) {
  const short = `${location.origin}/${l.slug}`;
  const meta = [
    `${l.click_count} click${l.click_count === 1 ? "" : "s"}`,
    l.max_clicks ? `limit ${l.max_clicks}` : "",
    l.expires_at ? `expires ${l.expires_at.replace("T", " ")} UTC` : "",
  ].filter(Boolean).join(" · ");

  const del = h("button", { className: "ghost", type: "button" }, "Delete");
  del.onclick = async () => {
    if (!del.dataset.armed) {
      del.dataset.armed = "1";
      del.textContent = "Really?";
      return;
    }
    try { await api(`/links/${l.id}`, "DELETE"); $("#stats").hidden = true; await load(); } catch (err) { say(err.message); }
  };
  const copy = h("button", { className: "ghost", type: "button" }, "Copy");
  copy.onclick = () => navigator.clipboard.writeText(short).then(() => say("Copied."), () => say("Copy failed."));
  const st = h("button", { type: "button" }, "Stats");
  st.onclick = () => stats(l, short).catch((err) => say(err.message));

  return h("li", {},
    h("div", { className: "main" },
      h("a", { href: short, target: "_blank", rel: "noopener" }, short.replace(/^https?:\/\//, "")),
      h("span", { className: "dest" }, l.url),
      h("small", {}, meta)),
    h("div", { className: "actions" },
      st, copy, h("a", { className: "btn ghost", href: short + "+", target: "_blank", rel: "noopener" }, "Preview"), del));
}

async function stats(l, short) {
  const s = await api(`/links/${l.id}/stats`);
  $("#stats-title").textContent = `Stats for /${l.slug}`;
  $("#total").textContent = `${s.total} clicks, ${s.visitors} unique visitors in the last ${s.days} days`;
  $("#chart").replaceChildren(chart(s.daily));
  bars("#refs", s.referrers);
  bars("#devices", s.devices);
  bars("#countries", s.countries);
  $("#qr").src = `/api/links/${l.id}/qr`;
  $("#qr").alt = `QR code for ${short}`;
  $("#stats").hidden = false;
  $("#stats").focus();
}
$("#close").onclick = () => ($("#stats").hidden = true);

function chart(daily) {
  const W = 640, H = 220, P = 34;
  const max = Math.max(1, ...daily.map((d) => d.n));
  const x = (i) => P + (i * (W - 2 * P)) / Math.max(1, daily.length - 1);
  const y = (n) => H - P - (n * (H - 2 * P)) / max;
  return svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": `Clicks per day, peak ${max}` },
    svg("line", { x1: P, y1: H - P, x2: W - P, y2: H - P, class: "axis" }),
    svg("line", { x1: P, y1: P, x2: P, y2: H - P, class: "axis" }),
    svg("text", { x: P - 6, y: P + 4, "text-anchor": "end" }, String(max)),
    svg("text", { x: P - 6, y: H - P + 4, "text-anchor": "end" }, "0"),
    svg("text", { x: P, y: H - 10 }, daily[0].day),
    svg("text", { x: W - P, y: H - 10, "text-anchor": "end" }, daily[daily.length - 1].day),
    svg("polyline", { points: daily.map((d, i) => `${x(i)},${y(d.n)}`).join(" "), class: "line" }),
    ...daily.map((d, i) => svg("circle", { cx: x(i), cy: y(d.n), r: 3, class: "dot" }, svg("title", {}, `${d.day}: ${d.n}`))));
}

function bars(sel, rows) {
  const el = $(sel);
  const max = Math.max(1, ...rows.map((r) => r.n));
  el.hidden = !rows.length;
  el.querySelector("ul").replaceChildren(...rows.map((r) =>
    h("li", {}, h("span", {}, r.name), h("progress", { max, value: r.n, "aria-label": `${r.name}: ${r.n}` }), h("b", {}, String(r.n)))));
}

api("/me").then(enter, showAuth);
