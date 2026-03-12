// =============================================================================
//  Logic Analyzer Top Module — Xilinx Spartan 7
//  4 Channels @ 100MHz Sample Rate
//  Communication: SPI Slave → Raspberry Pi 4B
//
//  Pin Map:
//    CH0..CH3   — Probe input pins (3.3V logic)
//    SPI_SCLK   — SPI Clock from RPi (GPIO11)
//    SPI_MOSI   — SPI MOSI from RPi (GPIO10) [not used for TX-only]
//    SPI_MISO   — SPI MISO to RPi   (GPIO9)
//    SPI_CS_N   — SPI Chip Select   (GPIO8)
//    TRIG_IN    — External trigger input (optional)
//    LED[3:0]   — Status LEDs
// =============================================================================

module logic_analyzer_top (
    input  wire        clk_100mhz,    // 100MHz board oscillator
    input  wire        rst_n,         // Active-low reset button

    // ── Probe Inputs ──────────────────────────────────────────────────────
    input  wire [3:0]  probe,         // CH0..CH3

    // ── SPI Slave Interface (to Raspberry Pi 4B) ─────────────────────────
    input  wire        spi_sclk,
    input  wire        spi_cs_n,
    input  wire        spi_mosi,      // CMD bytes from RPi
    output wire        spi_miso,      // Sample data to RPi

    // ── Trigger ───────────────────────────────────────────────────────────
    input  wire        trig_ext,      // External trigger (optional)

    // ── Status LEDs ───────────────────────────────────────────────────────
    output reg  [3:0]  led
);

// =============================================================================
//  Parameters
// =============================================================================
parameter SAMPLE_DEPTH  = 4096;       // Samples per capture
parameter ADDR_BITS     = 12;         // log2(4096)

// CMD bytes from RPi
parameter CMD_ARM       = 8'hAA;      // Arm capture
parameter CMD_READ      = 8'hBB;      // Start read burst
parameter CMD_STATUS    = 8'hCC;      // Read status byte

// =============================================================================
//  Internal Signals
// =============================================================================
wire clk = clk_100mhz;
wire rst = ~rst_n;

// ── Sample memory (4 bits × 4096 depth) ──────────────────────────────────
reg [3:0] sample_mem [0:SAMPLE_DEPTH-1];

// ── Write side ────────────────────────────────────────────────────────────
reg [ADDR_BITS-1:0] wr_ptr   = 0;
reg                 capturing = 0;
reg                 buf_full  = 0;

// ── Trigger logic ─────────────────────────────────────────────────────────
reg [3:0] probe_sync0, probe_sync1, probe_sync2;  // 3-stage CDC sync
wire [3:0] probe_s = probe_sync2;

// trigger on any rising edge on CH0 (configurable via MOSI — simplified here)
wire trig_rise = (~probe_sync2[0]) & probe_sync1[0];
wire triggered = trig_rise | trig_ext;

// ── State machine ─────────────────────────────────────────────────────────
localparam S_IDLE    = 2'd0,
           S_WAIT    = 2'd1,
           S_CAPTURE = 2'd2,
           S_DONE    = 2'd3;

reg [1:0] state = S_IDLE;

// =============================================================================
//  Input synchronizer (3-stage for metastability)
// =============================================================================
always @(posedge clk or posedge rst) begin
    if (rst) begin
        probe_sync0 <= 0;
        probe_sync1 <= 0;
        probe_sync2 <= 0;
    end else begin
        probe_sync0 <= probe;
        probe_sync1 <= probe_sync0;
        probe_sync2 <= probe_sync1;
    end
end

// =============================================================================
//  Capture State Machine  (runs at 100 MHz)
// =============================================================================
always @(posedge clk or posedge rst) begin
    if (rst) begin
        state     <= S_IDLE;
        wr_ptr    <= 0;
        buf_full  <= 0;
        capturing <= 0;
        led       <= 4'b0001;
    end else begin
        case (state)
            // ── Idle: wait for ARM command from RPi via SPI ───────────────
            S_IDLE: begin
                led      <= 4'b0001;
                buf_full <= 0;
                wr_ptr   <= 0;
                if (arm_pulse) begin
                    state <= S_WAIT;
                end
            end

            // ── Armed: wait for trigger ────────────────────────────────────
            S_WAIT: begin
                led <= 4'b0011;
                if (triggered) begin
                    state     <= S_CAPTURE;
                    capturing <= 1;
                end
            end

            // ── Capture: fill sample buffer ────────────────────────────────
            S_CAPTURE: begin
                led <= 4'b0111;
                sample_mem[wr_ptr] <= probe_s;
                wr_ptr <= wr_ptr + 1;
                if (wr_ptr == SAMPLE_DEPTH - 1) begin
                    state     <= S_DONE;
                    capturing <= 0;
                    buf_full  <= 1;
                end
            end

            // ── Done: hold until RPi reads all data, then re-arm ──────────
            S_DONE: begin
                led <= 4'b1111;
                if (read_done) begin
                    state    <= S_IDLE;
                    buf_full <= 0;
                end
            end
        endcase
    end
