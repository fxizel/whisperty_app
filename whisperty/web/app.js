/* Whisperty — contrôleur de l'interface (vanilla JS, aucune dépendance réseau).
 *
 * Couche données : préfère window.pywebview.api (pont Python) ; en son absence
 * (ouverture du fichier seul / aperçu Artifact), retombe sur des données factices
 * INTERACTIVES, fidèles à la maquette. Le même fichier sert donc d'application
 * réelle ET d'aperçu autonome. 100 % local : aucun fetch, aucune CDN. */
"use strict";

// ───────────────────────────── Couche données ──────────────────────────────
const Mock = (() => {
  const cfg = {
    model: "small", device: "CPU", langue: "fr",
    mic: null,
    mics: [
      { value: null, label: "Microphone par défaut" },
      { value: 1, label: "Casque USB — Jabra Evolve" },
      { value: 2, label: "Microphone de la webcam" },
    ],
    vad: 10, silence: 1500,
    combo: "<alt>+<shift>+d",
    injection: "presse", delai: 5,
    ia: false, iaEndpoint: "http://localhost:11434", iaModel: "qwen2.5:3b",
    localOnly: true,
  };
  const stats = { words: 3482, dur: 47, trans: 18 };
  let lastText = "Pense à relancer l'équipe produit sur la roadmap du quatrième trimestre, et préparer un récapitulatif des retours utilisateurs avant la réunion de lundi matin.";

  const texts = [
    "Pense à relancer l'équipe produit sur la roadmap du quatrième trimestre avant la réunion de lundi.",
    "Compte rendu de l'entretien client : besoin d'une intégration SSO et d'un export CSV des rapports mensuels.",
    "Note rapide, vérifier la latence du modèle small sur le processeur, elle reste correcte en dessous de cinq cents millisecondes.",
    "Bonjour Marc, peux-tu m'envoyer les chiffres consolidés du mois dernier ? Merci d'avance et bonne journée.",
    "Idée d'article : comparer la transcription locale et les services cloud sous l'angle de la confidentialité des données.",
    "Réunion d'équipe, décider du passage au modèle medium pour les conférences et tester la charge sur le GPU.",
    "Rappel important, signer le devis du prestataire audio et planifier l'installation des micros pour jeudi prochain.",
    "Brouillon de message : confirmer la disponibilité pour la démonstration du produit mercredi après-midi.",
    "Observation, le seuil de détection vocale à quarante-cinq pour cent élimine bien le bruit de fond du clavier.",
    "Synthèse de la conférence : trois axes prioritaires, performance, accessibilité et support du mode hors ligne.",
    "Mémo personnel, revoir la documentation d'installation et clarifier l'étape de téléchargement initial du modèle.",
    "Penser à exporter le journal des sessions de dictée pour le rapport hebdomadaire de productivité de l'équipe.",
  ];
  const sources = ["dictée", "live", "réunion"];
  const times = ["Aujourd'hui 15:42","Aujourd'hui 14:08","Aujourd'hui 11:23","Aujourd'hui 09:51","Hier 18:30","Hier 16:12","Hier 13:45","Hier 10:02","19 juin 17:20","19 juin 14:55","19 juin 09:30","18 juin 16:40","18 juin 11:15","17 juin 15:00","17 juin 10:48","16 juin 14:20","16 juin 09:05","15 juin 17:55","15 juin 12:30","14 juin 16:10","14 juin 10:25","13 juin 15:35","13 juin 11:00","12 juin 09:40"];
  // ids = chaînes numériques, comme l'API réelle (str(e.id) d'un entier SQLite).
  let history = times.map((t, i) => ({
    id: String(i + 1), time: t, sec: 18 + (i * 23) % 172,
    words: 38 + (i * 17) % 170, source: sources[i % 3], text: texts[i % texts.length],
  }));

  // Machine à états factice (anime le visualiseur dans l'aperçu).
  let state = "idle", timer = null;

  return {
    poll: () => ({ state, level: state === "recording" ? 0.35 + Math.random() * 0.5 : (state === "processing" ? 0.2 : 0) }),
    get_dashboard: () => ({ lastText, statsWords: stats.words, statsDur: stats.dur, statsTrans: stats.trans, combo: cfg.combo, model: cfg.model, device: cfg.device }),
    set_mode: () => ({ ok: true }),
    toggle_record: () => {
      if (state === "idle") { state = "recording"; }
      else if (state === "recording") {
        state = "processing";
        clearTimeout(timer);
        timer = setTimeout(() => { state = "idle"; stats.words += 42 + Math.floor(Math.random() * 60); stats.trans += 1; }, 1700);
      } else { state = "idle"; }
      return { ok: true };
    },
    get_config: () => JSON.parse(JSON.stringify(cfg)),
    save_config: (p) => { Object.assign(cfg, p); return { ok: true }; },
    list_microphones: () => cfg.mics,
    list_audio_outputs: () => [
      { value: null, label: "Sortie par défaut" },
      { value: 0, label: "Haut-parleurs (Realtek) (défaut)" },
      { value: 1, label: "Casque USB — Jabra Evolve" },
      { value: 2, label: "Écran HDMI — Dell U2720Q" },
    ],
    set_source: () => ({ ok: true }),
    get_history: () => ({ total: history.length, items: history.map(h => ({ ...h })) }),
    delete_history: (id) => { history = history.filter(h => h.id !== id); return { ok: true }; },
    clear_history: () => { history = []; return { ok: true }; },
    copy_text: (t) => { try { navigator.clipboard && navigator.clipboard.writeText(t); } catch (e) {} return { ok: true }; },
    win_minimize: () => ({ ok: true }), win_maximize: () => ({ ok: true }), win_close: () => ({ ok: true }),
    win_move: () => ({ ok: true }),
  };
})();

