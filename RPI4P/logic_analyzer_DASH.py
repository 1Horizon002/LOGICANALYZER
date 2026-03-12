#!/usr/bin/env python3
# =============================================================================
#  logic_analyzer.py — Raspberry Pi 4B
#  Logic Analyzer Driver + Web Interface
#  
#  Hardware: RPi 4B ↔ Spartan 7 via SPI0
#  Install:  pip3 install flask spidev
#  Run:      sudo python3 logic_analyzer.py
#  Open:     http://<rpi-ip>:5000
# =============================================================================

import spidev
import time
import threading
import json
import struct
from collections import deque
from flask import Flask, jsonify, render_template_string

# =============================================================================
#  Config
# =============================================================================
SPI_BUS         = 0         # SPI0
SPI_DEVICE      = 0         # CE0 → GPIO8
SPI_SPEED_HZ    = 10_000_000  # 10MHz — reliable on RPi4 with short wires
CHANNELS        = 4
SAMPLE_DEPTH    = 4096      # Must match FPGA parameter
CHANNEL_NAMES   = ["CH0", "CH1", "CH2", "CH3"]
CHANNEL_COLORS  = ["#00ff88", "#ff6b35", "#4fc3f7", "#ce93d8"]

# CMD bytes (must match FPGA)
CMD_ARM    = 0xAA
CMD_READ   = 0xBB
CMD_STATUS = 0xCC

# =============================================================================
#  FPGA SPI Driver
# =============================================================================
class FPGADriver:
    def __init__(self):
        self.spi = spidev.SpiDev()
        self.spi.open(SPI_BUS, SPI_DEVICE)
        self.spi.max_speed_hz = SPI_SPEED_HZ
        self.spi.mode = 0b00        # CPOL=0, CPHA=0
        self.spi.bits_per_word = 8
        self.spi.lsbfirst = False
        print(f"[FPGA] SPI initialized — bus={SPI_BUS} dev={SPI_DEVICE} speed={SPI_SPEED_HZ/1e6}MHz")

    def arm(self):
        """Send ARM command to FPGA — starts waiting for trigger"""
        self.spi.xfer2([CMD_ARM])
        print("[FPGA] Armed — waiting for trigger...")

    def read_samples(self):
        """
        Send READ cmd then clock out SAMPLE_DEPTH bytes.
        Each byte = 0b0000XXXX where X = 4 channel bits.
        Returns list of SAMPLE_DEPTH integers (each 0–15)
        """
        # Send READ command + clock out all samples in one burst
        cmd_and_dummy = [CMD_READ] + [0x00] * SAMPLE_DEPTH
        raw = self.spi.xfer2(cmd_and_dummy)
        # First byte is response to CMD, skip it
        samples = raw[1:]
        return samples

    def close(self):
        self.spi.close()


# =============================================================================
#  Capture Manager
# =============================================================================
class CaptureManager:
    def __init__(self):
        self.fpga = FPGADriver()
        self.lock = threading.Lock()
        self.latest_data = {
            "channels": [[] for _ in range(CHANNELS)],
            "timestamp": 0,
            "sample_rate_mhz": 100,
            "sample_depth": SAMPLE_DEPTH,
            "status": "idle"
        }
        self._running = True
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()

    def _decode_samples(self, raw_samples):
        """Split each byte into 4 channel bit arrays"""
        channels = [[] for _ in range(CHANNELS)]
        for byte in raw_samples:
            for ch in range(CHANNELS):
                channels[ch].append((byte >> ch) & 1)
        return channels

    def _capture_loop(self):
        print("[Capture] Loop started")
        while self._running:
            try:
                # Arm FPGA
                self.fpga.arm()
                with self.lock:
                    self.latest_data["status"] = "armed"

                # Poll — in real implementation, use a GPIO "DONE" line from FPGA
                # Here we wait a fixed time for trigger + capture (100MHz × 4096 = 40.96µs capture)
                time.sleep(0.1)   # 100ms between captures (adjustable)

                # Read samples
                raw = self.fpga.read_samples()
                channels = self._decode_samples(raw)

                with self.lock:
                    self.latest_data["channels"] = channels
                    self.latest_data["timestamp"] = time.time()
                    self.latest_data["status"] = "captured"

                print(f"[Capture] Got {SAMPLE_DEPTH} samples per channel ✓")

            except Exception as e:
                print(f"[Capture] Error: {e}")
                time.sleep(1.0)

    def get_data(self):
        with self.lock:
            return dict(self.latest_data)

    def stop(self):
        self._running = False
        self.fpga.close()


