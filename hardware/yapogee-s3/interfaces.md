# Electrical and mechanical interface

Prototype contract, version 0.8. [Dimensions and receiving lands](mechanical-interface.json). A complete component fit and routed PCB have not been established.

## Power

Carrier input: 4.75–5.25 V, with a 1.5 A daughterboard allocation. USB-only power requires a known 5 V source providing at least 1.5 A. Keep board input at or below 5.5 V.

J1 is the castellated module edge; J1.1 is GND and J1.3 is the external input; J1.16 is GND. USB VBUS reaches the common `5V_IN` rail through JP1. Leave JP1 open for external power, including USB debugging. Close it for standalone USB-only operation before installation, with the external carrier disconnected. When mounted, keep JP1 open and power the daughterboard from the carrier. USB data is supported only while the carrier is on; independent Pi PMIC-off operation is unsupported. The two 5.1 kΩ CC pull-downs establish USB sink attachment; current availability is not detected automatically.

TPS62933FDRLR provides main 3.328 V nominal with a Bourns SRP5030TA-2R2M, 2.2 µH inductor. The module and interfaces use this rail. TPS22919 and a 1 Ω / 22 µF filter feed the SPF5043Z driver.

TPS7A9001DSKR provides quiet 3.000 V from `5V_IN` for the mixer, TCXO and reference buffer. Use the thermal pad, 33 kΩ / 12 kΩ feedback and 560 Ω minimum-load bleeder. Two 22 µF GCM31CR71A226KE02L are fitted at each input/output (44 µF nominal per side). TI's capacitor guidance includes 50% derating. The adopted interpretation and effective-capacitance screens are in Design Master. The RFFC operating range is 2.7–3.3 V.

Calculations and supply corners are in [Design Master](design-master.xlsx). The external PA is outside this allocation.

## Castellations

Top view; module origin is its upper-left corner. The reference envelope is 55 × 43.18 mm. Two separate 1×8 header rows use 2.54 mm pitch; each header hole is paired with an edge castellation. Castellations use 0.80 mm drills / 1.40 mm copper, and intact header holes use 1.10 mm drills / 1.80 mm copper. J1 carries the real closed Edge.Cuts outline, including R1 outer corners and the Samtec notch. The matching carrier footprint uses drawing guides.

| Pad | Function | Pad | Function |
| ---: | --- | ---: | --- |
| 1 | GND | 9 | GND |
| 2 | SCLK | 10 | D3 |
| 3 | 5V_CARRIER | 11 | READY |
| 4 | CS_N | 12 | GND |
| 5 | D0 | 13 | PA_EN |
| 6 | GND | 14 | Reserved |
| 7 | D1 | 15 | GND |
| 8 | D2 | 16 | GND |

SCLK, CS_N and D0–D3 flow into the transmitter; READY and PA_EN return to the carrier. Pad 14 is reserved and unconnected. The host interface uses direct 3.3 V GPIO. Neither endpoint may drive or pull up an unpowered receiver. The carrier must provide a 4.7 kΩ CS_N pull-up to Pi 3V3; that carrier change is not yet captured. Startup, stop and supply-voltage corners remain to be qualified. Twelve perimeter GND anchors share pad number 1: 28 solder-down contacts, including 18 GND sites, retain the same 16 logical numbers. Header rows run north: 1, 2, 4, 5, 7, 8, 10, 9; south: 3, 6, 11, 12, 13, 14, 15, 16. Use the coordinate table in mechanical-interface.json for the mating carrier.

PA_EN is a request to the Pi. The Pi owns the separate PA power enable. Supply the installed daughterboard from carrier 5V_MAIN so it can issue that request while PA power is off. Carrier routing and firmware remain unfinished.

The top sheet shows the module edge as a strip. One CARRIER bus port groups SCLK, CS_N, D0–D3, READY and PA_EN. Power uses global rail symbols. The MCU sheet retains the RF control translator; the two host translators are removed and GPIO2 is free. Only SCLK, SDATA, ENX_N and ENBL cross to the RF sheet, at the quiet 3.0 V logic level. RF translator DIR and OE stay local.

Initialize ENX high, SCLK low, ENBL low and SDATA direction before enabling the RF translator. Its data ports include internal pull-downs; ENX uses the fitted 4.7 kΩ pull-up on the RF side.

J1 uses the 55 × 43.18 mm Edge_Header16_GND12 footprint; the shared carrier-landing symbol uses the matching 28-land footprint. These PCB features are excluded from the purchased BOM. Carrier lands omit paste for edge hand soldering. Optional bench headers mount underneath; omit them and all underside parts for flush carrier assembly. Intact header holes sit 1.60 mm inboard with a 0.65 mm nominal drill web. Continuous 1.8 × 3.4 mm oval top copper lands are exposed; no mask barrier against solder wicking is claimed. Carrier lands stop 0.60 mm inboard, 0.10 mm before the header copper. Retain manual soldering with no carrier paste.

## Connectors and shield

SMA-J-P-H-ST-EM3 occupies the left notch at local (0,34.27) mm. Its signal land sits inward of the back wall. The proposed carrier opening continues to the existing carrier edge for the body and mating nut. The receiving footprint shows that proposal on Dwgs.User and does not change carrier Edge.Cuts. Check the actual cable envelope.

USB-C is at local (3.675,21) mm, rotation 270°, opening left. Proposed shield-frame center is (32.5,21.59) mm. Buy BMI-S-206-C separately; nominal assembled shield height is 5.21 mm. Reserve at most 3.5 mm component height underneath. Keep the buck outside the frame with continuous L2 GND.

U1 is the WROOM-1U-N16R8 module at local center (27.45,16.81) mm. It integrates 16 MB flash, 8 MB octal PSRAM, the crystal and chip RF matching. Module pads 28–30 (GPIO35–37) are reserved for PSRAM. Existing payload/control GPIO assignments are retained, with module pad numbers in the capture handoff.

W501 links U1's integrated U.FL socket to J3 at local (45.45,20.67) mm. J3 feeds the existing input pad/balun/DC blocks. The Dwgs.User harness guide has a 19 mm centerline. The selected cable is 25 ± 5 mm between plug mating axes; 23 mm would exceed the shortest specified assembly. The 19 mm guide reserves 1 mm before height and bend effects. Check actual plug height, bend, longer-cable slack and retention before freezing poses. The Hirose J3 prototype footprint omits paste for hand assembly.

ESP32, USB-C, U.FL and SMA start positions are loaded; other component positions are unchanged. Carrier openings remain proposals. Plan top-side parts for flush carrier mounting. Further carrier cutouts may accommodate selected underside parts if later placement reduces board size.

Before fabrication, resolve the SMA's R0.40 mm reliefs and ±0.10 mm notch requirement with the fabricator. Standard routing radius/tolerance must not silently replace that geometry. Specify castellated-hole processing and keep panel tabs clear. Final carrier routing, mechanical loading, cable fit and assembly remain to be checked.

JP2 BOOT and JP3 RESET are bare, normally-open PCB jumper pads. For manual download, close BOOT during reset. Leave both open for normal operation; briefly short RESET to pull EN to GND. Native USB provides the usual ESP flashing path, without a UART bridge or Pi BOOT control.
