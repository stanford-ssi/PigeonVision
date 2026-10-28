# Electrical and mechanical interface

Prototype contract, version 0.3. [Dimensions and receiving lands](mechanical-interface.json). A complete component fit and routed PCB have not been established.

## Power

Carrier input: 4.75–5.25 V, with a 1.5 A daughterboard allocation. Standalone USB-C requires a source advertising 1.5 A or 3 A. STUSB4500L detects capability without MCU firmware. TPS2116 selects eligible USB; otherwise it selects the powered carrier. With only a default-current USB source, the board remains off. Such a host can provide data while carrier power runs the board.

The selector provides reverse blocking. Source transitions may reset the board and need bench verification. It is not a precise 1.5 A current clamp. The separate PA is outside this power budget.

The main buck is nominally 3.328 V and powers the WROOM module and interfaces. The module's operating range is 3.0–3.6 V; the reset supervisor releases near 3.08 V. The driver has a separately switched 1 ohm / 10 uF local filter. Its gain/compression budget remains based on typical 3 V characterization.

TPS7A9001DSKR generates the quiet 3.0 V converter/reference supply directly from selected 5 V. Connect its thermal pad to GND and provide the selected feedback, noise-bypass/feedforward and bulk capacitors. Its accuracy condition requires at least 5 mA load; retain the 560 ohm bleeder for standby. Confirm input/output effective capacitance and closed-lid temperature. The RFFC's recommended supply is 2.7–3.3 V; 3.6 V is its absolute maximum.

USB VBUS may reach 5.5 V. That is the source selector's recommended upper limit; the regulator ratings do not raise the assembled board's permitted input.

## Castellations

Top view; module origin is its upper-left corner. Main centers run from (18,0) to (48,0) mm in 2 mm steps. Each uses a 1.00 mm plated drill and 1.60 mm copper diameter, bisected by the board edge.

| Pad | Function | Pad | Function |
| ---: | --- | ---: | --- |
| 1 | GND | 9 | GND |
| 2 | SCLK | 10 | D3 |
| 3 | GND | 11 | READY |
| 4 | CS_N | 12 | GND |
| 5 | D0 | 13 | PA_EN |
| 6 | GND | 14 | HOST_3V3 |
| 7 | D1 | 15 | GND |
| 8 | D2 | 16 | 5V_CARRIER |

SCLK, CS_N and D0–D3 flow into the transmitter; READY and PA_EN return to the carrier. HOST_3V3 is the translators' carrier-side reference, separate from the local regulator output. Extra GND anchors at (0,8), (16,40) and (48,40) mm share pad number 1: 19 physical pads, 16 electrical numbers.

Library symbols YapogeeS3_ModuleEdge_Interface16 and YapogeeS3_CarrierLanding_Interface16 have matching footprints. These PCB features are excluded from the purchased BOM. Prototype carrier lands omit paste for hand soldering.

## Connectors and shield

SMA-J-P-H-ST-EM3 occupies the left notch at local (0,33) mm. Its signal land sits inward of the back wall. The proposed carrier opening continues to the existing carrier edge for the body and mating nut. The receiving footprint shows that proposal on Dwgs.User and does not change carrier Edge.Cuts. Check the actual cable envelope.

USB-C is at local (3.675,21) mm, rotation 270°, opening left. Shield-frame center is (33.25,20) mm. Buy BMI-S-206-C separately; nominal assembled shield height is 5.21 mm. Reserve at most 3.5 mm component height underneath. Keep the buck/selector outside the frame with continuous L2 GND.

U101 is the WROOM-1U-N16R8 module at local center (26,15.1) mm. It integrates 16 MB flash, 8 MB octal PSRAM, the crystal and chip RF matching. Module pads 28–30 (GPIO35–37) are reserved for PSRAM. Existing payload/control GPIO assignments are retained, with module pad numbers in the capture handoff.

W501 links U101's integrated U.FL socket to J502 at local (48,20.96) mm. J502 feeds the existing input pad/balun/DC blocks. The 25 mm cable centerline on Dwgs.User is a nominal envelope, not a qualified cable fit or PCB trace. Check length datum, bend, plug height and retention before freezing poses. The Hirose J502 prototype footprint omits paste for hand assembly.

Current placement uses top-side parts. Further carrier cutouts may accommodate selected underside parts if later placement reduces board size.

Before fabrication, resolve the SMA's R0.40 mm reliefs and ±0.10 mm notch requirement with the fabricator. Standard routing radius/tolerance must not silently replace that geometry. Specify castellated-hole processing and keep panel tabs clear. Final carrier routing, mechanical loading, cable fit and assembly remain to be checked.
