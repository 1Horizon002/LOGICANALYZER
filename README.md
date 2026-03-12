# LOGICANALYZER
THE LOGIC ANALYZER BASICALLY TEST TOOL  WCHICH USE TO ANALYZE LOGIC SIGANAL  OF ANY DIGITAL  DEVICE 
----------------------------------------------------------------------------------------------------
SO THIS BUILD IS MADE USING RASPBERRY PI 4B + REAL DIGITAL BOOLEAN BOARD ( SPARTAN 7 SERIES )//

DESIGN OVERVIEW:
------------------------------------------------------
-    RPI IS USE TO SHOW LOGIC SIGNAL WHICH           -
-  ACT AS A DASHBOARD TO SEE LOGICAL SIGNAL          -   
------------------------------------------------------
            ///////
-------------------------------------------------------------
-    THE FPGA IS USE FOR  READ LOGICAL SIGNAL THROUGH       -
-  GPIO AND ACT AS BUFFER , SEND THIS SIGNAL DATA TO RPI    -   
-------------------------------------------------------------

# LogicScope — 4-Channel 100MHz Logic Analyzer
### Spartan 7 FPGA + Raspberry Pi 4B

---



## Hardware Wiring

```
Spartan 7 Board          Raspberry Pi 4B
─────────────────        ───────────────
SPI_CS_N    ──────────── GPIO8  (Pin 24) CE0
SPI_MISO    ──────────── GPIO9  (Pin 21) MISO
SPI_MOSI    ──────────── GPIO10 (Pin 19) MOSI
SPI_SCLK    ──────────── GPIO11 (Pin 23) SCLK
GND         ──────────── GND    (Pin 6)

FPGA Probe Inputs (from your digital circuit):
CH0 → FPGA IO pin (see constraints.xdc)
CH1 → FPGA IO pin
CH2 → FPGA IO pin
CH3 → FPGA IO pin
⚠️  Max input: 3.3V. Use voltage divider for 5V signals.
```

---

## FPGA Setup (Vivado)

1. Open Vivado → Create New Project → Spartan-7 (e.g. xc7s50csga324-1)
2. Add `logic_analyzer_top.v` as design source
3. Add `constraints.xdc` — **edit pin names to match your board**
4. Run Synthesis → Implementation → Generate Bitstream
5. Program FPGA via Vivado Hardware Manager

---

## RPi 4B Setup

```bash
# Copy files to RPi
scp -r rpi/ pi@<rpi-ip>:~/logic_analyzer/

# SSH into RPi
ssh pi@<rpi-ip>

# Run setup (enables SPI, installs deps)
chmod +x setup.sh && sudo ./setup.sh

# Reboot to activate SPI
sudo reboot

# Run the analyzer
cd ~/logic_analyzer
sudo python3 logic_analyzer.py
```

Open browser: **http://\<rpi-ip\>:5000**

---

## System Specs

| Parameter | Value |
|---|---|
| Channels | 4 |
| Sample Rate | 100 MHz |
| Sample Depth | 4096 per channel |
| Capture Window | 40.96 µs |
| Trigger | Rising edge CH0 (or external) |
| FPGA→RPi Link | SPI @ 10 MHz |
| Transfer Time | ~3.3 ms per burst |
| Web UI | Flask, any browser |

---

## Keyboard Shortcuts (Web UI)

| Key | Action |
|---|---|
| R | Start capture |
| S | Stop capture |
| + / - | Zoom in / out |
| ← / → | Pan left / right |

---

## LED Status (on FPGA board)

| LED Pattern | Meaning |
|---|---|
| `0001` | Idle — waiting for ARM command |
| `0011` | Armed — waiting for trigger |
| `0111` | Capturing samples |
| `1111` | Done — data ready to read |

---

## Troubleshooting

**No data / all zeros**
- Check SPI wiring — especially MISO/MOSI not swapped
- Verify `dtparam=spi=on` in /boot/config.txt
- Run `ls /dev/spi*` to confirm spidev is present

**FPGA not responding**
- Check bitstream programmed correctly
- Verify 3.3V IO voltage on FPGA
- Check GND is shared between both boards

**Probe signal corrupt**
- Keep probe wires short (< 20cm)
- Add 100Ω series resistor on each probe input
- Do NOT connect 5V signals directly — use voltage divider
            
