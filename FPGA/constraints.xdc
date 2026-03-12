# =============================================================================
#  Constraints File — Xilinx Spartan 7 (XC7S50 / Arty S7 style board)
#  Logic Analyzer Project
#  Modify pin names to match YOUR specific Spartan 7 board variant
# =============================================================================

# ── Clock ─────────────────────────────────────────────────────────────────────
set_property PACKAGE_PIN R2      [get_ports clk_100mhz]
set_property IOSTANDARD  LVCMOS33 [get_ports clk_100mhz]
create_clock -period 10.000 -name sys_clk [get_ports clk_100mhz]

# ── Reset (active-low pushbutton) ─────────────────────────────────────────────
set_property PACKAGE_PIN C2      [get_ports rst_n]
set_property IOSTANDARD  LVCMOS33 [get_ports rst_n]

# ── Probe Inputs (CH0..CH3) ───────────────────────────────────────────────────
# Connect to PMOD or GPIO header pins — adjust as needed
set_property PACKAGE_PIN D3      [get_ports {probe[0]}]
set_property PACKAGE_PIN F3      [get_ports {probe[1]}]
set_property PACKAGE_PIN G2      [get_ports {probe[2]}]
set_property PACKAGE_PIN H2      [get_ports {probe[3]}]
set_property IOSTANDARD  LVCMOS33 [get_ports {probe[*]}]

# Pull-down on probe inputs (signal is 0 when not connected)
set_property PULLDOWN TRUE [get_ports {probe[*]}]

# ── SPI Interface (to Raspberry Pi 4B) ───────────────────────────────────────
# Connect RPi GPIO9  (MISO) ← Spartan MISO
# Connect RPi GPIO10 (MOSI) → Spartan MOSI
# Connect RPi GPIO11 (SCLK) → Spartan SCLK
# Connect RPi GPIO8  (CE0)  → Spartan CS_N
set_property PACKAGE_PIN J2      [get_ports spi_sclk]
set_property PACKAGE_PIN K2      [get_ports spi_cs_n]
set_property PACKAGE_PIN L2      [get_ports spi_mosi]
set_property PACKAGE_PIN M2      [get_ports spi_miso]
set_property IOSTANDARD  LVCMOS33 [get_ports spi_sclk]
set_property IOSTANDARD  LVCMOS33 [get_ports spi_cs_n]
set_property IOSTANDARD  LVCMOS33 [get_ports spi_mosi]
set_property IOSTANDARD  LVCMOS33 [get_ports spi_miso]

# ── External Trigger ──────────────────────────────────────────────────────────
set_property PACKAGE_PIN N2      [get_ports trig_ext]
set_property IOSTANDARD  LVCMOS33 [get_ports trig_ext]
set_property PULLDOWN    TRUE     [get_ports trig_ext]

# ── Status LEDs ───────────────────────────────────────────────────────────────
set_property PACKAGE_PIN E1      [get_ports {led[0]}]
set_property PACKAGE_PIN F1      [get_ports {led[1]}]
set_property PACKAGE_PIN G1      [get_ports {led[2]}]
set_property PACKAGE_PIN H1      [get_ports {led[3]}]
set_property IOSTANDARD  LVCMOS33 [get_ports {led[*]}]

# =============================================================================
#  Timing Constraints
# =============================================================================

# SPI clock is async to system clock — set as false path
set_property CLOCK_DEDICATED_ROUTE FALSE [get_nets spi_sclk]
set_clock_groups -asynchronous \
    -group [get_clocks sys_clk] \
    -group [get_clocks -of_objects [get_ports spi_sclk]]

# Relax timing on SPI paths (data clocked by sys_clk, so 10ns budget)
set_input_delay  -clock sys_clk -max 3.0 [get_ports spi_mosi]
set_input_delay  -clock sys_clk -min 0.5 [get_ports spi_mosi]
set_output_delay -clock sys_clk -max 3.0 [get_ports spi_miso]
set_output_delay -clock sys_clk -min 0.5 [get_ports spi_miso]

# Probe inputs — no setup/hold constraints needed (sampled at 100MHz)
set_input_delay  -clock sys_clk 2.0 [get_ports {probe[*]}]

# =============================================================================
#  Configuration
# =============================================================================
set_property CFGBVS         VCCO [current_design]
set_property CONFIG_VOLTAGE  3.3  [current_design]
set_property BITSTREAM.GENERAL.COMPRESS TRUE [current_design]
