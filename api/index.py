"""
VoiceCare.ai — The Content Gap Agent
Flask web server for Vercel deployment.

Routes:
  GET  /          → Main UI (VoiceCare.ai branded, Run Strategy button, live terminal)
  GET  /run       → SSE stream: runs pipeline, streams live debug logs
  GET  /download  → Download ZIP of all CSV/JSON reports
"""

import io
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

from flask import Flask, Response, request, send_file

app = Flask(__name__)

# ── Paths ──────────────────────────────────────────────────────────────────
ROOT_DIR = Path(__file__).parent.parent
AGENT_DIR = ROOT_DIR / "content-gap-agent"
IS_VERCEL = bool(os.environ.get("VERCEL"))
REPORTS_DIR = Path("/tmp/reports") if IS_VERCEL else AGENT_DIR / "reports"

# ── Main Page HTML ─────────────────────────────────────────────────────────
INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>The Content Gap Agent | VoiceCare.ai</title>
<style>
  :root {
    --bg: #070b14;
    --surface: #0e1524;
    --surface2: #162035;
    --primary: #00d4aa;
    --primary-glow: rgba(0,212,170,0.25);
    --accent: #6366f1;
    --text: #e8f1ff;
    --muted: #6b7a99;
    --term-bg: #000206;
    --term-green: #00ff41;
    --term-dim: #003a14;
    --term-cyan: #00d4ff;
    --term-amber: #f59e0b;
    --term-red: #ef4444;
    --border: #1e2d4d;
    --step-done: #00d4aa;
    --step-active: #6366f1;
  }
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  html { scroll-behavior: smooth; }
  body {
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    min-height: 100vh;
    display: flex;
    flex-direction: column;
    overflow-x: hidden;
  }
  #bgCanvas {
    position: fixed; top: 0; left: 0;
    width: 100%; height: 100%;
    pointer-events: none; z-index: 0;
    opacity: 0;
    transition: opacity 0.5s;
  }
  #bgCanvas.active { opacity: 1; }

  /* ── Header ── */
  header {
    position: relative; z-index: 10;
    display: flex; align-items: center; justify-content: space-between;
    padding: 18px 40px;
    border-bottom: 1px solid var(--border);
    background: rgba(7,11,20,0.85);
    backdrop-filter: blur(12px);
  }
  .logo {
    display: flex; align-items: center; gap: 10px;
    font-size: 22px; font-weight: 700; letter-spacing: -0.5px;
    color: var(--text);
  }
  .logo img { height: 36px; width: auto; display: block; }
  .logo-dot { color: var(--primary); }
  .tagline {
    font-size: 12px; color: var(--muted);
    letter-spacing: 0.5px; text-align: right;
    max-width: 280px;
  }

  /* ── Main ── */
  main {
    position: relative; z-index: 10;
    max-width: 900px; margin: 0 auto;
    padding: 40px 24px 60px;
    flex: 1;
  }

  /* ── Hero ── */
  .hero { text-align: center; margin-bottom: 48px; }
  .badge {
    display: inline-block;
    background: rgba(0,212,170,0.12);
    color: var(--primary);
    border: 1px solid rgba(0,212,170,0.3);
    border-radius: 999px;
    font-size: 11px; font-weight: 600;
    letter-spacing: 1.5px; text-transform: uppercase;
    padding: 4px 14px; margin-bottom: 18px;
  }
  h1 {
    font-size: clamp(28px, 5vw, 48px);
    font-weight: 800; letter-spacing: -1.5px;
    background: linear-gradient(135deg, #e8f1ff 0%, var(--primary) 60%, var(--accent) 100%);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
    background-clip: text;
    margin-bottom: 14px;
  }
  .hero-sub {
    color: var(--muted); font-size: 16px; max-width: 520px; margin: 0 auto;
    line-height: 1.6;
  }

  /* ── Pipeline Steps ── */
  .pipeline {
    display: flex; align-items: center; justify-content: center;
    gap: 8px; margin-bottom: 44px;
    flex-wrap: wrap;
  }
  .step {
    display: flex; flex-direction: column; align-items: center; gap: 6px;
    background: var(--surface);
    border: 1.5px solid var(--border);
    border-radius: 12px; padding: 14px 18px;
    min-width: 110px; cursor: default;
    transition: all 0.3s ease;
    position: relative;
  }
  .step.active {
    border-color: var(--step-active);
    box-shadow: 0 0 18px rgba(99,102,241,0.35);
    animation: stepPulse 1.5s infinite;
  }
  .step.done {
    border-color: var(--step-done);
    box-shadow: 0 0 12px rgba(0,212,170,0.2);
  }
  .step.done::after {
    content: '✓';
    position: absolute; top: -8px; right: -8px;
    background: var(--step-done); color: #000;
    border-radius: 50%; width: 20px; height: 20px;
    display: flex; align-items: center; justify-content: center;
    font-size: 11px; font-weight: 900;
  }
  @keyframes stepPulse {
    0%, 100% { box-shadow: 0 0 12px rgba(99,102,241,0.3); }
    50% { box-shadow: 0 0 28px rgba(99,102,241,0.6); }
  }
  .step-icon { font-size: 22px; }
  .step-label { font-size: 11px; color: var(--muted); font-weight: 500; text-align: center; }
  .step.active .step-label { color: var(--accent); }
  .step.done .step-label { color: var(--primary); }
  .arrow { color: var(--border); font-size: 20px; padding: 0 4px; }

  /* ── Controls ── */
  .controls {
    display: flex; flex-direction: column; align-items: center; gap: 18px;
    margin-bottom: 36px;
  }
  .mode-toggle {
    display: flex; align-items: center; gap: 10px;
    font-size: 13px; color: var(--muted);
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 999px; padding: 6px 16px;
    cursor: pointer; user-select: none;
  }
  .toggle-switch {
    position: relative; width: 38px; height: 20px;
  }
  .toggle-switch input { opacity: 0; width: 0; height: 0; }
  .toggle-track {
    position: absolute; top: 0; left: 0; right: 0; bottom: 0;
    background: var(--border); border-radius: 999px;
    transition: background 0.3s;
  }
  .toggle-switch input:checked + .toggle-track { background: var(--primary); }
  .toggle-thumb {
    position: absolute; top: 2px; left: 2px;
    width: 16px; height: 16px;
    background: white; border-radius: 50%;
    transition: transform 0.3s;
  }
  .toggle-switch input:checked ~ .toggle-thumb { transform: translateX(18px); }
  .demo-label { font-weight: 600; color: var(--primary); font-size: 12px; }

  #runBtn {
    position: relative;
    background: linear-gradient(135deg, var(--primary) 0%, #00a882 100%);
    color: #000; border: none;
    font-size: 17px; font-weight: 700; letter-spacing: 0.5px;
    padding: 16px 52px; border-radius: 999px;
    cursor: pointer;
    transition: all 0.25s;
    box-shadow: 0 0 0 0 var(--primary-glow);
  }
  #runBtn:hover:not(:disabled) {
    transform: translateY(-2px);
    box-shadow: 0 8px 32px var(--primary-glow), 0 0 0 0 transparent;
  }
  #runBtn:disabled {
    opacity: 0.7; cursor: not-allowed; transform: none;
  }
  #runBtn.running {
    background: linear-gradient(135deg, var(--accent) 0%, #4f46e5 100%);
    animation: btnPulse 1.5s infinite;
  }
  @keyframes btnPulse {
    0%, 100% { box-shadow: 0 0 0 0 rgba(99,102,241,0.5); }
    50% { box-shadow: 0 0 0 14px rgba(99,102,241,0); }
  }
  .btn-spinner {
    display: none;
    width: 18px; height: 18px;
    border: 2.5px solid rgba(255,255,255,0.3);
    border-top-color: #fff;
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
    margin-right: 8px;
    vertical-align: middle;
  }
  .btn-spinner.visible { display: inline-block; }
  @keyframes spin { to { transform: rotate(360deg); } }

  /* ── Terminal ── */
  #termSection { display: none; margin-bottom: 24px; }
  .term-header {
    background: #1a1a1a;
    border: 1px solid #333;
    border-bottom: none;
    border-radius: 10px 10px 0 0;
    padding: 10px 16px;
    display: flex; align-items: center; gap: 8px;
  }
  .term-dots { display: flex; gap: 6px; }
  .term-dot {
    width: 12px; height: 12px; border-radius: 50%;
  }
  .term-dot.r { background: #ff5f57; }
  .term-dot.y { background: #febc2e; }
  .term-dot.g { background: #28c840; }
  .term-title {
    flex: 1; text-align: center;
    font-size: 12px; color: #888;
    font-family: monospace;
  }
  .term-badge {
    font-size: 10px; font-weight: 700; letter-spacing: 1px;
    padding: 3px 10px; border-radius: 999px;
    background: rgba(0,255,65,0.1); color: var(--term-green);
    border: 1px solid rgba(0,255,65,0.3);
    display: none;
  }
  .term-badge.visible { display: block; }

  #terminal {
    background: var(--term-bg);
    border: 1px solid #333;
    border-radius: 0 0 10px 10px;
    font-family: 'Courier New', Courier, monospace;
    font-size: 13px;
    height: 380px;
    overflow-y: auto;
    padding: 14px 16px;
    position: relative;
    scroll-behavior: smooth;
  }
  /* Scanline overlay */
  #terminal::after {
    content: '';
    position: absolute; top: 0; left: 0;
    width: 100%; height: 100%;
    background: repeating-linear-gradient(
      0deg,
      rgba(0,0,0,0.04) 0px,
      rgba(0,0,0,0.04) 1px,
      transparent 1px,
      transparent 3px
    );
    pointer-events: none; z-index: 5;
  }
  .term-line {
    line-height: 1.55;
    white-space: pre-wrap; word-break: break-all;
    animation: lineIn 0.25s ease forwards;
    padding: 1px 0;
  }
  @keyframes lineIn {
    from { opacity: 0; transform: translateX(-6px); }
    to   { opacity: 1; transform: translateX(0); }
  }
  .term-line.info    { color: var(--term-green); }
  .term-line.step    { color: var(--term-cyan); font-weight: bold; }
  .term-line.warning { color: var(--term-amber); }
  .term-line.error   { color: var(--term-red); }
  .term-line.success { color: #69ff47; }
  .term-line.dim     { color: var(--term-dim); }
  .term-line.header  { color: var(--term-cyan); letter-spacing: 0.5px; }
  .term-cursor-line { display: flex; align-items: center; gap: 4px; padding-top: 4px; }
  .prompt { color: var(--primary); font-weight: bold; }
  .blink { color: var(--term-green); animation: blink 1s step-end infinite; }
  @keyframes blink { 50% { opacity: 0; } }

  /* Scrollbar */
  #terminal::-webkit-scrollbar { width: 6px; }
  #terminal::-webkit-scrollbar-track { background: #0a0a0a; }
  #terminal::-webkit-scrollbar-thumb { background: #1e3a1e; border-radius: 3px; }

  /* ── Progress Bar ── */
  .progress-wrap {
    display: none; margin-bottom: 28px;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px; padding: 14px 20px;
  }
  .progress-wrap.visible { display: block; }
  .progress-label {
    display: flex; justify-content: space-between;
    font-size: 12px; color: var(--muted); margin-bottom: 8px;
  }
  .progress-bar {
    height: 6px; background: var(--border); border-radius: 3px; overflow: hidden;
  }
  .progress-fill {
    height: 100%;
    background: linear-gradient(90deg, var(--primary), var(--accent));
    border-radius: 3px;
    transition: width 0.6s cubic-bezier(0.4,0,0.2,1);
    width: 0%;
  }

  /* ── Results ── */
  #results { display: none; }
  .success-banner {
    background: rgba(0,212,170,0.1);
    border: 1px solid rgba(0,212,170,0.4);
    border-radius: 12px;
    padding: 18px 24px; margin-bottom: 20px;
    display: flex; align-items: center; gap: 12px;
    font-size: 17px; font-weight: 600; color: var(--primary);
    animation: fadeIn 0.5s ease;
  }
  .success-icon {
    width: 36px; height: 36px;
    background: var(--primary); color: #000;
    border-radius: 50%; display: flex; align-items: center; justify-content: center;
    font-size: 18px; font-weight: 900; flex-shrink: 0;
  }
  @keyframes fadeIn { from { opacity:0; transform:translateY(8px); } to { opacity:1; transform:translateY(0); } }
  .action-btns { display: flex; gap: 14px; flex-wrap: wrap; }
  .action-btn {
    flex: 1; min-width: 180px;
    padding: 14px 28px; border-radius: 10px;
    font-size: 15px; font-weight: 600;
    cursor: pointer; border: none;
    transition: all 0.2s; display: flex; align-items: center; justify-content: center; gap: 8px;
  }
  .btn-dashboard {
    background: linear-gradient(135deg, var(--accent) 0%, #4f46e5 100%);
    color: white;
    box-shadow: 0 4px 18px rgba(99,102,241,0.3);
  }
  .btn-dashboard:hover { transform: translateY(-2px); box-shadow: 0 8px 28px rgba(99,102,241,0.4); }
  .btn-download {
    background: var(--surface);
    color: var(--text);
    border: 1.5px solid var(--border);
  }
  .btn-download:hover { background: var(--surface2); border-color: var(--primary); color: var(--primary); }

  /* ── Particles (confetti) ── */
  .particle {
    position: fixed; pointer-events: none; z-index: 9999;
    border-radius: 50%;
    animation: particleFall 1.8s ease-in forwards;
  }
  @keyframes particleFall {
    0%   { transform: translateY(0) rotate(0deg); opacity: 1; }
    100% { transform: translateY(100vh) rotate(720deg); opacity: 0; }
  }

  /* ── Footer ── */
  footer {
    position: relative; z-index: 10;
    text-align: center; padding: 20px;
    border-top: 1px solid var(--border);
    color: var(--muted); font-size: 13px;
  }
  footer strong { color: var(--text); }
  footer a { color: var(--primary); text-decoration: none; }
  footer a:hover { text-decoration: underline; }

  /* ── Responsive ── */
  @media (max-width: 600px) {
    header { padding: 14px 20px; }
    .tagline { display: none; }
    .pipeline { gap: 4px; }
    .step { min-width: 80px; padding: 10px 10px; }
    .step-icon { font-size: 18px; }
    main { padding: 24px 16px 48px; }
  }
</style>
</head>
<body>

<!-- Matrix rain background canvas -->
<canvas id="bgCanvas"></canvas>

<!-- ── Header ── -->
<header>
  <div class="logo">
    <img src="/logo.avif" alt="VoiceCare.ai">
  </div>
  <div class="tagline">AI-powered voice agents for healthcare &mdash; 24/7, HIPAA-compliant</div>
</header>

<!-- ── Main ── -->
<main>

  <!-- Hero -->
  <section class="hero">
    <div class="badge">Content Intelligence</div>
    <h1>The Content Gap Agent</h1>
    <p class="hero-sub">Competitor analysis pipeline &mdash; crawl sites, identify content gaps, generate 60-second video scripts, download your report.</p>
  </section>

  <!-- Pipeline Steps -->
  <div class="pipeline">
    <div class="step" id="s1">
      <span class="step-icon">🕷️</span>
      <span class="step-label">Crawl Sites</span>
    </div>
    <span class="arrow">›</span>
    <div class="step" id="s2">
      <span class="step-icon">🔍</span>
      <span class="step-label">Analyze Gaps</span>
    </div>
    <span class="arrow">›</span>
    <div class="step" id="s3">
      <span class="step-icon">✍️</span>
      <span class="step-label">Write Scripts</span>
    </div>
    <span class="arrow">›</span>
    <div class="step" id="s4">
      <span class="step-icon">📊</span>
      <span class="step-label">Report</span>
    </div>
  </div>

  <!-- Controls -->
  <div class="controls">
    <label class="mode-toggle" title="Demo mode uses mock data and runs instantly. Disable for live API calls.">
      <span>Mode:</span>
      <label class="toggle-switch">
        <input type="checkbox" id="demoMode" checked>
        <div class="toggle-track"></div>
        <div class="toggle-thumb"></div>
      </label>
      <span class="demo-label" id="modeLabel">Demo (Mock Data)</span>
    </label>
    <button id="runBtn" onclick="startRun()">
      <span class="btn-spinner" id="btnSpinner"></span>
      <span id="btnText">▶ &nbsp;Run Strategy</span>
    </button>
  </div>

  <!-- Terminal -->
  <section id="termSection">
    <div class="term-header">
      <div class="term-dots">
        <div class="term-dot r"></div>
        <div class="term-dot y"></div>
        <div class="term-dot g"></div>
      </div>
      <span class="term-title">content-gap-agent &mdash; python main.py</span>
      <span class="term-badge" id="termBadge">● RUNNING</span>
    </div>
    <div id="terminal">
      <div class="term-cursor-line" id="cursorLine">
        <span class="prompt">$</span>
        <span id="cmdDisplay">&nbsp;</span>
        <span class="blink">█</span>
      </div>
    </div>
  </section>

  <!-- Progress -->
  <div class="progress-wrap" id="progressWrap">
    <div class="progress-label">
      <span id="progressText">Initializing...</span>
      <span id="progressPct">0%</span>
    </div>
    <div class="progress-bar">
      <div class="progress-fill" id="progressFill"></div>
    </div>
  </div>

  <!-- Results -->
  <section id="results">
    <div class="success-banner">
      <div class="success-icon">✓</div>
      <div>
        <div>Analysis Complete!</div>
        <div style="font-size:13px;font-weight:400;color:var(--muted);margin-top:2px;">Your content gap report is ready.</div>
      </div>
    </div>
    <div class="action-btns">
      <button class="action-btn btn-dashboard" onclick="viewDashboard()">
        📊 &nbsp;View Dashboard
      </button>
      <button class="action-btn btn-download" onclick="downloadReports()">
        ⬇ &nbsp;Download Reports
      </button>
    </div>
  </section>

</main>

<!-- ── Footer ── -->
<footer>
  Built by <strong>Shubham Anand</strong> &nbsp;&bull;&nbsp;
  <a href="https://voicecare.ai" target="_blank">VoiceCare.ai</a>
</footer>

<script>
// ── State ─────────────────────────────────────────────────────────────────
let dashboardHtml = null;
let isRunning = false;
let sse = null;
let glitchTimer = null;
let matrixTimer = null;

// ── Mode Toggle ────────────────────────────────────────────────────────────
document.getElementById('demoMode').addEventListener('change', function() {
  document.getElementById('modeLabel').textContent =
    this.checked ? 'Demo (Mock Data)' : 'Live (Real APIs)';
});

// ── Matrix Rain ────────────────────────────────────────────────────────────
const bgCanvas = document.getElementById('bgCanvas');
const ctx = bgCanvas.getContext('2d');
let drops = [];

function initMatrix() {
  bgCanvas.width = window.innerWidth;
  bgCanvas.height = window.innerHeight;
  const cols = Math.floor(bgCanvas.width / 16);
  drops = Array.from({ length: cols }, () => Math.random() * -bgCanvas.height / 16);
}

function drawMatrix() {
  ctx.fillStyle = 'rgba(7,11,20,0.06)';
  ctx.fillRect(0, 0, bgCanvas.width, bgCanvas.height);
  const chars = 'アイウエオカキクケコ0123456789ABCDEF$#@%';
  ctx.font = '14px monospace';
  drops.forEach((y, i) => {
    const ch = chars[Math.floor(Math.random() * chars.length)];
    ctx.fillStyle = i % 5 === 0 ? '#00ff4155' : '#003a1488';
    ctx.fillText(ch, i * 16, y * 16);
    if (y * 16 > bgCanvas.height && Math.random() > 0.975) drops[i] = 0;
    drops[i] += 0.5;
  });
}

function startMatrix() {
  initMatrix();
  bgCanvas.classList.add('active');
  matrixTimer = setInterval(drawMatrix, 60);
}
function stopMatrix() {
  if (matrixTimer) { clearInterval(matrixTimer); matrixTimer = null; }
  bgCanvas.classList.remove('active');
  ctx.clearRect(0, 0, bgCanvas.width, bgCanvas.height);
}

window.addEventListener('resize', () => { if (matrixTimer) initMatrix(); });

// ── Glitch Effect ─────────────────────────────────────────────────────────
function glitchRandom() {
  const lines = document.querySelectorAll('.term-line');
  if (!lines.length) return;
  const el = lines[Math.floor(Math.random() * lines.length)];
  const orig = el.dataset.orig || el.textContent;
  el.dataset.orig = orig;
  const glitchSet = '█▓▒░╗╔╝╚║═╬╪╫▄▀■□';
  const arr = orig.split('');
  const n = Math.floor(Math.random() * 4) + 1;
  for (let i = 0; i < n; i++) {
    const pos = Math.floor(Math.random() * arr.length);
    arr[pos] = glitchSet[Math.floor(Math.random() * glitchSet.length)];
  }
  const saved = el.style.color;
  el.textContent = arr.join('');
  el.style.color = '#ff3333';
  setTimeout(() => { el.textContent = orig; el.style.color = saved; }, 80);
}

function startGlitch() {
  glitchTimer = setInterval(() => { if (Math.random() > 0.6) glitchRandom(); }, 2200);
}
function stopGlitch() {
  if (glitchTimer) { clearInterval(glitchTimer); glitchTimer = null; }
}

// ── Terminal ───────────────────────────────────────────────────────────────
function clearTerminal() {
  const term = document.getElementById('terminal');
  const cursor = document.getElementById('cursorLine');
  term.querySelectorAll('.term-line').forEach(el => el.remove());
  cursor.style.display = 'flex';
}

function getLineClass(text) {
  if (/\[ERROR\]|ERROR|FAILED|Traceback/.test(text)) return 'error';
  if (/\[WARNING\]|WARNING|WARNING/.test(text)) return 'warning';
  if (/STEP \d\/\d/.test(text)) return 'step';
  if (/={30,}/.test(text) || /-{30,}/.test(text)) return 'dim';
  if (/COMPLETE|complete|✓|SUCCESS/.test(text)) return 'success';
  if (/^[\s]*$/.test(text)) return 'dim';
  return 'info';
}

function appendLog(text) {
  const term = document.getElementById('terminal');
  const cursor = document.getElementById('cursorLine');
  const cls = getLineClass(text);

  const line = document.createElement('div');
  line.className = 'term-line ' + cls;
  line.dataset.orig = '> ' + text;
  term.insertBefore(line, cursor);

  // Step lines get typewriter, others get instant
  if (cls === 'step' || cls === 'header') {
    typeWrite(line, '> ' + text, 0);
  } else {
    line.textContent = '> ' + text;
  }
  term.scrollTop = term.scrollHeight;
}

function typeWrite(el, text, i) {
  if (i <= text.length) {
    el.textContent = text.slice(0, i) + (i < text.length ? '' : '');
    setTimeout(() => typeWrite(el, text, i + 1), 22);
  }
}

// ── Progress ───────────────────────────────────────────────────────────────
const STEP_LABELS = [
  'Step 1 / 4 — Crawling sites...',
  'Step 2 / 4 — Analyzing content gaps...',
  'Step 3 / 4 — Generating video scripts...',
  'Step 4 / 4 — Generating reports...',
];

function setProgress(pct, label) {
  document.getElementById('progressFill').style.width = pct + '%';
  document.getElementById('progressText').textContent = label;
  document.getElementById('progressPct').textContent = pct + '%';
}

function updateProgressFromLog(text) {
  const m = text.match(/STEP (\d)\/4/);
  if (m) {
    const n = parseInt(m[1]);
    activateStep(n);
    setProgress(n * 22, STEP_LABELS[n - 1]);
  }
  if (/CONTENT GAP AGENT.*COMPLETE|RUN COMPLETE/.test(text)) {
    setProgress(100, 'Complete!');
    for (let i = 1; i <= 4; i++) doneStep(i);
  }
}

function activateStep(n) {
  for (let i = 1; i <= 4; i++) {
    const el = document.getElementById('s' + i);
    if (!el) continue;
    el.classList.remove('active', 'done');
    if (i < n) el.classList.add('done');
    else if (i === n) el.classList.add('active');
  }
}

function doneStep(n) {
  const el = document.getElementById('s' + n);
  if (el) { el.classList.remove('active'); el.classList.add('done'); }
}

// ── Confetti Burst ─────────────────────────────────────────────────────────
function confettiBurst() {
  const colors = ['#00d4aa','#6366f1','#f59e0b','#ef4444','#a78bfa','#34d399'];
  for (let i = 0; i < 55; i++) {
    const p = document.createElement('div');
    p.className = 'particle';
    const sz = Math.random() * 10 + 5;
    p.style.cssText = [
      'width:' + sz + 'px', 'height:' + sz + 'px',
      'left:' + (Math.random() * 100) + 'vw',
      'top:' + (Math.random() * 40) + 'vh',
      'background:' + colors[Math.floor(Math.random() * colors.length)],
      'animation-delay:' + (Math.random() * 0.8) + 's',
      'animation-duration:' + (Math.random() * 1.2 + 1.2) + 's',
    ].join(';');
    document.body.appendChild(p);
    setTimeout(() => p.remove(), 3000);
  }
}

// ── Run Strategy ───────────────────────────────────────────────────────────
function startRun() {
  if (isRunning) return;
  isRunning = true;
  dashboardHtml = null;

  const dryRun = document.getElementById('demoMode').checked ? '1' : '0';

  // UI: running state
  const btn = document.getElementById('runBtn');
  btn.disabled = true;
  btn.classList.add('running');
  document.getElementById('btnSpinner').classList.add('visible');
  document.getElementById('btnText').textContent = 'Running Pipeline...';

  // Show terminal + progress
  document.getElementById('termSection').style.display = 'block';
  document.getElementById('termBadge').classList.add('visible');
  document.getElementById('progressWrap').classList.add('visible');
  document.getElementById('results').style.display = 'none';

  clearTerminal();
  for (let i = 1; i <= 4; i++) {
    const el = document.getElementById('s' + i);
    if (el) el.classList.remove('active', 'done');
  }
  setProgress(0, 'Initializing...');
  startMatrix();
  startGlitch();

  // Command display
  const cmd = 'python main.py' + (dryRun === '1' ? ' --dry-run --skip-slack' : ' --skip-slack');
  document.getElementById('cmdDisplay').textContent = cmd;

  // SSE
  sse = new EventSource('/run?dry=' + dryRun);

  sse.onmessage = function(e) {
    let data;
    try { data = JSON.parse(e.data); } catch { return; }

    if (data.type === 'log') {
      appendLog(data.text);
      updateProgressFromLog(data.text);
    } else if (data.type === 'complete') {
      handleComplete(data);
    }
  };

  sse.onerror = function() {
    sse.close();
    finishRun(false);
    appendLog('[ERROR] Connection lost or pipeline timed out.', 'error');
  };
}

function handleComplete(data) {
  sse.close();
  if (data.dashboard_html) dashboardHtml = data.dashboard_html;
  setProgress(100, 'Complete!');
  for (let i = 1; i <= 4; i++) doneStep(i);
  finishRun(data.success !== false);
  if (data.success !== false) {
    confettiBurst();
    document.getElementById('results').style.display = 'block';
    document.getElementById('results').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }
}

function finishRun(success) {
  isRunning = false;
  stopMatrix();
  stopGlitch();
  document.getElementById('termBadge').textContent = success ? '● DONE' : '● FAILED';
  document.getElementById('termBadge').style.color = success ? 'var(--term-green)' : 'var(--term-red)';
  const btn = document.getElementById('runBtn');
  btn.disabled = false;
  btn.classList.remove('running');
  document.getElementById('btnSpinner').classList.remove('visible');
  document.getElementById('btnText').textContent = '▶  Run Again';
  document.getElementById('cursorLine').style.display = 'none';
}

// ── Dashboard / Download ───────────────────────────────────────────────────
function viewDashboard() {
  if (!dashboardHtml) {
    alert('No dashboard available — run the strategy first.');
    return;
  }
  const blob = new Blob([dashboardHtml], { type: 'text/html' });
  window.open(URL.createObjectURL(blob), '_blank');
}

function downloadReports() {
  window.location.href = '/download';
}
</script>
</body>
</html>
"""


# ── Routes ─────────────────────────────────────────────────────────────────

@app.route("/logo.avif")
def serve_logo():
    return send_file(str(AGENT_DIR / "logo.avif"), mimetype="image/avif")


@app.route("/")
def index():
    return INDEX_HTML, 200, {"Content-Type": "text/html; charset=utf-8"}


@app.route("/run")
def run_strategy():
    """SSE endpoint: starts main.py, streams every stdout/stderr line."""
    dry_run = request.args.get("dry", "1") == "1"
    if not (AGENT_DIR / "main.py").exists():
        yield "data: " + json.dumps({
            "type": "log",
            "text": "ERROR: main.py not found"
        }) + "\n\n"
        return
    def generate():
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["REPORTS_DIR"] = str(REPORTS_DIR)
        # Ensure the agent package is importable
        env["PYTHONPATH"] = str(AGENT_DIR) + os.pathsep + env.get("PYTHONPATH", "")

        cmd = [sys.executable, str(AGENT_DIR / "main.py"), "--skip-slack"]
        if dry_run:
            cmd.append("--dry-run")

        REPORTS_DIR.mkdir(parents=True, exist_ok=True)

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            cwd=str(AGENT_DIR),
            env=env,
        )

        for raw in proc.stdout:
            line = raw.rstrip("\n")
            if line:
                yield "data: " + json.dumps({"type": "log", "text": line}) + "\n\n"

        proc.wait()

        # Read generated HTML dashboard (if any)
        html_content = None
        try:
            html_files = sorted(
                REPORTS_DIR.glob("dashboard_*.html"),
                key=lambda p: p.stat().st_mtime,
            )
            if html_files:
                html_content = html_files[-1].read_text("utf-8")
        except Exception:
            pass

        # List CSV report names for display
        csv_files: list[str] = []
        try:
            csv_files = [f.name for f in sorted(REPORTS_DIR.glob("*.csv"))]
        except Exception:
            pass

        payload = {
            "type": "complete",
            "success": proc.returncode == 0,
            "dashboard_html": html_content,
            "csv_files": csv_files,
        }
        yield "data: " + json.dumps(payload) + "\n\n"

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.route("/download")
def download_reports():
    """Bundle all CSV and JSON reports into a ZIP for download."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        try:
            for f in sorted(REPORTS_DIR.glob("*.csv")):
                zf.write(f, f.name)
            for f in sorted(REPORTS_DIR.glob("full_report_*.json")):
                zf.write(f, f.name)
        except Exception:
            pass
    buf.seek(0)
    return send_file(
        buf,
        mimetype="application/zip",
        as_attachment=True,
        download_name="voicecare_content_gap_report.zip",
    )


# ASGI wrapper so uvicorn can serve this Flask (WSGI) app locally.
# Install: pip install a2wsgi
try:
    from a2wsgi import WSGIMiddleware
    asgi_app = WSGIMiddleware(app)
except ImportError:
    asgi_app = None  # falls back to plain Flask dev server below

if __name__ == "__main__":
    app.run(debug=True, port=5000, threaded=True)