// Appelle une méthode du pont Python si présent, sinon la doublure locale.
function call(method, ...args) {
  const api = window.pywebview && window.pywebview.api;
  if (api && typeof api[method] === "function") {
    return Promise.resolve(api[method](...args));
  }
  return Promise.resolve(Mock[method] ? Mock[method](...args) : null);
}

// ───────────────────────────── État de l'UI ────────────────────────────────
const ui = {
  tab: "dashboard",
  mode: "dictee",
  state: "idle",
  source: null,        // sortie audio choisie pour les modes loopback (null = défaut)
  sourceCount: 0,      // nombre d'options de source (sélecteur masqué si ≤ 1)
  cfg: null,
  hist: { all: [], query: "", mode: "all", words: 0, page: 1, expanded: null },
  capturing: false,
  copiedId: null,
  stopping: false,  // arrêt demandé : retour visuel immédiat le temps que le worker finalise
};

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

// ───────────────────────────── Raccourci clavier ───────────────────────────
const MOD_LABEL = { "<ctrl>": "Ctrl", "<alt>": "Alt", "<shift>": "Maj", "<cmd>": "Win" };
const KEY_LABEL = { "<space>": "Espace", "<enter>": "Entrée", "<tab>": "Tab", "<esc>": "Échap", "<backspace>": "Retour" };

function comboToKeys(combo) {
  if (!combo) return ["—"];
  return combo.split("+").map(tok => {
    tok = tok.trim();
    if (MOD_LABEL[tok]) return MOD_LABEL[tok];
    if (KEY_LABEL[tok]) return KEY_LABEL[tok];
    if (/^<f\d+>$/.test(tok)) return tok.slice(1, -1).toUpperCase();
    return tok.replace(/[<>]/g, "").toUpperCase();
  });
}

function renderKeys(container, combo) {
  const keys = comboToKeys(combo);
  container.innerHTML = keys.map(k => `<span class="kbd">${k}</span>`).join('<span class="kbd-plus">+</span>');
}

// Convertit un évènement clavier (capture) en combo format pynput.
function eventToCombo(e) {
  const mods = [];
  if (e.ctrlKey) mods.push("<ctrl>");
  if (e.altKey) mods.push("<alt>");
  if (e.shiftKey) mods.push("<shift>");
  if (e.metaKey) mods.push("<cmd>");
  let key = e.key;
  if (["Control", "Alt", "Shift", "Meta"].includes(key)) return null; // modificateur seul
  const special = { " ": "<space>", "Enter": "<enter>", "Tab": "<tab>", "Escape": "<esc>", "Backspace": "<backspace>" };
  if (special[key]) key = special[key];
  else if (/^F\d{1,2}$/.test(key)) key = "<" + key.toLowerCase() + ">";
  else if (key.length === 1) key = key.toLowerCase();
  else return null; // touche non gérée
  return mods.concat([key]).join("+");
}

// ───────────────────────────── Onglets / navigation ────────────────────────
function setTab(tab) {
  ui.tab = tab;
  $$(".nav-btn").forEach(b => b.classList.toggle("active", b.dataset.tab === tab));
  $$(".screen").forEach(s => s.classList.toggle("active", s.id === "screen-" + tab));
  if (tab === "configuration") loadConfig();
  if (tab === "historique") loadHistory();
}

