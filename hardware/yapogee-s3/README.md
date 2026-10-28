# Yapogee S3

ESP32-S3 DVB-S2 transmitter daughterboard for PigeonVision. The CM5 supplies MPEG-TS over SPI. The S3 encodes and pulse-shapes the waveform through its own 2.42 GHz radio; an integrated PLL/VCO/mixer converts it to approximately 1.28 GHz. A driver feeds the separate power-amplifier and final-filter board.

Open the [KiCad project](kicad/YapogeeS3.kicad_pro). [Project and library setup](kicad/README.md).

| Interface | Design basis |
| --- | --- |
| Power | Nominal 5 V from the carrier; local digital and RF regulation |
| Payload | 3.3 V SPI and READY; quad data reserved where compatible |
| Modem | ESP32-S3 with 8 MB octal PSRAM |
| Conversion | 2420 MHz input, 1140 MHz LO, 1280 MHz output |
| RF output | 50 ohms into the separate PA/filter board |
| Service | USB programming, boot/reset and test access |

Component libraries live in the shared `hardware/libraries` folder. The schematic and PCB are under development. The BOM and library parts are being selected and checked for circuit capture. Layout, matching verification and hardware measurements are required before fabrication release.
