# Electrical and mechanical interface

Prototype contract, version 0.2. [Dimensions and receiving lands](mechanical-interface.json). A complete component fit and routed PCB have not been established.

## Power

Carrier input: 4.75–5.25 V, with a 1.5 A daughterboard allocation. Standalone USB-C requires a source advertising 1.5 A or 3 A. STUSB4500L detects capability without MCU firmware. TPS2116 selects eligible USB; otherwise it selects the powered carrier. With only a default-current USB source, the board remains off. Such a host can provide data while carrier power runs the board.

The selector provides reverse blocking. Source transitions may reset the board and need bench verification. It is not a precise 1.5 A current clamp. The separate PA is outside this power budget.

The digital buck is nominally 3.456 V. Measured ripple/transients must remain within 3.3–3.6 V to preserve the RF LDO's specified input margin and digital maximum operating voltage. The quiet 3.0 V LDO feeds the converter/reference. The driver uses a separately switched, locally filtered digital-supply branch.

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

Current placement uses top-side parts. Further carrier cutouts may accommodate selected underside parts if later placement reduces board size.

Before fabrication, resolve the SMA's R0.40 mm reliefs and ±0.10 mm notch requirement with the fabricator. Standard routing radius/tolerance must not silently replace that geometry. Specify castellated-hole processing and keep panel tabs clear. Final carrier routing, mechanical loading, cable fit and assembly remain to be checked.