# =============================================================================
#  Flask Web Application
# =============================================================================
app = Flask(__name__)
capture_mgr = None   # initialized in main

# =============================================================================
#  HTML/JS Frontend — Dark oscilloscope theme
# =============================================================================
HTML = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Logic Analyzer — Spartan 7 × RPi4</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=Orbitron:wght@400;700;900&display=swap');

  :root {
    --bg:       #0a0c0f;
    --surface:  #111418;
    --border:   #1e2530;
    --ch0:      #00ff88;
    --ch1:      #ff6b35;
    --ch2:      #4fc3f7;
    --ch3:      #ce93d8;
    --text:     #c8d6e5;
    --dim:      #4a5568;
    --green:    #00ff88;
    --amber:    #ffb300;
  }

  * { margin:0; padding:0; box-sizing:border-box; }

  body {
    background: var(--bg);
    color: var(--text);
    font-family: 'Share Tech Mono', monospace;
    min-height: 100vh;
    overflow-x: hidden;
  }

  /* ── Scanline overlay ─────────────────────────────────────────── */
  body::before {
    content: '';
    position: fixed; inset: 0;
    background: repeating-linear-gradient(
      0deg,
      transparent,
      transparent 2px,
      rgba(0,0,0,0.15) 2px,
      rgba(0,0,0,0.15) 4px
    );
    pointer-events: none;
    z-index: 999;
  }

  /* ── Header ───────────────────────────────────────────────────── */
  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 14px 28px;
    border-bottom: 1px solid var(--border);
    background: var(--surface);
  }

  .logo {
    font-family: 'Orbitron', sans-serif;
    font-weight: 900;
    font-size: 1.1rem;
    letter-spacing: 0.15em;
    color: var(--green);
    text-shadow: 0 0 20px rgba(0,255,136,0.5);
  }

  .logo span { color: var(--ch2); }

  .status-bar {
    display: flex;
    gap: 24px;
    font-size: 0.75rem;
    color: var(--dim);
  }

  .status-item { display: flex; align-items: center; gap: 6px; }

  .dot {
    width: 8px; height: 8px;
    border-radius: 50%;
    background: var(--dim);
    transition: background 0.3s, box-shadow 0.3s;
  }
  .dot.active {
    background: var(--green);
    box-shadow: 0 0 10px var(--green);
    animation: blink 1s infinite;
  }
  .dot.armed {
    background: var(--amber);
    box-shadow: 0 0 10px var(--amber);
    animation: blink 0.4s infinite;
  }

  @keyframes blink { 0%,100%{opacity:1} 50%{opacity:0.3} }

  /* ── Controls ─────────────────────────────────────────────────── */
  .controls {
    display: flex;
    align-items: center;
    gap: 16px;
    padding: 12px 28px;
    background: var(--surface);
    border-bottom: 1px solid var(--border);
    flex-wrap: wrap;
  }

  .ctrl-group {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 0.72rem;
    color: var(--dim);
  }

  .ctrl-group label { text-transform: uppercase; letter-spacing: 0.1em; }

  select, input[type=range] {
    background: var(--bg);
    border: 1px solid var(--border);
    color: var(--text);
    font-family: 'Share Tech Mono', monospace;
    font-size: 0.72rem;
    padding: 4px 8px;
    border-radius: 3px;
    outline: none;
    cursor: pointer;
  }
  select:focus { border-color: var(--green); }

  .btn {
    font-family: 'Orbitron', sans-serif;
    font-size: 0.65rem;
    font-weight: 700;
    letter-spacing: 0.12em;
    padding: 7px 16px;
    border: 1px solid;
    border-radius: 3px;
    cursor: pointer;
    transition: all 0.15s;
    text-transform: uppercase;
  }
  .btn-run  { border-color: var(--green); color: var(--green); background: transparent; }
  .btn-run:hover  { background: var(--green); color: var(--bg); box-shadow: 0 0 20px rgba(0,255,136,0.4); }
  .btn-stop { border-color: #ff4444; color: #ff4444; background: transparent; }
  .btn-stop:hover { background: #ff4444; color: var(--bg); }
  .btn-export { border-color: var(--ch2); color: var(--ch2); background: transparent; }
  .btn-export:hover { background: var(--ch2); color: var(--bg); }

  /* ── Main Layout ─────────────────────────────────────────────── */
  .main {
    display: grid;
    grid-template-columns: 160px 1fr;
    height: calc(100vh - 110px);
  }

  /* ── Sidebar ──────────────────────────────────────────────────── */
  .sidebar {
    border-right: 1px solid var(--border);
    padding: 16px 12px;
    display: flex;
    flex-direction: column;
    gap: 12px;
    overflow-y: auto;
  }

  .sidebar-title {
    font-size: 0.6rem;
    text-transform: uppercase;
    letter-spacing: 0.2em;
    color: var(--dim);
    padding-bottom: 8px;
    border-bottom: 1px solid var(--border);
  }

  .ch-row {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 0.75rem;
    cursor: pointer;
    padding: 6px 8px;
    border-radius: 4px;
    border: 1px solid transparent;
    transition: all 0.15s;
    user-select: none;
  }
  .ch-row:hover { background: rgba(255,255,255,0.03); }
  .ch-row.active { border-color: var(--ch-color); background: rgba(255,255,255,0.04); }

  .ch-swatch {
    width: 12px; height: 12px;
    border-radius: 2px;
    flex-shrink: 0;
  }

  .ch-label { font-family: 'Orbitron', sans-serif; font-size: 0.65rem; }
  .ch-state {
    margin-left: auto;
    font-size: 0.65rem;
    font-weight: bold;
    min-width: 14px;
    text-align: right;
  }

  .stats-block {
    margin-top: auto;
    font-size: 0.65rem;
    color: var(--dim);
    line-height: 1.8;
    border-top: 1px solid var(--border);
    padding-top: 12px;
  }
  .stat-val { color: var(--green); }

  /* ── Canvas Area ──────────────────────────────────────────────── */
  .canvas-area {
    display: flex;
    flex-direction: column;
    overflow: hidden;
    position: relative;
  }

  .time-ruler {
    height: 28px;
    border-bottom: 1px solid var(--border);
    background: var(--surface);
    position: relative;
    flex-shrink: 0;
  }

  .channels-wrap {
    flex: 1;
    overflow-y: auto;
    overflow-x: hidden;
  }

  .ch-canvas-row {
    display: flex;
    align-items: stretch;
    border-bottom: 1px solid var(--border);
    height: 80px;
    flex-shrink: 0;
  }

  .ch-tag {
    width: 52px;
    flex-shrink: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    font-family: 'Orbitron', sans-serif;
    font-size: 0.6rem;
    font-weight: 700;
    border-right: 1px solid var(--border);
    letter-spacing: 0.08em;
  }

  canvas.wave {
    flex: 1;
    display: block;
    cursor: crosshair;
  }

  /* ── Measurement bar ──────────────────────────────────────────── */
  .measure-bar {
    height: 32px;
    border-top: 1px solid var(--border);
    background: var(--surface);
    display: flex;
    align-items: center;
    padding: 0 20px;
    gap: 28px;
    font-size: 0.68rem;
    color: var(--dim);
    flex-shrink: 0;
  }
  .measure-item { display: flex; gap: 6px; }
  .measure-val { color: var(--text); }

  /* ── Cursor line ─────────────────────────────────────────────── */
  #cursor-line {
    position: absolute;
    top: 28px;
    bottom: 32px;
    width: 1px;
    background: rgba(255,255,255,0.25);
    pointer-events: none;
    display: none;
  }

  /* ── No data overlay ─────────────────────────────────────────── */
  .no-data {
    position: absolute;
    inset: 0;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 12px;
    color: var(--dim);
    font-size: 0.8rem;
    pointer-events: none;
  }
  .no-data .big { font-family: 'Orbitron', sans-serif; font-size: 1.8rem; color: var(--border); }
</style>
</head>
<body>

<header>
  <div class="logo">LOGIC<span>SCOPE</span> <span style="font-size:0.6rem;color:var(--dim)">v1.0</span></div>
  <div class="status-bar">
    <div class="status-item">
      <div class="dot" id="dot-fpga"></div>
      <span id="lbl-fpga">FPGA IDLE</span>
    </div>
    <div class="status-item">
      <div class="dot active"></div>
      <span>100 MHz</span>
    </div>
    <div class="status-item">
      <span id="lbl-fps" style="color:var(--dim)">— FPS</span>
    </div>
    <div class="status-item">
      <span id="lbl-ts" style="color:var(--dim)">--:--:--</span>
    </div>
  </div>
</header>

<div class="controls">
  <button class="btn btn-run"    onclick="startCapture()">▶ RUN</button>
  <button class="btn btn-stop"   onclick="stopCapture()">■ STOP</button>
  <button class="btn btn-export" onclick="exportCSV()">⬇ CSV</button>

  <div class="ctrl-group">
    <label>Zoom</label>
    <input type="range" id="zoom" min="1" max="20" value="1"
           oninput="zoomLevel=+this.value; redraw()">
    <span id="lbl-zoom">1×</span>
  </div>

  <div class="ctrl-group">
    <label>Trigger</label>
    <select id="trig-ch">
      <option value="0">CH0</option>
      <option value="1">CH1</option>
      <option value="2">CH2</option>
      <option value="3">CH3</option>
    </select>
    <select id="trig-edge">
      <option value="rise">↑ Rise</option>
      <option value="fall">↓ Fall</option>
    </select>
  </div>

  <div class="ctrl-group">
    <label>Rate</label>
    <select id="poll-rate" onchange="setPollRate(this.value)">
      <option value="100">10 FPS</option>
      <option value="200" selected>5 FPS</option>
      <option value="500">2 FPS</option>
      <option value="1000">1 FPS</option>
    </select>
  </div>
</div>

<div class="main">
  <!-- Sidebar -->
  <div class="sidebar">
    <div class="sidebar-title">Channels</div>

    <div class="ch-row active" id="row-0" style="--ch-color:#00ff88" onclick="toggleCh(0)">
      <div class="ch-swatch" style="background:#00ff88"></div>
      <span class="ch-label">CH0</span>
      <span class="ch-state" id="state-0" style="color:#00ff88">—</span>
    </div>
    <div class="ch-row active" id="row-1" style="--ch-color:#ff6b35" onclick="toggleCh(1)">
      <div class="ch-swatch" style="background:#ff6b35"></div>
      <span class="ch-label">CH1</span>
      <span class="ch-state" id="state-1" style="color:#ff6b35">—</span>
    </div>
    <div class="ch-row active" id="row-2" style="--ch-color:#4fc3f7" onclick="toggleCh(2)">
      <div class="ch-swatch" style="background:#4fc3f7"></div>
      <span class="ch-label">CH2</span>
      <span class="ch-state" id="state-2" style="color:#4fc3f7">—</span>
    </div>
    <div class="ch-row active" id="row-3" style="--ch-color:#ce93d8" onclick="toggleCh(3)">
      <div class="ch-swatch" style="background:#ce93d8"></div>
      <span class="ch-label">CH3</span>
      <span class="ch-state" id="state-3" style="color:#ce93d8">—</span>
    </div>

    <div class="stats-block">
      <div>DEPTH  <span class="stat-val" id="s-depth">4096</span></div>
      <div>RATE   <span class="stat-val">100 MHz</span></div>
      <div>WINDOW <span class="stat-val">40.96 µs</span></div>
      <div>IFACE  <span class="stat-val">SPI</span></div>
      <div>BOARD  <span class="stat-val">Spartan7</span></div>
    </div>
  </div>

  <!-- Canvas Area -->
  <div class="canvas-area" id="canvas-area">
    <div class="time-ruler" id="time-ruler"></div>
    <div class="channels-wrap" id="channels-wrap">
      <!-- canvases injected by JS -->
    </div>
    <div class="measure-bar">
      <div class="measure-item">CURSOR <span class="measure-val" id="m-cursor">—</span></div>
      <div class="measure-item">CH0    <span class="measure-val" id="m-ch0">—</span></div>
      <div class="measure-item">CH1    <span class="measure-val" id="m-ch1">—</span></div>
      <div class="measure-item">CH2    <span class="measure-val" id="m-ch2">—</span></div>
      <div class="measure-item">CH3    <span class="measure-val" id="m-ch3">—</span></div>
      <div class="measure-item">FREQ   <span class="measure-val" id="m-freq">—</span></div>
    </div>
    <div id="cursor-line"></div>

    <div class="no-data" id="no-data">
      <div class="big">◈</div>
      <div>No signal captured yet</div>
      <div style="font-size:0.65rem">Press RUN to start acquisition</div>
    </div>
  </div>
</div>

<script>
// =============================================================================
//  State
// =============================================================================
const CHANNELS   = 4;
const COLORS     = ['#00ff88','#ff6b35','#4fc3f7','#ce93d8'];
const CH_NAMES   = ['CH0','CH1','CH2','CH3'];
const SAMPLE_DEPTH = 4096;

let running      = false;
let pollTimer    = null;
let pollInterval = 200;
let zoomLevel    = 1;
let panOffset    = 0;
let channelData  = Array(CHANNELS).fill([]);
let chVisible    = Array(CHANNELS).fill(true);
let lastTs       = 0;
let frameCount   = 0;
let fpsTimer     = performance.now();

// Canvas map: ch → canvas element
const canvases = [];

// =============================================================================
//  Build DOM canvases
// =============================================================================
function buildCanvases() {
  const wrap = document.getElementById('channels-wrap');
  wrap.innerHTML = '';
  for (let ch = 0; ch < CHANNELS; ch++) {
    const row = document.createElement('div');
    row.className = 'ch-canvas-row';
    row.id = `canvas-row-${ch}`;

    const tag = document.createElement('div');
    tag.className = 'ch-tag';
    tag.style.color = COLORS[ch];
    tag.textContent = CH_NAMES[ch];

    const cv = document.createElement('canvas');
    cv.className = 'wave';
    cv.id = `canvas-${ch}`;
    cv.addEventListener('mousemove', onMouseMove);
    cv.addEventListener('mouseleave', onMouseLeave);

    row.appendChild(tag);
    row.appendChild(cv);
    wrap.appendChild(row);
    canvases[ch] = cv;
  }
  resizeCanvases();
}

function resizeCanvases() {
  for (let ch = 0; ch < CHANNELS; ch++) {
    if (!canvases[ch]) continue;
    const row = document.getElementById(`canvas-row-${ch}`);
    canvases[ch].width  = row.clientWidth - 52;
    canvases[ch].height = row.clientHeight;
  }
  drawTimeRuler();
}

// =============================================================================
//  Drawing
// =============================================================================
function drawWaveform(ch, data) {
  const cv = canvases[ch];
  if (!cv) return;
  const ctx = cv.getContext('2d');
  const W = cv.width, H = cv.height;

  ctx.clearRect(0, 0, W, H);

  if (!chVisible[ch]) return;

  // Background grid
  ctx.strokeStyle = 'rgba(255,255,255,0.04)';
  ctx.lineWidth = 1;
  const gridCols = 10;
  for (let i = 0; i <= gridCols; i++) {
    const x = (W / gridCols) * i;
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, H); ctx.stroke();
  }
  ctx.beginPath(); ctx.moveTo(0, H/2); ctx.lineTo(W, H/2); ctx.stroke();

  if (!data || data.length === 0) return;

  // Visible window
  const totalSamples = data.length;
  const visibleSamples = Math.floor(totalSamples / zoomLevel);
  const startIdx = Math.max(0, Math.min(panOffset, totalSamples - visibleSamples));
  const endIdx   = Math.min(totalSamples, startIdx + visibleSamples);
  const slice    = data.slice(startIdx, endIdx);

  const hiY = H * 0.15;
  const loY = H * 0.85;

  ctx.beginPath();
  ctx.strokeStyle = COLORS[ch];
  ctx.lineWidth   = 1.8;
  ctx.shadowColor = COLORS[ch];
  ctx.shadowBlur  = 4;

  for (let i = 0; i < slice.length; i++) {
    const x  = (i / slice.length) * W;
    const y  = slice[i] === 1 ? hiY : loY;
    const xn = ((i + 1) / slice.length) * W;

    if (i === 0) {
      ctx.moveTo(x, y);
    } else {
      const prevY = slice[i-1] === 1 ? hiY : loY;
      if (y !== prevY) {
        ctx.lineTo(x, prevY);  // vertical edge
        ctx.lineTo(x, y);      // step
      } else {
        ctx.lineTo(x, y);
      }
    }
  }
  ctx.stroke();
  ctx.shadowBlur = 0;
}

