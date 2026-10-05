# RF frontend

**Current plan:** AFE7071 transmitter or E200 → source-specific attenuation / optional driver → GRF5613 → filter → antenna. Target 0.5 W average DVB-S2 output at the PA, initially at 1.28 GHz. RF performance and loaded power consumption remain unmeasured.

![RF frontend block diagram](block-diagram.svg)

[Edit diagram](block-diagram.drawio) · [Link budget](../../calculations/link-budget.xlsx)

| Stage | Part / starting value |
| --- | --- |
| Input pad | 50 Ω; size from the selected source's measured output and waveform peaks |
| Driver | GRF2011 optional; evaluate direct E200 drive first |
| Interstage pad | Value after gain / peak-level calculation |
| PA | GRF5613 selected; use the EVB184 1240–1420 MHz reference tune for 1.28 GHz |
| Output filter | LFCN-1500+ candidate; verify attenuation at required frequencies |

The preferred mechanical concept is a PA daughterboard soldered to a carrier GND contact area, with thermal vias through both boards and a TIM-coupled heatsink below the carrier. Preserve ground copper beneath the PA. Mounting geometry, contact area and heat rejection remain unverified. Two low-profile heatsink planning footprints and envelope models are available in the [project library](../libraries/README.md), targeting 12.7 mm total underside clearance.

The optional breakout interface is a planned four-circuit Molex **0430450418** Micro-Fit 3.0 connector. The same power/control signals are needed for the soldered module; its lands have not been defined. No PA connector or control wiring has been added to the carrier schematic yet.

| Connector pin | Signal | Carrier connection |
| --- | --- | --- |
| 1 | 5V_RF | Switched RF buck output |
| 2 | GND | Power return |
| 3 | PA_EN | CM5 GPIO22, J1.46; 3.3 V active-high enable request |
| 4 | GND | Control return; common with pin 2 |

CM5 GPIO17, J1.50 controls the RF buck separately through **5V_RF_EN**. Keep the **1.5 A / 7.5 W** PA planning allowance until modulated-load measurements are available. The TPS62933 IC is rated for 3 A; this does not establish the assembled rail's continuous thermal capacity. EVB184 reports approximately 309 mA quiescent current at 5 V, which is not the loaded maximum.

The PA board provides local RF chokes, decoupling, matching, bias resistors, shutdown logic and a thermal path from the exposed pad. Its PA_EN input should have a 10 kΩ pull-down and keep the PA off when the carrier is unpowered or the cable is disconnected. GRF5613 **VSHDN is high to disable**, so local circuitry must handle this polarity and the VEN1 / VEN2 bias sequencing; do not connect those bias pins directly to the GPIO. Mute the transmitter before changing PA bias or rail power. Use the EVB184 layout and matching as the starting point; this remains an RF layout and thermal design, even with a simple carrier interface.

The existing RF divider remains **R14 = 51 kΩ, R21 = 1.5 kΩ, R22 = 10 kΩ**, giving 5.000 V nominal. At an assumed 125°C resistor temperature, ±1% initial tolerance, opposite ±100 ppm/°C drift, 0.816 V maximum feedback reference and 0.15 µA maximum feedback input current give a **5.283 V static upper corner**, above the GRF5613's **5.25 V** supply ceiling. Changing R21 to 0 Ω is a **4.880 V nominal candidate**, with a 5.155 V upper corner under the same assumptions. That change is not applied. These calculations exclude ripple, startup and load-release transients; PA supply compliance and RF performance at the lower voltage need measurement. See the RF regulator calculation in [Design Master](../../calculations/design-master.xlsx).

SMA input/output. Size attenuation from measured gain and waveform peaks. A 1 dB filter loss leaves about 0.4 W. Check modulation quality, spectrum and temperature; filtering cannot fix clipping.

[GRF5613 datasheet](https://www.guerrilla-rf.com/includes/prodFiles/5613/GRF5613DS.pdf) · [GRF5613 EVB184 tune](https://www.guerrilla-rf.com/includes/prodFiles/5613/GRF5613%20EVB184%201240-1420%20MHz.pdf) · [GRF2011](https://www.guerrilla-rf.com/products/detail/sku/GRF2011) · [LFCN-1500+](https://www.minicircuits.com/pdfs/LFCN-1500%2B.pdf) · [Molex 0430450418](https://www.digikey.com/en/products/detail/molex/0430450418/2467320)
