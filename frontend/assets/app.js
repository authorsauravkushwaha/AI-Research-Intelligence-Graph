/* NEXUS front end — vanilla ES modules + three.js (vendored).
   No framework, no CDN: everything runs from the FastAPI server. */

import * as THREE from "/vendor/three.module.js";
import { OrbitControls } from "/vendor/OrbitControls.js";

/* ------------------------------------------------------------------ util */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const num = (v, d = 2) => (v === null || v === undefined ? "—" : Number(v).toFixed(d));
const pct = (v) => `${Math.round((v ?? 0) * 100)}%`;

function toast(msg, ms = 3200) {
  const el = $("#toast");
  el.textContent = msg;
  el.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { el.hidden = true; }, ms);
}

async function api(path, { method = "GET", body, raw = false } = {}) {
  const opts = { method, headers: {} };
  if (body !== undefined) { opts.headers["content-type"] = "application/json"; opts.body = JSON.stringify(body); }
  const res = await fetch(path, opts);
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try { const j = await res.json(); detail = j.detail || j.error || detail; } catch { /* keep status text */ }
    throw new Error(detail);
  }
  return raw ? res.text() : res.json();
}

function renderMarkdown(md) {
  const lines = String(md || "").split("\n");
  let out = "", inList = false, inTable = false;
  const closeList = () => { if (inList) { out += "</ul>"; inList = false; } };
  const closeTable = () => { if (inTable) { out += "</tbody></table>"; inTable = false; } };
  const inline = (t) => esc(t)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\*([^*]+)\*/g, "<em>$1</em>")
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>');
  for (const line of lines) {
    if (/^\s*$/.test(line)) { closeList(); closeTable(); continue; }
    if (/^###\s/.test(line)) { closeList(); closeTable(); out += `<h3>${inline(line.slice(4))}</h3>`; continue; }
    if (/^##\s/.test(line)) { closeList(); closeTable(); out += `<h2>${inline(line.slice(3))}</h2>`; continue; }
    if (/^#\s/.test(line)) { closeList(); closeTable(); out += `<h1>${inline(line.slice(2))}</h1>`; continue; }
    if (/^>\s/.test(line)) { closeList(); closeTable(); out += `<blockquote>${inline(line.slice(2))}</blockquote>`; continue; }
    if (/^\|/.test(line)) {
      const cells = line.split("|").slice(1, -1).map((c) => c.trim());
      if (cells.every((c) => /^-+$/.test(c))) continue;
      if (!inTable) { out += "<table><tbody>"; inTable = true; }
      out += `<tr>${cells.map((c) => `<td>${inline(c)}</td>`).join("")}</tr>`;
      continue;
    }
    if (/^[-*]\s/.test(line)) { if (!inList) { out += "<ul>"; inList = true; } out += `<li>${inline(line.slice(2))}</li>`; continue; }
    closeList(); closeTable();
    out += `<p>${inline(line)}</p>`;
  }
  closeList(); closeTable();
  return out;
}

function barChart(canvas, data, { color = "#61e4ff", label = "" } = {}) {
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth || 480, h = Number(canvas.getAttribute("height")) || 180;
  canvas.width = w * dpr; canvas.height = h * dpr;
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  const entries = Object.entries(data || {}).sort((a, b) => Number(a[0]) - Number(b[0]));
  if (!entries.length) { ctx.fillStyle = "#8ba1bb"; ctx.font = "13px ui-monospace"; ctx.fillText("no data", 8, 24); return; }
  const max = Math.max(...entries.map(([, v]) => Number(v) || 0), 1);
  const pad = 26, bw = (w - pad - 8) / entries.length;
  ctx.strokeStyle = "rgba(120,160,200,.25)";
  ctx.beginPath(); ctx.moveTo(pad - 6, h - 22); ctx.lineTo(w - 4, h - 22); ctx.stroke();
  entries.forEach(([year, value], i) => {
    const bh = (h - 40) * (Number(value) / max);
    const x = pad + i * bw, y = h - 22 - bh;
    const grad = ctx.createLinearGradient(0, y, 0, h - 22);
    grad.addColorStop(0, color); grad.addColorStop(1, "rgba(97,228,255,.15)");
    ctx.fillStyle = grad;
    ctx.fillRect(x + bw * 0.15, y, Math.max(2, bw * 0.7), bh);
    if (entries.length <= 16 || i % 2 === 0) {
      ctx.fillStyle = "#8ba1bb"; ctx.font = "10px ui-monospace";
      ctx.fillText(String(year).slice(2), x + bw * 0.15, h - 8);
    }
  });
  ctx.fillStyle = "#8ba1bb"; ctx.font = "11px ui-monospace";
  ctx.fillText(`${max}${label ? " " + label : ""}`, 2, 12);
}

/* --------------------------------------------------------------- router */
const VIEWS = ["home", "explorer", "graph", "gaps", "papers", "agent", "report"];
function route() {
  const hash = (location.hash || "#home").replace("#", "");
  const view = VIEWS.includes(hash) ? hash : "home";
  $$(".view").forEach((el) => el.classList.toggle("active", el.id === `view-${view}`));
  $$("#nav a").forEach((a) => a.classList.toggle("active", a.dataset.view === view));
  if (view === "graph") { Graph.ensure(); Graph.planner.ensure(); }
  if (view === "home") Home.load();
  if (view === "explorer") Explorer.ensureDefaults();
  if (view === "papers") Papers.ensure();
  if (view === "agent") Agent.ensure();
  if (view === "report") Report.ensure();
}
window.addEventListener("hashchange", route);

/* ------------------------------------------------------------------ home */
const Home = {
  loaded: false,
  _years: {},
  async load(force = false) {
    if (this.loaded && !force) return;
    this.loaded = true;
    try {
      const [health, dash, timeline, services] = await Promise.all([
        api("/api/health"), api("/api/dashboard"), api("/api/timeline"), api("/api/services"),
      ]);
      this.services(services || {});
      this.health(health);
      this.cards(dash);
      this._years = timeline.papers_per_year;
      barChart($("#chart-years"), timeline.papers_per_year, { label: "papers" });
      $("#timeline-note").textContent = `${Object.values(timeline.papers_per_year).reduce((a, b) => a + b, 0)} papers`;
      this.communities(dash.communities || []);
      this.emerging(dash.emerging_topics || []);
      this.opportunities(dash.opportunities || []);
      this.bridges(dash.bridge_papers || []);
      this.conflicts(dash.conflicts || []);
      this.provenance(dash.provenance || {}, dash.engines || {});
    } catch (err) {
      toast(`Home failed: ${err.message}`);
    }
  },
  health(h) {
    const banner = $("#health-banner");
    const notices = h.notices || [];
    if (notices.length) {
      banner.hidden = false;
      banner.innerHTML = notices.map((n) => `<div>⚠ ${esc(n)}</div>`).join("");
    } else banner.hidden = true;
    $("#hero-engine").textContent = `engine: ${h.store.engine} · graph: ${h.counts.nodes} nodes / ${h.counts.edges} edges · analytics: ${Object.values(h.algorithms || {}).join(", ") || "n/a"}`;
  },
  cards(d) {
    const c = d.cards || {};
    const items = [
      ["papers", c.papers], ["topics", c.topics], ["methods", c.methods], ["communities", c.communities],
      ["candidate opportunities", c.potential_opportunities], ["claim tensions", c.conflicts],
      ["predicted links", c.predicted_links], ["datasets", c.datasets],
    ];
    $("#home-cards").innerHTML = items.map(([label, value]) =>
      `<div class="card"><div class="n">${value ?? "—"}</div><div class="l">${esc(label)}</div></div>`).join("");
  },
  communities(list) {
    $("#home-communities").innerHTML = list.slice(0, 6).map((c) => `
      <div class="rowitem linkish" data-community="${c.community_index}">
        <span class="t">${esc(c.name)}</span>
        <span class="m">${c.paper_count} papers · ${c.topic_count} topics · avg PageRank ${num(c.avg_pagerank, 5)} · ${(c.year_range || []).join("–") || "n/a"}</span>
      </div>`).join("") || '<p class="muted">no communities</p>';
    $$("#home-communities .rowitem").forEach((el) => el.addEventListener("click", () => {
      location.hash = "#graph";
      Graph.load({ seeds: null, focus: `community:${el.dataset.community}` });
    }));
  },
  emerging(list) {
    $("#home-emerging").innerHTML = list.slice(0, 8).map((t) => `
      <div class="rowitem"><span class="t">${esc(t.label)}</span>
      <span class="m">${t.papers_recent} recent (2023+) vs ${t.papers_earlier} earlier · ×${t.growth_ratio}</span></div>`).join("")
      || '<p class="muted">not enough data</p>';
  },
  opportunities(list) {
    $("#home-opportunities").innerHTML = list.slice(0, 5).map((o) => `
      <div class="rowitem linkish" data-opp="${esc(o.id)}">
        <span class="t">${esc(o.title)} <span class="pill violet">${num(o.opportunity_score, 1)}</span></span>
        <span class="m">${esc(o.trajectory?.status || "")} · confidence ${esc(o.confidence || "")} · ${o.bridge_papers?.length || 0} bridge papers</span>
      </div>`).join("") || '<p class="muted">run the gap finder for this scope</p>';
    $$("#home-opportunities .rowitem").forEach((el) => el.addEventListener("click", () => {
      GapFinder.highlight(el.dataset.opp);
      location.hash = "#gaps";
    }));
    GapFinder.cache = list;
  },
  bridges(list) {
    $("#home-bridges").innerHTML = list.slice(0, 8).map((p) => `
      <div class="rowitem"><span class="t">${esc(p.label)}</span>
      <span class="m">betweenness ${num(p.value, 5)} · degree ${p.degree} · ${p.year || "n/a"}</span></div>`).join("");
  },
  conflicts(list) {
    $("#home-conflicts").innerHTML = list.slice(0, 4).map((c) => `
      <div class="rowitem"><span class="t">${esc((c.text_a || "").slice(0, 120))}…</span>
      <span class="m">vs “${esc((c.text_b || "").slice(0, 110))}…” · score ${num(c.score, 2)} · ${esc(c.kind)} · hypothesis</span></div>`).join("")
      || '<p class="muted">no tensions detected in this scope</p>';
  },
  services(payload) {
    const list = payload.sidecars || [];
    if (!list.length) return;
    $("#home-services").innerHTML = list.map((s) => `
      <div class="rowitem">
        <span class="t">${esc(s.language)} <span class="muted tiny">${esc(s.role)}</span></span>
        <span class="m">${s.reachable
          ? `<span class="pill green">live</span> ${esc(s.engine)} · ${esc(s.url)}`
          : `${s.configured ? '<span class="pill">offline</span>' : '<span class="pill">not configured</span>'} → python: ${esc(s.fallback)}`}</span>
      </div>`).join("");
    const note = $("#services-note");
    if (note) note.textContent = `${payload.summary}. Each engine has a Python fallback behind the same interface — a response always names the engine that answered.`;
  },
  provenance(p, engines) {
    $("#home-provenance").innerHTML = `
      <div><strong>${esc(p.notice || "")}</strong></div>
      <div>ids &amp; titles: ${esc(p.ids_and_titles || "—")}</div>
      <div>summaries: ${esc(p.summaries || "—")}</div>
      <div>authors: ${esc(p.authors || "—")}</div>
      <div>citations: ${esc(p.citations || "—")}</div>
      <div>algorithms: ${Object.entries(engines.analytics || {}).map(([k, v]) => `${esc(k)}=${esc(v)}`).join(" · ")}</div>`;
    $("#footer-safety").textContent = "prototype heuristic · AI-generated hypotheses · verify at the sources";
  },
};

/* ------------------------------------------------------------- explorer */
const Explorer = {
  ready: false,
  ensureDefaults() {
    if (this.ready) return;
    this.ready = true;
    $("#explorer-run").addEventListener("click", () => this.run());
    this.run();
  },
  async run() {
    const topic = $("#explorer-topic").value.trim();
    const field = $("#explorer-field").value.trim();
    const ymin = $("#explorer-year-min").value || "";
    const ymax = $("#explorer-year-max").value || "";
    const qs = new URLSearchParams();
    if (topic) qs.set("topic", topic);
    if (field) qs.set("field", field);
    if (ymin) qs.set("year_min", ymin);
    if (ymax) qs.set("year_max", ymax);
    $("#explorer-status").textContent = "analysing…";
    try {
      const data = await api(`/api/explorer?${qs}`);
      const [tl] = await Promise.all([api(`/api/timeline?${topic ? "topic=" + encodeURIComponent(topic) : ""}`)]);
      barChart($("#chart-explorer-years"), tl.papers_per_year, { color: "#a78bfa", label: "papers" });
      const c = data.counts;
      const r = data.recency || {};
      $("#explorer-metrics").innerHTML = [
        ["papers", c.papers], ["authors", c.authors], ["topics", c.topics], ["methods", c.methods],
        ["datasets", c.datasets], ["communities", c.communities],
        ["papers 2023+", `${r.papers_since_2023 ?? "—"} (${pct(r.share_since_2023)})`],
        ["year range", `${r.oldest_year ?? "—"}–${r.newest_year ?? "—"}`],
        ["resolution", (data.scope?.resolution?.strategy || "all-papers")],
        ["matched topics", (data.scope?.resolution?.matched_topics || []).slice(0, 6).join(", ") || "—"],
      ].map(([k, v]) => `<div class="k">${esc(k)}</div><div class="v">${esc(v)}</div>`).join("");
      const list = (items, fmt) => items.length ? items.map(fmt).join("") : '<p class="muted">none</p>';
      $("#explorer-papers").innerHTML = list(data.top_papers, (p) =>
        `<div class="rowitem linkish" data-paper="${esc(p.id)}"><span class="t">${esc(p.label)}</span><span class="m">${num(p.value, 5)} · ${p.year || "n/a"}</span></div>`);
      $("#explorer-topics").innerHTML = list(data.top_topics, (t) =>
        `<div class="rowitem"><span class="t">${esc(t.label)}</span><span class="m">${num(t.value, 5)} · degree ${t.degree}</span></div>`);
      $("#explorer-methods").innerHTML = list(data.top_methods, (m) =>
        `<div class="rowitem"><span class="t">${esc(m.label)}</span><span class="m">degree ${m.degree}</span></div>`);
      $("#explorer-communities").innerHTML = list(data.communities, (cm) => `
        <div class="rowitem linkish" data-community="${cm.community_index}">
          <span class="t">${esc(cm.name)}</span>
          <span class="m">${cm.paper_count} papers · topics: ${(cm.top_topics || []).slice(0, 4).join(", ")}</span>
        </div>`);
      $$("#explorer-papers .rowitem").forEach((el) => el.addEventListener("click", () => {
        location.hash = "#papers"; Papers.open(el.dataset.paper);
      }));
      $$("#explorer-communities .rowitem").forEach((el) => el.addEventListener("click", () => {
        location.hash = "#graph"; Graph.load({ focus: `community:${el.dataset.community}` });
      }));
      $("#explorer-status").textContent = `scope: ${c.papers} papers · resolved via ${data.scope?.resolution?.strategy || "all-papers"}`;
    } catch (err) {
      $("#explorer-status").textContent = `failed: ${err.message}`;
    }
  },
};

/* ---------------------------------------------------------------- graph */
const Graph = {
  ready: false,
  legend: null,
  init: null,
  nodes: [], edges: [], mesh: {}, pickable: [], selected: null, focus: null,
  ensure() { if (!this.ready) { this.ready = true; try { this.init = this.build(); } catch (e) { toast(`3D view failed: ${e.message}`); } } },
  build() {
    const host = $("#graph-canvas");
    const scene = new THREE.Scene();
    scene.fog = new THREE.FogExp2(0x05080e, 0.0022);
    const camera = new THREE.PerspectiveCamera(55, host.clientWidth / host.clientHeight, 0.1, 4000);
    camera.position.set(0, 40, 190);
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
    renderer.setSize(host.clientWidth, host.clientHeight);
    host.appendChild(renderer.domElement);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true; controls.dampingFactor = 0.08; controls.autoRotate = true; controls.autoRotateSpeed = 0.35;

    scene.add(new THREE.AmbientLight(0xffffff, 0.65));
    const key = new THREE.PointLight(0x88ddff, 1.4, 1400); key.position.set(140, 180, 120); scene.add(key);
    const rim = new THREE.PointLight(0xb99cff, 1.0, 1200); rim.position.set(-160, -120, -140); scene.add(rim);
    for (let i = 0; i < 900; i++) {
      const star = new THREE.Mesh(new THREE.SphereGeometry(0.5, 4, 4), new THREE.MeshBasicMaterial({ color: 0x2a3d55 }));
      star.position.set((Math.random() - 0.5) * 2200, (Math.random() - 0.5) * 2200, (Math.random() - 0.5) * 2200);
      scene.add(star);
    }

    const group = new THREE.Group(); scene.add(group);
    const raycaster = new THREE.Raycaster(); const pointer = new THREE.Vector2();
    let down = null;
    renderer.domElement.addEventListener("pointerdown", (e) => { down = { x: e.clientX, y: e.clientY }; });
    renderer.domElement.addEventListener("pointerup", (e) => {
      if (!down || Math.abs(e.clientX - down.x) + Math.abs(e.clientY - down.y) > 6) return;
      const rect = renderer.domElement.getBoundingClientRect();
      pointer.x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
      pointer.y = -((e.clientY - rect.top) / rect.height) * 2 + 1;
      raycaster.setFromCamera(pointer, camera);
      const hits = raycaster.intersectObjects(this.pickable, false);
      if (hits.length) this.select(hits[0].object.userData.id);
    });
    window.addEventListener("resize", () => {
      if (!host.clientWidth) return;
      camera.aspect = host.clientWidth / host.clientHeight; camera.updateProjectionMatrix();
      renderer.setSize(host.clientWidth, host.clientHeight);
    });
    const tick = () => { controls.update(); renderer.render(scene, camera); requestAnimationFrame(tick); };
    tick();
    $("#graph-load").addEventListener("click", () => this.load({}));
    $("#graph-reset").addEventListener("click", () => { camera.position.set(0, 40, 190); controls.target.set(0, 0, 0); });
    $("#graph-focus").addEventListener("click", () => this.load({ query: $("#graph-search").value.trim() }));
    $("#graph-expand").addEventListener("click", () => this.expandSelected());
    $("#graph-search").addEventListener("keydown", (e) => { if (e.key === "Enter") this.load({ query: e.target.value.trim() }); });
    return { scene, camera, renderer, controls, group, raycaster };
  },
  color(label, colors) { return colors?.[label] || "#7dd3fc"; },

  planner: {
    ready: false,
    ensure() {
      if (this.ready) return;
      this.ready = true;
      const run = () => this.run();
      $("#plan-run").addEventListener("click", run);
      $("#plan-dsl").addEventListener("keydown", (e) => { if (e.key === "Enter") run(); });
    },
    async run() {
      const dsl = $("#plan-dsl").value.trim();
      if (!dsl) return;
      $("#plan-engine").textContent = "planning…";
      try {
        const p = await api("/api/plan", { method: "POST", body: { query: dsl } });
        const badge = p.source === "sidecar" ? "Kotlin planner (JVM sidecar)" : "Python planner (sidecar offline)";
        $("#plan-engine").innerHTML = `<span class="pill ${p.source === "sidecar" ? "green" : "amber"}">${esc(p.engine)}</span> ${esc(badge)}`;
        $("#plan-cypher").textContent = p.cypher;
        const params = Object.entries(p.params || {}).map(([k, v]) => `$${k} = ${JSON.stringify(v)}`).join(" · ");
        $("#plan-detail").innerHTML = `
          <div class="muted">${esc(params || "no bind parameters")}</div>
          <div>${(p.explanation || []).map((w) => `<div>• ${esc(w)}</div>`).join("")}</div>
          <div class="muted">cost: ${esc(p.cost?.strategy || "")} · seeds ${p.cost?.seeds} · ~${p.cost?.estimated_nodes_visited} nodes (budget ${p.cost?.budget})</div>
          <div class="safety">${esc(p.note || "")}</div>`;
      } catch (err) {
        $("#plan-engine").textContent = `rejected: ${err.message}`;
        $("#plan-cypher").textContent = "";
        $("#plan-detail").innerHTML = "";
      }
    },
  },
  async load({ query = null, seeds = null, focus = null } = {}) {
    if (!this.ready) this.ensure();
    if (!this.init) return;
    const nodeTypes = $$("#graph-node-types option:checked").map((o) => o.value);
    const relTypes = $$("#graph-rel-types option:checked").map((o) => o.value);
    $("#graph-stats").textContent = "loading…";
    try {
      const body = {
        query: query || null,
        depth: query || focus ? 1 : 1,
        node_types: nodeTypes.length ? nodeTypes : null,
        rel_types: relTypes.length ? relTypes : null,
        include_predicted: $("#graph-predicted").checked,
        focus: focus || (query ? null : $("#graph-search").value.trim() || null),
        max_nodes: 280, max_edges: 900,
      };
      const data = await api("/api/graph", { method: "POST", body });
      this.legend = data.legend;
      this.render(data, focus);
      $("#graph-stats").textContent = `${data.nodes.length} nodes · ${data.edges.length} edges${data.truncated ? " · truncated (expand on demand)" : ""} · click a node for its metrics`;
      $("#graph-legend").innerHTML = Object.entries(data.legend?.node_colors || {}).slice(0, 8)
        .map(([k, v]) => `<span><i style="background:${v}"></i>${k}</span>`).join("")
        + `<span><i style="background:#f472b6"></i>predicted</span>`;
    } catch (err) {
      $("#graph-stats").textContent = `failed: ${err.message}`;
    }
  },

  render(data, focus) {
    const { group, controls, camera } = this.init;
    group.clear(); this.pickable = []; this.mesh = {};
    const center = new THREE.Vector3();
    const nodes = data.nodes.filter((n) => n.id);
    const byId = Object.fromEntries(nodes.map((n) => [n.id, n]));
    const radius = Math.max(60, Math.cbrt(nodes.length) * 26);

    const positions = new Map();
    nodes.forEach((n, i) => {
      const phi = Math.acos(1 - 2 * ((i + 0.5) / nodes.length));
      const theta = Math.PI * (1 + Math.sqrt(5)) * i;
      positions.set(n.id, new THREE.Vector3(
        radius * Math.sin(phi) * Math.cos(theta),
        radius * Math.cos(phi) * 0.7,
        radius * Math.sin(phi) * Math.sin(theta),
      ));
    });

    const edges = data.edges.filter((e) => byId[e.source ?? e.src] && byId[e.target ?? e.dst]);
    const pr = nodes.map((n) => n.pagerank ?? n.value ?? 0);
    const maxPr = Math.max(...pr, 0.0001);

    for (let iter = 0; iter < 160; iter++) {
      const disp = new Map(nodes.map((n) => [n.id, new THREE.Vector3()]));
      for (let i = 0; i < nodes.length; i++) {
        for (let j = i + 1; j < nodes.length; j++) {
          const a = nodes[i].id, b = nodes[j].id;
          const d = positions.get(a).distanceTo(positions.get(b)) || 0.01;
          if (d > radius) continue;
          const push = (260 / (d * d)) * (iter / 160);
          const dir = positions.get(a).clone().sub(positions.get(b)).normalize();
          disp.get(a).addScaledVector(dir, push);
          disp.get(b).addScaledVector(dir, -push);
        }
      }
      edges.forEach((e) => {
        const a = e.source ?? e.src, b = e.target ?? e.dst;
        const d = positions.get(a).distanceTo(positions.get(b));
        const pull = (d - 34) * 0.02;
        const dir = positions.get(b).clone().sub(positions.get(a)).normalize();
        disp.get(a).addScaledVector(dir, pull);
        disp.get(b).addScaledVector(dir, -pull);
      });
      nodes.forEach((n) => {
        const p = positions.get(n.id);
        p.add(disp.get(n.id).multiplyScalar(0.5));
        p.addScaledVector(p.clone().normalize(), -p.length() * 0.002);
        center.add(p);
      });
    }
    center.multiplyScalar(1 / Math.max(1, nodes.length));

    const sphere = new THREE.SphereGeometry(1, 18, 18);
    const colors = data.legend?.node_colors || {};
    nodes.forEach((n) => {
      const size = 1.6 + 6.4 * Math.sqrt(((n.pagerank ?? n.value ?? 0) / maxPr) || 0.02);
      const material = new THREE.MeshStandardMaterial({
        color: new THREE.Color(this.color(n.type, colors)),
        emissive: new THREE.Color(this.color(n.type, colors)).multiplyScalar(0.35),
        roughness: 0.45, metalness: 0.15,
      });
      const mesh = new THREE.Mesh(sphere, material);
      mesh.scale.setScalar(size);
      mesh.position.copy(positions.get(n.id)).sub(center);
      mesh.userData = { id: n.id, label: n.label, type: n.type };
      group.add(mesh); this.pickable.push(mesh); this.mesh[n.id] = mesh;
    });

    const predicted = new Set((data.legend?.predicted_rels || []));
    const linePositions = [], lineColors = [];
    edges.forEach((e) => {
      const a = positions.get(e.source ?? e.src).clone().sub(center);
      const b = positions.get(e.target ?? e.dst).clone().sub(center);
      linePositions.push(a.x, a.y, a.z, b.x, b.y, b.z);
      const isPred = predicted.has(e.type) || e.predicted;
      const c = isPred ? new THREE.Color("#f472b6") : e.type === "BELONGS_TO" || e.type === "STUDIES"
        ? new THREE.Color("#2b4a63") : new THREE.Color("#3f6a8c");
      lineColors.push(c.r, c.g, c.b, c.r, c.g, c.b);
    });
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.Float32BufferAttribute(linePositions, 3));
    geo.setAttribute("color", new THREE.Float32BufferAttribute(lineColors, 3));
    group.add(new THREE.LineSegments(geo, new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, opacity: 0.55 })));

    const focusId = focus && this.mesh[focus] ? focus : (data.focus?.id && this.mesh[data.focus.id] ? data.focus.id : null);
    if (focusId) {
      const target = this.mesh[focusId].position;
      controls.target.copy(target);
      camera.position.copy(target.clone().add(new THREE.Vector3(0, 30, 95)));
    }
    this.nodes = nodes; this.edges = edges;
    if (focusId) this.select(focusId);
  },

  select(id) {
    this.selected = id;
    this.init.controls.autoRotate = false;
    const node = this.nodes.find((n) => n.id === id);
    if (!node) return;
    const esc2 = (s) => esc(s);
    $("#graph-detail").innerHTML = `
      <h3>${esc2(node.label)}</h3>
      <div><span class="pill cyan">${esc2(node.type)}</span>${node.year ? `<span class="pill">${node.year}</span>` : ""}
      ${node.pagerank !== undefined ? `<span class="pill violet">PageRank ${num(node.pagerank, 5)}</span>` : ""}
      ${node.url ? `<a class="pill cyan" href="${esc(node.url)}" target="_blank" rel="noreferrer">source ↗</a>` : ""}</div>
      <p class="muted tiny" id="graph-detail-body">loading metrics…</p>`;
    api(`/api/nodes/${encodeURIComponent(id)}`).then((d) => {
      const metrics = Object.entries(d.metrics || {}).map(([k, v]) => `${k}=${typeof v === "number" ? num(v, 5) : esc(v)}`).join(" · ");
      $("#graph-detail-body").innerHTML = `
        ${metrics ? `<div class="muted tiny">${metrics}</div>` : ""}
        ${(d.metrics_explained || []).slice(0, 4).map((m) => `<div class="tiny">• ${esc(m.explanation || m.text || JSON.stringify(m))}</div>`).join("")}
        ${d.properties?.url ? `<p class="tiny"><a href="${esc(d.properties.url)}" target="_blank" rel="noreferrer">open on arXiv ↗</a></p>` : ""}
        <p class="tiny muted">${Object.entries(d.neighbour_counts || {}).map(([k, v]) => `${k}:${v}`).join(" · ")}</p>
        <button class="btn small" id="graph-detail-expand">Expand from here</button>`;
      $("#graph-detail-expand")?.addEventListener("click", () => this.expandSelected());
    }).catch((err) => { $("#graph-detail-body").textContent = err.message; });
  },

  async expandSelected() {
    if (!this.selected) { toast("Select a node in the graph first"); return; }
    try {
      const data = await api("/api/graph/expand", { method: "POST", body: { node_ids: [this.selected], limit: 40 } });
      const merged = {
        nodes: [...this.nodes, ...data.nodes.filter((n) => !this.nodes.some((x) => x.id === n.id))],
        edges: [...this.edges, ...data.edges.filter((e) => !this.edges.some((x) => (x.source ?? x.src) === (e.source ?? e.src) && (x.target ?? x.dst) === (e.target ?? e.dst) && x.type === e.type))],
        legend: null,
      };
      merged.legend = this.legend || null;
      this.render(merged, null);
      $("#graph-stats").textContent = `${merged.nodes.length} nodes after expansion · click a node for metrics`;
    } catch (err) { toast(`expand failed: ${err.message}`); }
  },
};