function redraw() {
  document.getElementById('lbl-zoom').textContent = zoomLevel + '×';
  for (let ch = 0; ch < CHANNELS; ch++) {
    drawWaveform(ch, channelData[ch]);
  }
  drawTimeRuler();
}

function drawTimeRuler() {
  const ruler = document.getElementById('time-ruler');
  const W = ruler.clientWidth - 52;
  ruler.innerHTML = '';

  const svg = document.createElementNS('http://www.w3.org/2000/svg','svg');
  svg.setAttribute('width', W);
  svg.setAttribute('height', 28);
  svg.style.marginLeft = '52px';
  svg.style.display = 'block';

  const totalNs   = (SAMPLE_DEPTH / zoomLevel) * 10;  // 10ns per sample at 100MHz
  const tickCount = 10;
  const tickNs    = totalNs / tickCount;

  for (let i = 0; i <= tickCount; i++) {
    const x = (W / tickCount) * i;
    const timeNs = panOffset * 10 + tickNs * i;
    let label;
    if (timeNs >= 1000) label = (timeNs/1000).toFixed(1)+'µs';
    else                label = timeNs.toFixed(0)+'ns';

    const line = document.createElementNS('http://www.w3.org/2000/svg','line');
    line.setAttribute('x1', x); line.setAttribute('y1', 20);
    line.setAttribute('x2', x); line.setAttribute('y2', 28);
    line.setAttribute('stroke','#1e2530');
    svg.appendChild(line);

    const text = document.createElementNS('http://www.w3.org/2000/svg','text');
    text.setAttribute('x', x+2); text.setAttribute('y', 14);
    text.setAttribute('fill','#4a5568');
    text.setAttribute('font-size','9');
    text.setAttribute('font-family','Share Tech Mono, monospace');
    text.textContent = label;
    svg.appendChild(text);
  }
  ruler.appendChild(svg);
}