// ───────────────────────────── Dashboard ───────────────────────────────────
let waveBars = null;
function ensureBars() {
  if (waveBars) return waveBars;
  waveBars = [];
  for (let i = 0; i < 44; i++) {
    waveBars.push({ dur: (0.65 + Math.random() * 0.85).toFixed(2), delay: (Math.random() * 0.7).toFixed(2) });
  }
  return waveBars;
}

// Code couleur partagé par mode : Dictée = violet, Live = vert, Conférence = ambre.
// (Cohérent avec les catégories de l'historique, cf. CAT.) Source unique de vérité
// pour teinter le sélecteur de mode, le bouton « Démarrer », le visualiseur et l'état.
const MODE_THEME = {
  dictee:     { accent: "#c084fc", ring: "rgba(168,85,247,0.5)", glow: "rgba(124,58,237,0.42)", grad: "linear-gradient(135deg,#7c3aed,#a855f7)", wave: "linear-gradient(180deg,#c084fc,#7c3aed)" },
  live:       { accent: "#4ade80", ring: "rgba(34,197,94,0.5)",  glow: "rgba(22,163,74,0.45)",  grad: "linear-gradient(135deg,#16a34a,#22c55e)", wave: "linear-gradient(180deg,#4ade80,#16a34a)" },
  conference: { accent: "#fbbf24", ring: "rgba(245,158,11,0.5)", glow: "rgba(217,119,6,0.45)",  grad: "linear-gradient(135deg,#d97706,#f59e0b)", wave: "linear-gradient(180deg,#fbbf24,#d97706)" },
};
function modeTheme() { return MODE_THEME[ui.mode] || MODE_THEME.dictee; }

// États « en cours » (capture active) et leurs libellés.
const RUNNING = ["recording", "live", "conference", "meeting"];
const RUN_LABEL = {
  recording:  "Enregistrement en cours",
  live:       "Live en cours",
  conference: "Réunion en cours",
  meeting:    "Assistant de réunion",
};

// État transitoire « Arrêt en cours… » : l'arrêt d'un live/réunion/dictée n'est pas
// instantané (le worker termine la transcription du segment en cours avant de rendre
// la main). On le signale tout de suite pour que le clic sur « Arrêter » soit visible.
function renderStopping() {
  $("#status-row").innerHTML = `<span class="spinner"></span><span class="status-label" style="color:#fbbf24">Arrêt en cours…</span>`;
  $("#action-slot").innerHTML = `<button class="btn-busy" disabled><span class="spinner sm"></span>Arrêt…</button>`;
}

function onRecClick() {
  const running = RUNNING.includes(ui.state);
  call("toggle_record").then(refreshState);
  if (running) {
    ui.stopping = true;
    renderStopping();
    // Filet de sécurité : si l'arrêt traîne anormalement, on redonne la main (nouvel essai).
    clearTimeout(onRecClick._t);
    onRecClick._t = setTimeout(() => { if (ui.stopping) { ui.stopping = false; renderStatus(ui.state); } }, 30000);
  }
}