/* ------------------------------------------------------------ gap finder */
const GapFinder = { cache: [], highlighted: null,
  highlight(id) { this.highlighted = id; },
  async run() {
    const body = {
      topic: $("#gaps-topic").value.trim() || null,
      year_min: Number($("#gaps-year-min").value) || null,
      year_max: Number($("#gaps-year-max").value) || null,
      top_k: Number($("#gaps-topk").value) || 5,
    };
    $("#gaps-status").textContent = "running the gap engine over the graph…";
    $("#gaps-results").innerHTML = "";
    try {
      const d = await api("/api/gaps", { method: "POST", body });
      this.cache = d.opportunities || [];
      $("#gaps-safety").hidden = false;
      $("#gaps-safety").textContent = d.safety_notice;
      $("#gaps-overview").hidden = false;
      $("#gaps-formula").innerHTML = Object.entries(d.score_formula || {}).map(([k, v]) =>
        `<div class="rowitem"><span class="t">${esc(k)}</span><span class="m">${esc(v)} weight · prototype heuristic</span></div>`).join("");
      $("#gaps-scope").innerHTML = Object.entries(d.scope || {}).map(([k, v]) =>
        `<div class="k">${esc(k)}</div><div class="v">${esc(Array.isArray(v) ? v.join(", ") : JSON.stringify(v))}</div>`).join("")
        + `<div class="k">candidates</div><div class="v">${d.score_distribution?.candidates_considered ?? "—"} pairs scored · ${d.score_distribution?.unique_candidates ?? "—"} unique · max ${num(d.score_distribution?.max, 1)} · median ${num(d.score_distribution?.median, 1)}</div>`;
      $("#gaps-method").innerHTML = (d.methodology || []).map((m) => `<div>• ${esc(m)}</div>`).join("");
      const filtered = d.filtered_pairs || [];
      $("#gaps-status").textContent = `${(d.opportunities || []).length} candidates shown (of ${d.score_distribution?.candidates_considered ?? 0} pairs scored) in ${d.scope?.papers ?? 0} scoped papers`
        + (filtered.length ? ` · ${filtered.length} pair(s) filtered out` : "");
      const cards = (d.opportunities || []).map((o, i) => this.card(o, i === 0)).join("");
      $("#gaps-results").innerHTML = cards || this.emptyState(d, filtered);
      $$("#gaps-results .opp-head").forEach((el) => el.addEventListener("click", () => el.parentElement.querySelector(".opp-body").classList.toggle("hidden")));
      if (this.highlighted) {
        const el = $(`[data-opp-card="${this.highlighted}"]`);
        if (el) el.scrollIntoView({ block: "center" });
      }
    } catch (err) {
      $("#gaps-status").textContent = `failed: ${err.message}`;
    }
  },
  emptyState(d, filtered) {
    /* An empty result is still an answer: show the scope, the reason, and the pairs
       the engine refused to count — a silently lowered bar would be worse. */
    const rows = filtered.map((f) => `
      <div class="evidence"><strong>${esc(f.a)}</strong> <span class="muted">×</span> <strong>${esc(f.b)}</strong>
        <div class="why">${esc(f.reason)}</div></div>`).join("");
    return `
      <div class="panel">
        <h3>No candidate pairs in this scope</h3>
        <p class="muted small">${esc(d.reason || "No pair of concept clusters was separated enough to score.")}</p>
        ${rows ? `<h4>The excluded pairs, with the reason</h4>${rows}` : ""}
        <p class="small">NEXUS only lists a gap when two concept clusters are genuinely separated. Instead of
        lowering that bar, it shows you what it refused to count. Try a broader topic, a wider year range, or a
        lower minimum-paper count.</p>
        <div class="small muted">Scope: ${d.scope?.papers ?? 0} papers · ${(d.clusters || []).length} concept clusters
        · score model: ${esc(d.score_label || "NEXUS Opportunity Score")}</div>
      </div>`;
  },
  card(o, open) {
    const bars = (o.score_components || []).map((c) => `
      <div class="bar-row" title="${esc(c.explanation || "")}">
        <span>${esc(c.label)} <span class="muted tiny">${esc(c.weight)}</span></span>
        <span class="bar"><i style="width:${Math.max(2, Math.min(100, c.value))}%"></i></span>
        <span class="val">${num(c.value, 0)}</span>
      </div>`).join("");
    const hist = o.trajectory?.bridge_papers_by_year || {};
    const maxHist = Math.max(1, ...Object.values(hist));
    const traj = Object.keys(hist).length ? `<div class="trajectory">${Object.entries(hist).sort().map(([y, v]) =>
      `<i style="height:${Math.max(6, (v / maxHist) * 44)}px" title="${y}: ${v} bridge paper(s)"></i>`).join("")}</div>` : "";
    const evidence = (o.evidence_papers || []).map((p) => `
      <div class="evidence">• ${esc(p.title)} <span class="muted">(${p.year || "n/a"})</span>
        <div class="why">${esc(p.reason || "")}</div></div>`).join("");
    const conflicts = (o.conflicts || []).map((c) => `
      <div class="evidence">⚠ ${esc((c.text_a || "").slice(0, 150))} <span class="muted">vs</span> ${esc((c.text_b || "").slice(0, 150))}
      <div class="why">potential contradiction (score ${num(c.score, 2)}) — AI-detected hypothesis</div></div>`).join("");
    return `
      <article class="opp" data-opp-card="${esc(o.id)}">
        <div class="opp-head">
          <div>
            <h3>#${o.rank ?? "–"} ${esc(o.title)}</h3>
            <div class="muted small">
              <span class="pill violet">${esc(o.trajectory?.status || "")}</span>
              <span class="pill">${esc(o.granularity || "")}</span>
              <span class="pill">confidence ${esc(o.confidence || "")}</span>
              <span class="pill">${o.bridge_papers?.length || 0} bridge paper(s)</span>
            </div>
          </div>
          <div style="text-align:right">
            <div class="opp-score">${num(o.opportunity_score, 1)}</div>
            <div class="muted tiny">NEXUS Opportunity Score<br/>prototype heuristic</div>
          </div>
        </div>
        <div class="opp-body ${open ? "" : "hidden"}">
          <div><strong>Hypothesis.</strong> ${esc(o.hypothesis)}</div>
          <div><strong>Why it scored this way.</strong>
            <ul class="small">${(o.why || []).map((w) => `<li>${esc(w)}</li>`).join("")}</ul>
          </div>
          <div class="bars">${bars}</div>
          <div class="small"><strong>Trajectory:</strong> ${esc(o.trajectory?.note || "")}
            <span class="muted">(${o.trajectory?.recent_bridges ?? 0} bridge papers since 2024, ${o.trajectory?.older_bridges ?? 0} earlier)</span>
            ${traj}
          </div>
          <div><strong>Candidate experiment.</strong> ${esc(o.experiment)}</div>
          <div class="small"><strong>Confidence basis.</strong> ${esc(o.confidence_reason || "")}</div>
          <details><summary class="linkish">Evidence papers (${(o.evidence_papers || []).length})</summary><div class="evidence-list">${evidence}</div></details>
          ${conflicts ? `<details><summary class="linkish">Claim tensions inside these clusters (${(o.conflicts || []).length})</summary><div class="evidence-list">${conflicts}</div></details>` : ""}
          <div class="safety tiny">${esc(o.labels?.finding ? o.labels.finding + " — " : "")}AI-generated hypothesis based on the analyzed corpus; independently validate before acting on it. NEXUS never claims a topic is unstudied.</div>
        </div>
      </article>`;
  },
};