// =============================================================================
//  Mouse / Cursor
// =============================================================================
function onMouseMove(e) {
  const cv = e.target;
  const rect = cv.getBoundingClientRect();
  const x = e.clientX - rect.left;
  const W = cv.width;

  // Update cursor line
  const area = document.getElementById('canvas-area');
  const areaRect = area.getBoundingClientRect();
  const line = document.getElementById('cursor-line');
  line.style.left = (e.clientX - areaRect.left) + 'px';
  line.style.display = 'block';

  // Cursor time
  const totalSamples = channelData[0]?.length || SAMPLE_DEPTH;
  const visibleSamples = Math.floor(totalSamples / zoomLevel);
  const sampleIdx = panOffset + Math.floor((x / W) * visibleSamples);
  const timeNs = sampleIdx * 10;
  let timeLabel = timeNs >= 1000 ? (timeNs/1000).toFixed(2)+'µs' : timeNs+'ns';
  document.getElementById('m-cursor').textContent = timeLabel;

  // Channel states at cursor
  for (let ch = 0; ch < CHANNELS; ch++) {
    const val = channelData[ch]?.[sampleIdx];
    document.getElementById(`m-ch${ch}`).textContent =
      val === undefined ? '—' : val ? 'HI' : 'LO';
  }

  // Frequency estimate on CH0
  measureFreq(0, sampleIdx);
}