function renderStatus(state) {
  // Pendant un arrêt demandé, on garde le retour visuel tant que l'état n'a pas changé.
  if (ui.stopping && state !== "idle" && state !== "processing") {
    renderStopping();
    return;
  }
  const t = modeTheme();

  // Ligne d'état (point + libellé), teintée par le mode quand la capture est active.
  const row = $("#status-row");
  if (state === "processing") {
    row.innerHTML = `<span class="spinner"></span><span class="status-label" style="color:#fbbf24">Transcription…</span>`;
  } else if (RUNNING.includes(state)) {
    row.innerHTML = `<span class="dot pulse" style="background:${t.accent};--ring:${t.ring}"></span><span class="status-label" style="color:${t.accent}">${RUN_LABEL[state] || "En cours"}</span>`;
  } else {
    row.innerHTML = `<span class="dot idle"></span><span class="status-label" style="color:#86efac">En attente</span>`;
  }

  // Visualiseur (barres aux couleurs du mode ; ambre pendant la transcription).
  const wave = $("#wave");
  if (state === "idle") {
    wave.innerHTML = `<div class="idle"></div>`;
  } else {
    const bars = ensureBars();
    const fill = state === "processing" ? "#f59e0b" : t.wave;
    wave.innerHTML = bars.map(b =>
      `<div class="bar" style="background:${fill};animation-duration:${b.dur}s;animation-delay:${b.delay}s;"></div>`
    ).join("");
  }

  // Bouton d'action (le « Démarrer » prend le dégradé du mode ; « Arrêter » reste rouge).
  const slot = $("#action-slot");
  if (state === "idle") {
    const label = ui.mode === "live" ? "Démarrer le live" : ui.mode === "conference" ? "Démarrer la réunion" : "Démarrer la dictée";
    slot.innerHTML = `<button class="btn-primary" id="rec-btn" style="background:${t.grad};box-shadow:0 6px 20px ${t.glow}"><svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="5.5" y="2" width="5" height="8" rx="2.5"/><path d="M3.5 8a4.5 4.5 0 0 0 9 0"/><line x1="8" y1="12.5" x2="8" y2="14.5"/></svg>${label}</button>`;
  } else if (state === "processing") {
    slot.innerHTML = `<button class="btn-busy" disabled><span class="spinner sm"></span>Traitement…</button>`;
  } else {
    slot.innerHTML = `<button class="btn-stop" id="rec-btn"><svg width="14" height="14" viewBox="0 0 14 14"><rect x="2.5" y="2.5" width="9" height="9" rx="2" fill="currentColor"/></svg>Arrêter</button>`;
  }
  const recBtn = $("#rec-btn");
  if (recBtn) recBtn.addEventListener("click", onRecClick);
}

function applyState(state) {
  if (state === ui.state) return;
  const prev = ui.state;
  ui.state = state;
  ui.stopping = false;            // l'état réel a changé : fin du transitoire « Arrêt… »
  clearTimeout(onRecClick._t);
  renderStatus(state);
  updateSourceVisibility();       // visible seulement au repos (modes loopback)
  // Rechargement du dashboard quand une transcription vient de se terminer.
  if (state === "idle" && (prev === "processing" || prev === "recording")) {
    loadDashboard();
  }
}

async function refreshState() {
  const { state, level } = await call("poll");
  applyState(state);
  // Modulation discrète de l'amplitude par le niveau réel.
  const amp = Math.max(0.25, Math.min(1, 0.3 + level));
  $("#wave").style.setProperty("--amp", amp.toFixed(2));
}

async function loadDashboard() {
  const d = await call("get_dashboard");
  $("#last-text").textContent = d.lastText || "Aucune transcription pour le moment.";
  $("#stat-words").textContent = (d.statsWords || 0).toLocaleString("fr-FR");
  $("#stat-dur").textContent = d.statsDur || 0;
  $("#stat-trans").textContent = d.statsTrans || 0;
  renderKeys($("#dash-hotkey"), d.combo);
  $("#sb-model").textContent = "whisper-" + (d.model || "small");
  $("#sb-device").textContent = d.device || "CPU";
}

// Source audio (sortie système) des modes loopback — équivalent inline des
// sous-menus du tray. Choix éphémère (par démarrage), transmis à start_live/
// start_conference côté Python via set_source.
async function loadSources() {
  const opts = (await call("list_audio_outputs")) || [];
  ui.sourceCount = opts.length;
  const sel = $("#dash-source");
  sel.innerHTML = opts.map(o =>
    `<option value="${o.value === null ? "" : o.value}">${escapeHtml(o.label)}</option>`
  ).join("");
  sel.value = ui.source == null ? "" : String(ui.source);
  updateSourceVisibility();
}

// Le sélecteur n'a de sens qu'au repos, en mode loopback, et s'il y a un choix
// réel (plus que « Sortie par défaut »). Sinon masqué (cohérent avec le tray).
function updateSourceVisibility() {
  const loop = ui.mode === "live" || ui.mode === "conference";
  const show = loop && ui.state === "idle" && ui.sourceCount > 1;
  const row = $("#source-row");
  if (row) row.style.display = show ? "" : "none";
}

// ───────────────────────────── Configuration ───────────────────────────────
const MODELS = ["tiny", "base", "small", "medium", "large-v3"];