end

// =============================================================================
//  SPI Slave (Mode 0 — CPOL=0, CPHA=0)
//  RPi sends 1 CMD byte, then clocks out SAMPLE_DEPTH bytes (4 bits per
//  sample packed into lower nibble of each byte)
// =============================================================================

// ── SPI input sync (cross clock domain) ──────────────────────────────────
reg sclk_r0, sclk_r1, sclk_r2;
reg cs_r0,   cs_r1,   cs_r2;
reg mosi_r0, mosi_r1;

always @(posedge clk) begin
    sclk_r0 <= spi_sclk; sclk_r1 <= sclk_r0; sclk_r2 <= sclk_r1;
    cs_r0   <= spi_cs_n; cs_r1   <= cs_r0;   cs_r2   <= cs_r1;
    mosi_r0 <= spi_mosi; mosi_r1 <= mosi_r0;
end

wire sclk_rise = (~sclk_r2) & sclk_r1;   // rising edge
wire sclk_fall = sclk_r2 & (~sclk_r1);   // falling edge
wire cs_active = ~cs_r1;                  // active low
wire cs_start  = cs_r2 & (~cs_r1);       // CS falling edge

// ── SPI receive (CMD byte) ────────────────────────────────────────────────
reg [7:0] rx_shift  = 0;
reg [2:0] rx_bit    = 7;
reg       rx_valid  = 0;
reg [7:0] rx_byte   = 0;
reg       in_cmd    = 0;   // first 8 bits are CMD

// ── SPI transmit (sample data) ────────────────────────────────────────────
reg [ADDR_BITS-1:0] rd_ptr   = 0;
reg [7:0]           tx_shift = 0;
reg [2:0]           tx_bit   = 7;
reg                 reading  = 0;
reg                 read_done_r = 0;

wire read_done = read_done_r;
wire arm_pulse;
reg  arm_r = 0;
reg  arm_rr = 0;
assign arm_pulse = arm_r & ~arm_rr;  // 1-cycle pulse

always @(posedge clk) arm_rr <= arm_r;

reg miso_reg = 1;
assign spi_miso = miso_reg;

always @(posedge clk or posedge rst) begin
    if (rst) begin
        rx_bit      <= 7;
        rx_valid    <= 0;
        rd_ptr      <= 0;
        tx_bit      <= 7;
        reading     <= 0;
        read_done_r <= 0;
        arm_r       <= 0;
        in_cmd      <= 0;
        miso_reg    <= 1;
    end else begin
        rx_valid    <= 0;
        read_done_r <= 0;
        arm_r       <= 0;

        // ── CS falling: start of transaction ─────────────────────────────
        if (cs_start) begin
            rx_bit  <= 7;
            tx_bit  <= 7;
            in_cmd  <= 1;
            reading <= 0;
            rd_ptr  <= 0;
            // pre-load first sample into tx_shift
            tx_shift <= {4'b0000, sample_mem[0]};
        end

        if (cs_active) begin
            // ── Rising edge: sample MOSI, shift out MISO ─────────────────
            if (sclk_rise) begin
                if (in_cmd) begin
                    // Receive CMD byte
                    rx_shift <= {rx_shift[6:0], mosi_r1};
                    if (rx_bit == 0) begin
                        rx_byte  <= {rx_shift[6:0], mosi_r1};
                        rx_valid <= 1;
                        in_cmd   <= 0;
                        rx_bit   <= 7;
                        // Process CMD
                        if ({rx_shift[6:0], mosi_r1} == CMD_ARM)
                            arm_r <= 1;
                        else if ({rx_shift[6:0], mosi_r1} == CMD_READ)
                            reading <= 1;
                    end else begin
                        rx_bit <= rx_bit - 1;
                    end
                end else if (reading) begin
                    // Clock out sample data
                    if (tx_bit == 0) begin
                        // Move to next sample
                        rd_ptr   <= rd_ptr + 1;
                        tx_bit   <= 7;
                        tx_shift <= {4'b0000, sample_mem[rd_ptr + 1]};
                        if (rd_ptr == SAMPLE_DEPTH - 1) begin
                            reading     <= 0;
                            read_done_r <= 1;
                        end
                    end else begin
                        tx_shift <= {tx_shift[6:0], 1'b0};
                        tx_bit   <= tx_bit - 1;
                    end
                end
            end

            // ── Falling edge: update MISO (data valid before next rise) ──
            if (sclk_fall && reading) begin
                miso_reg <= tx_shift[7];
            end
        end
    end
end

endmodule