function onMouseLeave() {
  document.getElementById('cursor-line').style.display = 'none';
}

function measureFreq(ch, cursorIdx) {
  const data = channelData[ch];
  if (!data || data.length < 10) return;

  // Count rising edges in visible window
  const start = panOffset;
  const end   = Math.min(data.length, panOffset + Math.floor(data.length / zoomLevel));
  let edges = 0;
  for (let i = start + 1; i < end; i++) {
    if (data[i] === 1 && data[i-1] === 0) edges++;
  }
  if (edges < 2) { document.getElementById('m-freq').textContent = '—'; return; }
  const windowNs = (end - start) * 10;
  const freqMHz  = (edges / (windowNs * 1e-3)).toFixed(3);
  document.getElementById('m-freq').textContent =
    freqMHz > 1 ? freqMHz + ' MHz' : (freqMHz * 1000).toFixed(1) + ' kHz';
}

// =============================================================================
//  Controls
// =============================================================================
function toggleCh(ch) {
  chVisible[ch] = !chVisible[ch];
  const row = document.getElementById(`row-${ch}`);
  if (chVisible[ch]) row.classList.add('active');
  else               row.classList.remove('active');
  redraw();
}

function startCapture() {
  running = true;
  clearInterval(pollTimer);
  pollTimer = setInterval(fetchData, pollInterval);
  console.log('[UI] Capture started');
}