async function loadConfig() {
  const c = await call("get_config");
  ui.cfg = c;

  // Taille du modèle
  $("#model-opts").innerHTML = MODELS.map(m =>
    `<button class="seg soft ${m === c.model ? "active" : ""}" data-model="${m}"><span>${m}</span></button>`
  ).join("");
  $$("#model-opts .seg").forEach(b => b.addEventListener("click", () => {
    ui.cfg.model = b.dataset.model;
    $$("#model-opts .seg").forEach(x => x.classList.toggle("active", x === b));
  }));

  $("#cfg-device").value = c.device;
  $("#cfg-langue").value = c.langue;

  // Micros
  const mics = c.mics || (await call("list_microphones"));
  $("#cfg-mic").innerHTML = mics.map(m =>
    `<option value="${m.value === null ? "" : m.value}">${m.label}</option>`
  ).join("");
  $("#cfg-mic").value = c.mic === null || c.mic === undefined ? "" : String(c.mic);

  // VAD / silence
  $("#cfg-vad").value = c.vad;
  $("#vad-label").textContent = (c.vad / 1000).toFixed(3);
  $("#cfg-silence").value = c.silence;
  $("#silence-label").textContent = c.silence + " ms";

  // Raccourci
  renderKeys($("#combo-display"), c.combo);
  ui.cfg.combo = c.combo;

  // Injection
  $$("#inj-switch .seg").forEach(b => b.classList.toggle("active", b.dataset.inj === c.injection));
  $("#cfg-delai").value = c.delai;
  $("#delai-label").textContent = c.delai + " ms";

  // IA
  setSwitch($("#ia-switch"), c.ia);
  $("#cfg-ia-endpoint").value = c.iaEndpoint || "";
  $("#cfg-ia-model").value = c.iaModel || "";
  applyIaState(c.ia);

  // Confidentialité
  setSwitch($("#local-switch"), c.localOnly);
  $("#local-badge").style.display = c.localOnly ? "" : "none";
}

function setSwitch(el, on) { el.classList.toggle("on", !!on); }

function applyIaState(on) {
  const f = $("#ia-fields");
  f.style.opacity = on ? "1" : "0.4";
  f.style.pointerEvents = on ? "auto" : "none";
}

function bindConfig() {
  $("#cfg-device").addEventListener("change", e => { ui.cfg.device = e.target.value; });
  $("#cfg-langue").addEventListener("change", e => { ui.cfg.langue = e.target.value; });
  $("#cfg-mic").addEventListener("change", e => { ui.cfg.mic = e.target.value === "" ? null : Number(e.target.value); });

  $("#cfg-vad").addEventListener("input", e => { ui.cfg.vad = +e.target.value; $("#vad-label").textContent = (e.target.value / 1000).toFixed(3); });
  $("#cfg-silence").addEventListener("input", e => { ui.cfg.silence = +e.target.value; $("#silence-label").textContent = e.target.value + " ms"; });
  $("#cfg-delai").addEventListener("input", e => { ui.cfg.delai = +e.target.value; $("#delai-label").textContent = e.target.value + " ms"; });

  $$("#inj-switch .seg").forEach(b => b.addEventListener("click", () => {
    ui.cfg.injection = b.dataset.inj;
    $$("#inj-switch .seg").forEach(x => x.classList.toggle("active", x === b));
  }));

  $("#ia-switch").addEventListener("click", () => {
    ui.cfg.ia = !ui.cfg.ia; setSwitch($("#ia-switch"), ui.cfg.ia); applyIaState(ui.cfg.ia);
  });
  $("#cfg-ia-endpoint").addEventListener("input", e => { ui.cfg.iaEndpoint = e.target.value; });
  $("#cfg-ia-model").addEventListener("input", e => { ui.cfg.iaModel = e.target.value; });

  $("#local-switch").addEventListener("click", () => {
    ui.cfg.localOnly = !ui.cfg.localOnly; setSwitch($("#local-switch"), ui.cfg.localOnly);
    $("#local-badge").style.display = ui.cfg.localOnly ? "" : "none";
  });

  // Capture du raccourci
  $("#combo-capture").addEventListener("click", toggleCapture);

  // Sauvegarde
  $("#save-config").addEventListener("click", saveConfig);

  // Accordéons
  $$(".accordion .acc-head").forEach(h => h.addEventListener("click", () => h.parentElement.classList.toggle("open")));
}

function toggleCapture() {
  ui.capturing = !ui.capturing;
  const btn = $("#combo-capture").querySelector("span");
  const disp = $("#combo-display");
  if (ui.capturing) {
    btn.textContent = "Annuler";
    disp.innerHTML = `<span style="font-size:12.5px; color:var(--violet); font-weight:500; animation:wsp-blink 1.2s ease-in-out infinite;">Appuyez sur une combinaison…</span>`;
    window.addEventListener("keydown", onCaptureKey, true);
  } else {
    btn.textContent = "Modifier";
    renderKeys(disp, ui.cfg.combo);
    window.removeEventListener("keydown", onCaptureKey, true);
  }
}