/* ----------------------------------------------------------- papers view */
const Papers = {
  offset: 0, limit: 25, total: 0, ready: false,
  ensure() {
    if (this.ready) return;
    this.ready = true;
    $("#papers-run").addEventListener("click", () => { this.offset = 0; this.load(); });
    $("#papers-q").addEventListener("keydown", (e) => { if (e.key === "Enter") { this.offset = 0; this.load(); } });
    $("#papers-prev").addEventListener("click", () => { this.offset = Math.max(0, this.offset - this.limit); this.load(); });
    $("#papers-next").addEventListener("click", () => { this.offset += this.limit; this.load(); });
    this.load();
  },
  async load() {
    const qs = new URLSearchParams({ limit: this.limit, offset: this.offset, sort: $("#papers-sort").value });
    const q = $("#papers-q").value.trim(); if (q) qs.set("q", q);
    const from = $("#papers-from").value; if (from) qs.set("year_min", from);
    const to = $("#papers-to").value; if (to) qs.set("year_max", to);
    $("#papers-status").textContent = "loading…";
    try {
      const data = await api(`/api/papers?${qs}`);
      this.total = data.total;
      $("#papers-table tbody").innerHTML = (data.items || []).map((p, i) => `
        <tr data-paper="${esc(p.id)}">
          <td class="num">${this.offset + i + 1}</td>
          <td>${esc(p.label)}${p.url ? ` <a href="${esc(p.url)}" target="_blank" rel="noreferrer">↗</a>` : ""}</td>
          <td class="num">${p.year || "—"}</td>
          <td>${esc(p.field || "—")}</td>
          <td class="num">${num(p.pagerank, 5)}</td>
          <td class="num">${num(p.betweenness, 5)}</td>
          <td class="num">${p.degree}</td>
        </tr>`).join("");
      $$("#papers-table tbody tr").forEach((tr) => tr.addEventListener("click", () => this.open(tr.dataset.paper)));
      $("#papers-page").textContent = `${this.offset + 1}–${Math.min(this.offset + this.limit, this.total)} of ${this.total}`;
      $("#papers-status").textContent = `sorted by ${data.sort || "pagerank"} · click a row for metrics, evidence and claims`;
    } catch (err) { $("#papers-status").textContent = `failed: ${err.message}`; }
  },
  async open(id) {
    const panel = $("#paper-detail");
    panel.hidden = false;
    panel.innerHTML = `<p class="muted">loading ${esc(id)}…</p>`;
    try {
      const d = await api(`/api/papers/${encodeURIComponent(id)}`);
      const claims = (d.claims || []).map((c) => `<div class="evidence">• ${esc(c.text)} <span class="pill">${esc(c.stance)}</span></div>`).join("");
      const similar = (d.similar_papers || []).slice(0, 6).map((s) => `<div class="evidence">• ${esc(s.name)} <span class="muted tiny">cosine ${num(s.similarity, 3)}</span></div>`).join("");
      const preds = (d.predicted_links || []).slice(0, 5).map((p) => `<div class="evidence tiny">↝ hypothesis link (score ${num(p.score ?? p.adamic_adar, 3)})</div>`).join("");
      panel.innerHTML = `
        <h3>${esc(d.label)} ${d.properties?.url ? `<a class="pill cyan" href="${esc(d.properties.url)}" target="_blank" rel="noreferrer">arXiv ↗</a>` : ""}</h3>
        <div class="muted small">${esc(d.properties?.field || "")} · ${d.properties?.year || ""} · ${(d.authors || []).join(", ") || "authorship not collected"}</div>
        <div class="kv">${Object.entries(d.metrics || {}).map(([k, v]) => `<div class="k">${esc(k)}</div><div class="v">${typeof v === "number" ? num(v, 5) : esc(v)}</div>`).join("")}</div>
        <p class="small">${esc(d.properties?.abstract || "No summary collected for this record — open the arXiv page for the abstract.")}</p>
        ${(d.metrics_explained || []).length ? `<div class="small muted">${d.metrics_explained.map((m) => `• ${esc(m.explanation || m.text || "")}`).join("<br/>")}</div>` : ""}
        <div class="grid three">
          <div><h3 class="small">Topics</h3><div class="small muted">${(d.topics || []).join(", ") || "—"}</div></div>
          <div><h3 class="small">Methods</h3><div class="small muted">${(d.methods || []).join(", ") || "—"}</div></div>
          <div><h3 class="small">Datasets</h3><div class="small muted">${(d.datasets || []).join(", ") || "—"}</div></div>
        </div>
        ${claims ? `<h3 class="small">Claims in the corpus</h3>${claims}` : ""}
        ${similar ? `<h3 class="small">Closest papers (embedding similarity)</h3>${similar}` : ""}
        ${preds ? `<h3 class="small">Predicted connections (hypotheses)</h3>${preds}` : ""}
        <p class="tiny muted">provenance: ${esc(d.provenance?.record || "")} · summary: ${esc(d.provenance?.summary || "")} · authors: ${esc(d.provenance?.authors || "")}</p>
        <button class="btn small" id="paper-graph">Show in 3D graph</button>`;
      $("#paper-graph").addEventListener("click", () => { location.hash = "#graph"; Graph.load({ seeds: [id], depth: 2 }); });
      panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
    } catch (err) { panel.innerHTML = `<p class="muted">failed: ${esc(err.message)}</p>`; }
  },
};