function stopCapture() {
  running = false;
  clearInterval(pollTimer);
  console.log('[UI] Capture stopped');
}

function setPollRate(ms) {
  pollInterval = parseInt(ms);
  if (running) { startCapture(); }
}

// =============================================================================
//  Data Fetch
// =============================================================================
async function fetchData() {
  try {
    const res  = await fetch('/api/data');
    const json = await res.json();

    if (json.status === 'error') return;

    // Update channel data
    channelData = json.channels;
    lastTs = json.timestamp;

    // Channel current state (last sample)
    for (let ch = 0; ch < CHANNELS; ch++) {
      const last = channelData[ch]?.slice(-1)[0];
      const el   = document.getElementById(`state-${ch}`);
      el.textContent = last === 1 ? 'HI' : 'LO';
    }

    // Status dot
    const dot = document.getElementById('dot-fpga');
    const lbl = document.getElementById('lbl-fpga');
    if (json.status === 'captured') {
      dot.className = 'dot active';
      lbl.textContent = 'CAPTURED';
    } else if (json.status === 'armed') {
      dot.className = 'dot armed';
      lbl.textContent = 'ARMED';
    } else {
      dot.className = 'dot';
      lbl.textContent = 'FPGA IDLE';
    }

    // Hide no-data overlay
    document.getElementById('no-data').style.display = 'none';

    // FPS
    frameCount++;
    const now = performance.now();
    if (now - fpsTimer > 1000) {
      document.getElementById('lbl-fps').textContent = frameCount + ' FPS';
      frameCount = 0;
      fpsTimer   = now;
    }

    // Timestamp
    const d = new Date(lastTs * 1000);
    document.getElementById('lbl-ts').textContent =
      d.toTimeString().split(' ')[0];

    redraw();

  } catch(e) {
    console.error('[Fetch]', e);
  }
}