function onCaptureKey(e) {
  e.preventDefault(); e.stopPropagation();
  const combo = eventToCombo(e);
  if (!combo) return; // attend une touche non-modificatrice valide
  ui.cfg.combo = combo;
  ui.capturing = false;
  $("#combo-capture").querySelector("span").textContent = "Modifier";
  renderKeys($("#combo-display"), combo);
  window.removeEventListener("keydown", onCaptureKey, true);
}

async function saveConfig() {
  const payload = {
    model: ui.cfg.model, device: ui.cfg.device, langue: ui.cfg.langue,
    mic: ui.cfg.mic, vad: ui.cfg.vad, silence: ui.cfg.silence, combo: ui.cfg.combo,
    injection: ui.cfg.injection, delai: ui.cfg.delai,
    ia: ui.cfg.ia, iaEndpoint: ui.cfg.iaEndpoint, iaModel: ui.cfg.iaModel,
    localOnly: ui.cfg.localOnly,
  };
  await call("save_config", payload);
  const note = $("#saved-note");
  note.style.display = "flex";
  clearTimeout(saveConfig._t);
  saveConfig._t = setTimeout(() => { note.style.display = "none"; }, 1800);
  loadDashboard(); // rafraîchit l'étiquette modèle/raccourci de la sidebar et du dashboard
}

// ───────────────────────────── Historique ──────────────────────────────────
const PAGE_SIZE = 8;
const CAT = {
  dictee:    { bg: "rgba(168,85,247,0.13)", bd: "rgba(168,85,247,0.28)", stroke: "#c084fc",
               svg: '<rect x="5.5" y="2" width="5" height="8" rx="2.5"/><path d="M3.5 8a4.5 4.5 0 0 0 9 0"/><line x1="8" y1="12.5" x2="8" y2="14.5"/>' },
  live:      { bg: "rgba(34,197,94,0.12)", bd: "rgba(34,197,94,0.26)", stroke: "#4ade80",
               svg: '<line x1="4" y1="6" x2="4" y2="10"/><line x1="8" y1="3" x2="8" y2="13"/><line x1="12" y1="6" x2="12" y2="10"/>' },
  conference:{ bg: "rgba(245,158,11,0.12)", bd: "rgba(245,158,11,0.26)", stroke: "#fbbf24",
               svg: '<circle cx="6" cy="6.5" r="2.5"/><circle cx="10.5" cy="6.5" r="2.5"/><path d="M2.5 13a3.5 3.5 0 0 1 7 0"/><path d="M8 10.5a3.5 3.5 0 0 1 5.5 2.5"/>' },
};

function categoryOf(source) {
  const s = (source || "").toLowerCase();
  if (s.includes("réunion") || s.includes("reunion") || s.includes("conf")) return "conference";
  if (s === "live") return "live";
  return "dictee";
}
function srcLabel(source) {
  const c = categoryOf(source);
  return c === "live" ? "Live" : c === "conference" ? "Conférence" : "Dictée";
}
function fmtSec(s) { const m = Math.floor(s / 60); return m + ":" + String(s % 60).padStart(2, "0"); }

async function loadHistory() {
  const data = await call("get_history");
  ui.hist.all = data.items || [];
  ui.hist.page = 1;
  renderHistory();
}

function filteredHistory() {
  let list = ui.hist.all;
  const q = ui.hist.query.trim().toLowerCase();
  if (q) list = list.filter(x => (x.text || "").toLowerCase().includes(q));
  if (ui.hist.mode !== "all") list = list.filter(x => categoryOf(x.source) === ui.hist.mode);
  if (ui.hist.words > 0) list = list.filter(x => (x.words || 0) >= ui.hist.words);
  return list;
}

