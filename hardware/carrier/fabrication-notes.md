# Carrier fabrication

Use JLC06161H-3313, six layers, the 1.6 mm ordering category, 1 oz outer / 0.5 oz inner copper, green soldermask and ENIG. The exact nominal dimensions and impedance inputs are in [routing-plan.json](routing-plan.json).

Request 100 Ω CSI/Ethernet, 90 Ω USB and 50 Ω single-ended impedance control using the recorded outer and L3 geometries. Confirm the final production stackup and impedance coupon with the fabricator.

Use epoxy-filled and copper-capped mechanical through vias (POFV). The minimum drill is 0.30 mm. The compact 0.45 and 0.50 mm lands with 0.30 mm drills have 0.075 and 0.10 mm annular rings. The standard 0.60 mm land with a 0.30 mm drill has a 0.15 mm ring. Apply resin filling and plated capping (IPC-4761 Type VII) to all vias marked on the filling and capping Gerbers. The 0.30, 0.40 and 0.50 mm drills in those files are vias. Leave component through-holes, plated slots and mounting holes open. Confirm the process and via locations in the JLCPCB CAM preview. [JLC process](https://jlcpcb.com/help/article/via-in-pad-pofv-guide) · [Capabilities](https://jlcpcb.com/capabilities/pcb-capabilities)

The camera connector's 0.60 × 1.50 mm lands can contain the signal-via land. The CM5 lands are only 0.20 mm wide on 0.40 mm pitch, so use short, staggered escapes there. Place GND return vias beside signal-layer transitions. Via geometry and stubs still need layout review.