/* ------------------------------------------------------------ agent view */
const Agent = {
  ready: false,
  suggestions: [
    "What are the most important research gaps in AI agents?",
    "Which papers connect agent memory and multi-agent coordination?",
    "What contradictory claims exist in the corpus?",
    "Which methods are used across multiple research fields?",
    "What experiment could investigate this?",
    "Which papers are the most important?",
  ],
  ensure() {
    if (this.ready) return;
    this.ready = true;
    $("#agent-suggestions").innerHTML = this.suggestions.map((s) => `<span class="chip">${esc(s)}</span>`).join("");
    $$("#agent-suggestions .chip").forEach((c) => c.addEventListener("click", () => { $("#agent-q").value = c.textContent; this.run(); }));
    $("#agent-run").addEventListener("click", () => this.run());
    $("#agent-q").addEventListener("keydown", (e) => { if (e.key === "Enter") this.run(); });
  },
  async run() {
    const question = $("#agent-q").value.trim();
    if (!question) { toast("Type a question first"); return; }
    $("#agent-answer").innerHTML = "";
    $("#agent-stages").innerHTML = "";
    try {
      const res = await fetch("/api/agent/stream", {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ question, depth: 2, top_k: 12 }),
      });
      if (!res.ok || !res.body) throw new Error(`stream failed (${res.status})`);
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split("\n\n");
        buffer = parts.pop();
        for (const part of parts) {
          const lines = part.split("\n");
          const event = (lines[0] || "").replace("event: ", "");
          const dataLine = lines.find((l) => l.startsWith("data: "));
          if (!dataLine) continue;
          let payload; try { payload = JSON.parse(dataLine.slice(6)); } catch { continue; }
          if (event === "answer") { this.answer(payload); }
          else if (event === "error") { toast(`agent error: ${payload.message || "unknown"}`); }
          else this.stage(event, payload);
        }
      }
    } catch (err) { toast(`agent failed: ${err.message}`); }
  },
  stage(name, detail) {
    const el = document.createElement("div");
    el.className = "stage done";
    const d = detail || {};
    const info = name === "intent" ? `intent: ${d.intent}`
      : name === "plan" ? `plan (${d.engine}): ${(d.steps || []).join(" → ")}`
      : name === "tool" ? `tool: ${d.tool}(${JSON.stringify(d.arguments || {}).slice(0, 70)}) → ${d.ok ? "ok" : "failed"}`
      : name === "retrieval" ? `GraphRAG: ${d.papers} papers · ${d.topics} topics · ${d.claims} claims · ${d.conflicts} tensions · ${d.predicted_links} predicted links`
      : name === "synthesis" ? `synthesis: ${d.engine}${d.used_llm ? " (LLM)" : " (graph evidence, no LLM key)"}`
      : name;
    el.innerHTML = `<span class="muted">${String(d.elapsed_ms ?? 0).padStart(4)}ms</span> <span>${esc(info)}</span>`;
    $("#agent-stages").appendChild(el);
  },
  answer(payload) {
    const ev = payload.explainability || {};
    const papers = (payload.evidence || payload.context?.papers || []).slice(0, 6).map((p) =>
      `<div class="evidence">• ${esc(p.title)} <span class="muted tiny">${p.year || ""}</span> ${p.url ? `<a href="${esc(p.url)}" target="_blank" rel="noreferrer">↗</a>` : ""}</div>`).join("");
    const tools = (payload.tool_calls || []).map((t) => `<span class="pill">${esc(t.tool)}${t.ok ? "" : " ✗"}</span>`).join("");
    $("#agent-answer").innerHTML = `
      <div>${esc(payload.answer)}</div>
      <span class="meta">intent ${esc(payload.intent?.name || "")} · engine ${esc(payload.answer_engine)} · confidence ${esc(payload.confidence)}
        · context ${payload.context?.papers?.length ?? 0} papers / ${payload.context?.topics?.length ?? 0} topics · ${ev.reasoning?.length ?? 0} reasoning steps</span>
      <div class="chips">${tools}</div>
      ${papers ? `<div class="small"><strong>Evidence</strong>${papers}</div>` : ""}
      ${ev.safety_notice ? `<div class="safety tiny">${esc(ev.safety_notice)}</div>` : ""}
      <div class="chips">${(payload.follow_ups || []).map((f) => `<span class="chip">${esc(f)}</span>`).join("")}</div>`;
    $$("#agent-answer .chip").forEach((c) => c.addEventListener("click", () => { $("#agent-q").value = c.textContent; this.run(); }));
  },
};