function renderHistory() {
  const list = filteredHistory();
  const total = list.length;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const page = Math.min(ui.hist.page, pages);
  ui.hist.page = page;
  $("#hist-total").textContent = ui.hist.all.length;
  $("#hist-page").textContent = page;
  $("#hist-pages").textContent = pages;
  $("#hist-prev").style.opacity = page <= 1 ? "0.35" : "1";
  $("#hist-next").style.opacity = page >= pages ? "0.35" : "1";

  const slice = list.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);
  const root = $("#hist-list");
  if (total === 0) {
    root.innerHTML = `<div class="hist-empty">Aucune transcription ne correspond à ces filtres.</div>`;
    return;
  }
  root.innerHTML = slice.map(it => {
    const cat = categoryOf(it.source);
    const c = CAT[cat];
    const open = ui.hist.expanded === it.id;
    const copied = ui.copiedId === it.id;
    const excerpt = it.text.length > 92 ? it.text.slice(0, 92) + "…" : it.text;
    const metaParts = [it.time];
    if (it.sec != null) metaParts.push(fmtSec(it.sec));
    metaParts.push((it.words || 0) + " mots", srcLabel(it.source));
    const meta = metaParts.join(" · ");
    const chev = open
      ? `<svg class="hist-chev" width="12" height="12" viewBox="0 0 13 13" fill="none" stroke="#94a3b8" stroke-width="1.6"><path d="M3 8l3.5-3.5L10 8"/></svg>`
      : `<svg class="hist-chev" width="12" height="12" viewBox="0 0 13 13" fill="none" stroke="#64748b" stroke-width="1.6"><path d="M3 5l3.5 3.5L10 5"/></svg>`;
    const copyBtn = copied
      ? `<svg width="12" height="12" viewBox="0 0 13 13" fill="none" stroke="#4ade80" stroke-width="1.6"><path d="M2.5 7l3 3 5-6.5"/></svg><span class="copied">Copié</span>`
      : `<svg width="12" height="12" viewBox="0 0 13 13" fill="none" stroke="currentColor" stroke-width="1.4"><rect x="3.5" y="3.5" width="7" height="7" rx="1.5"/><path d="M2 8.5V2.5A1 1 0 0 1 3 1.5h6"/></svg>Copier`;
    return `
      <div class="hist-item ${open ? "open" : ""}" data-id="${it.id}">
        <button class="row" data-act="toggle">
          <span class="hist-ico" style="background:${c.bg}; border:1px solid ${c.bd};">
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="${c.stroke}" stroke-width="1.45">${c.svg}</svg>
          </span>
          <span class="hist-mid"><span class="hist-excerpt">${escapeHtml(excerpt)}</span><span class="hist-meta">${escapeHtml(meta)}</span></span>
          ${chev}
        </button>
        <div class="hist-detail">
          <p>${escapeHtml(it.text)}</p>
          <div style="display:flex; gap:8px;">
            <button class="btn-ghost" data-act="copy">${copyBtn}</button>
            <button class="btn-danger" data-act="delete"><svg width="12" height="12" viewBox="0 0 13 13" fill="none" stroke="currentColor" stroke-width="1.4"><path d="M2.5 3.5h8M5 3.5V2.3a1 1 0 0 1 1-1h1a1 1 0 0 1 1 1v1.2M3.5 3.5l.5 7a1 1 0 0 0 1 .9h3a1 1 0 0 0 1-.9l.5-7"/></svg>Supprimer</button>
          </div>
        </div>
      </div>`;
  }).join("");

  $$("#hist-list .hist-item").forEach(item => {
    const id = item.dataset.id;
    const entry = ui.hist.all.find(h => h.id === id);
    item.querySelector('[data-act="toggle"]').addEventListener("click", () => {
      ui.hist.expanded = ui.hist.expanded === id ? null : id;
      renderHistory();
    });
    const copyB = item.querySelector('[data-act="copy"]');
    if (copyB) copyB.addEventListener("click", () => {
      call("copy_text", entry.text);
      ui.copiedId = id; renderHistory();
      clearTimeout(renderHistory._ct);
      renderHistory._ct = setTimeout(() => { ui.copiedId = null; renderHistory(); }, 1400);
    });
    const delB = item.querySelector('[data-act="delete"]');
    if (delB) delB.addEventListener("click", async () => {
      await call("delete_history", id);
      if (ui.hist.expanded === id) ui.hist.expanded = null;
      loadHistory();
    });
  });
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function bindHistory() {
  // Tout changement de filtre réinitialise la page ET l'élément déplié (sinon un id
  // déplié pourrait ne plus figurer dans les résultats filtrés).
  $("#hist-search").addEventListener("input", e => { ui.hist.query = e.target.value; ui.hist.page = 1; ui.hist.expanded = null; renderHistory(); });
  $$("#hist-mode .seg").forEach(b => b.addEventListener("click", () => {
    ui.hist.mode = b.dataset.hmode; ui.hist.page = 1; ui.hist.expanded = null;
    $$("#hist-mode .seg").forEach(x => x.classList.toggle("active", x === b));
    renderHistory();
  }));
  $("#hist-dur").addEventListener("change", e => { ui.hist.words = +e.target.value; ui.hist.page = 1; ui.hist.expanded = null; renderHistory(); });
  $("#hist-prev").addEventListener("click", () => { ui.hist.page = Math.max(1, ui.hist.page - 1); ui.hist.expanded = null; renderHistory(); });
  $("#hist-next").addEventListener("click", () => { ui.hist.page += 1; ui.hist.expanded = null; renderHistory(); });
}