// =============================================================================
//  Export CSV
// =============================================================================
function exportCSV() {
  if (!channelData[0]?.length) { alert('No data to export'); return; }

  let csv = 'Sample,Time_ns,CH0,CH1,CH2,CH3\n';
  for (let i = 0; i < channelData[0].length; i++) {
    const t = i * 10;
    csv += `${i},${t},${channelData[0][i]},${channelData[1][i]},${channelData[2][i]},${channelData[3][i]}\n`;
  }
  const blob = new Blob([csv], {type:'text/csv'});
  const a    = document.createElement('a');
  a.href     = URL.createObjectURL(blob);
  a.download = `capture_${Date.now()}.csv`;
  a.click();
}

// =============================================================================
//  Keyboard shortcuts
// =============================================================================
document.addEventListener('keydown', e => {
  if (e.key === 'r' || e.key === 'R') startCapture();
  if (e.key === 's' || e.key === 'S') stopCapture();
  if (e.key === '+' || e.key === '=') {
    zoomLevel = Math.min(20, zoomLevel + 1);
    document.getElementById('zoom').value = zoomLevel;
    redraw();
  }
  if (e.key === '-') {
    zoomLevel = Math.max(1, zoomLevel - 1);
    document.getElementById('zoom').value = zoomLevel;
    redraw();
  }
  // Pan with arrow keys
  if (e.key === 'ArrowRight') { panOffset += 64; redraw(); }
  if (e.key === 'ArrowLeft')  { panOffset = Math.max(0, panOffset - 64); redraw(); }
});

// =============================================================================
//  Init
// =============================================================================
window.addEventListener('load', () => {
  buildCanvases();
  window.addEventListener('resize', () => { resizeCanvases(); redraw(); });
});
</script>
</body>
</html>
"""

# =============================================================================
#  Flask Routes
# =============================================================================
@app.route('/')
def index():
    return render_template_string(HTML)

@app.route('/api/data')
def api_data():
    try:
        data = capture_mgr.get_data()
        return jsonify(data)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route('/api/arm', methods=['POST'])
def api_arm():
    try:
        capture_mgr.fpga.arm()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

# =============================================================================
#  Entry Point
# =============================================================================
if __name__ == '__main__':
    print("=" * 60)
    print("  LogicScope — Spartan 7 × RPi 4B")
    print("  4 Channels @ 100MHz")
    print("=" * 60)

    capture_mgr = CaptureManager()

    print("[Web] Starting server on http://0.0.0.0:5000")
    print("[Web] Access from any device on your network!")
    print("[Keys] R=Run  S=Stop  +/-=Zoom  ←/→=Pan")

    try:
        app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)
    except KeyboardInterrupt:
        print("\n[Shutdown] Stopping...")
        capture_mgr.stop()