/* ----------------------------------------------------------- report view */
const Report = {
  ready: false, markdown: "", json: null,
  ensure() {
    if (this.ready) return;
    this.ready = true;
    $("#report-run").addEventListener("click", () => this.run());
    $("#report-download-md").addEventListener("click", () => this.download("nexus-report.md", this.markdown, "text/markdown"));
    $("#report-download-json").addEventListener("click", () => this.download("nexus-report.json", JSON.stringify(this.json, null, 2), "application/json"));
    $("#report-export-cypher").addEventListener("click", async () => {
      try {
        const text = await api("/api/export/cypher?limit=300", { raw: true });
        this.download("nexus-graph.cypher", text, "text/plain");
      } catch (err) { toast(`export failed: ${err.message}`); }
    });
  },
  body() {
    return {
      topic: $("#report-topic").value.trim() || null,
      year_min: Number($("#report-year-min").value) || null,
      year_max: Number($("#report-year-max").value) || null,
      top_k: Number($("#report-topk").value) || 5,
    };
  },
  async run() {
    $("#report-status").textContent = "generating…";
    try {
      const [md, json] = await Promise.all([
        api("/api/report/markdown", { method: "POST", body: this.body(), raw: true }),
        api("/api/report", { method: "POST", body: this.body() }),
      ]);
      this.markdown = md; this.json = json;
      $("#report-body").innerHTML = renderMarkdown(md);
      $("#report-status").textContent = `generated ${json.generated_at || ""} · ${(json.gaps || []).length} candidate gaps · ${json.sources?.length ?? 0} sources`;
    } catch (err) { $("#report-status").textContent = `failed: ${err.message}`; }
  },
  download(name, content, type) {
    if (!content) { toast("Generate the report first"); return; }
    const blob = new Blob([content], { type });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = name; a.click();
    URL.revokeObjectURL(a.href);
  },
};

/* ------------------------------------------------------------- bootstrap */
async function status() {
  const dot = $("#status-dot"), text = $("#status-text");
  try {
    const h = await api("/api/health");
    const degraded = h.degraded;
    dot.className = `dot ${degraded ? "degraded" : "ok"}`;
    text.textContent = `${h.store.engine} · ${h.counts.nodes} nodes · llm ${h.capabilities.llm_configured ? "on" : "off"}`;
  } catch {
    dot.className = "dot down"; text.textContent = "backend unreachable";
  }
}

function bootstrap() {
  $("#gaps-run").addEventListener("click", () => GapFinder.run());
  route();
  status();
  setInterval(status, 30000);
  window.addEventListener("resize", () => {
    const t = $("#chart-years"); if (t && t.clientWidth) barChart(t, Home._years || {});
  });
}
bootstrap();

export { Home, Explorer, Graph, GapFinder, Papers, Agent, Report };