// ───────────────────────────── Barre de titre / fenêtre ─────────────────────
function bindTitlebar() {
  $$(".win-btn").forEach(b => b.addEventListener("click", () => {
    const a = b.dataset.win;
    if (a === "minimize") call("win_minimize");
    else if (a === "maximize") call("win_maximize");
    else if (a === "close") call("win_close");
  }));

  // Déplacement de la fenêtre par drag sur la barre de titre.
  // On NE lit JAMAIS la position de la fenêtre depuis Python (la lecture de .x/.y
  // se fait hors du thread UI WinForms → plantage). On calcule le coin haut-gauche
  // visé par : position écran du curseur − décalage du curseur DANS la fenêtre.
  // Ce décalage (grab) est capté une fois au mousedown via clientX/clientY (px CSS =
  // px logiques attendus par window.move). win_move s'appuie sur SetWindowPos
  // (thread-safe). Mathématiquement identique à « origine + delta », sans lecture.
  const titlebar = $(".titlebar");
  let dragging = false;
  let grab = { x: 0, y: 0 };
  let movePending = false;
  let pendingPos = null;

  titlebar.addEventListener("mousedown", (e) => {
    if (e.button !== 0 || e.target.closest(".win-controls")) return;
    e.preventDefault();
    grab = { x: e.clientX, y: e.clientY };
    dragging = true;
    document.body.style.cursor = "grabbing";
  });

  document.addEventListener("mousemove", (e) => {
    if (!dragging) return;
    const nx = Math.round(e.screenX - grab.x);
    const ny = Math.round(e.screenY - grab.y);
    if (movePending) { pendingPos = { x: nx, y: ny }; return; }
    movePending = true;
    pendingPos = null;
    call("win_move", nx, ny).finally(() => {
      movePending = false;
      if (pendingPos) {
        const p = pendingPos; pendingPos = null;
        call("win_move", p.x, p.y);
      }
    });
  });

  document.addEventListener("mouseup", () => {
    if (!dragging) return;
    dragging = false;
    document.body.style.cursor = "";
  });
}

// ───────────────────────────── Initialisation ──────────────────────────────
function bindNav() {
  $$(".nav-btn").forEach(b => b.addEventListener("click", () => setTab(b.dataset.tab)));
  $$("#mode-switch .seg").forEach(b => b.addEventListener("click", () => {
    ui.mode = b.dataset.mode;
    $$("#mode-switch .seg").forEach(x => x.classList.toggle("active", x === b));
    call("set_mode", ui.mode);
    renderStatus(ui.state); // met à jour le libellé du bouton
    updateSourceVisibility(); // affiche le sélecteur de source en Live/Conférence
  }));
  $("#dash-source").addEventListener("change", e => {
    ui.source = e.target.value === "" ? null : Number(e.target.value);
    call("set_source", ui.source);
  });
  $("#copy-last").addEventListener("click", () => {
    const t = $("#last-text").textContent;
    call("copy_text", t);
    const span = $("#copy-last span"); const old = span.textContent;
    span.textContent = "Copié"; span.classList.add("copied");
    setTimeout(() => { span.textContent = old; span.classList.remove("copied"); }, 1400);
  });
}

function init() {
  bindNav();
  bindConfig();
  bindHistory();
  bindTitlebar();
  renderStatus("idle");
  loadDashboard();
  loadSources();
  refreshState();
  setInterval(refreshState, 200);
}

// window.pywebview est injecté après le chargement ; on initialise dès que le DOM
// est prêt (l'aperçu factice marche tout de suite) et on rafraîchit le dashboard
// quand le pont devient disponible.
if (document.readyState !== "loading") init();
else document.addEventListener("DOMContentLoaded", init);
window.addEventListener("pywebviewready", () => { loadDashboard(); loadSources(); if (ui.tab === "configuration") loadConfig(); if (ui.tab === "historique") loadHistory(); });
