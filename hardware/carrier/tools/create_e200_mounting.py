"""Create an E200 mounting footprint and drawing-derived planning STEP.

Run using Python with ezdxf and OCP installed. The input is MicroPhase's
E200_Mechanical.dxf. All vertical sizes and the carrier drill size are planning
choices, not dimensions established by that two-dimensional drawing.
"""
from pathlib import Path
import argparse
import hashlib
import json
import math
import shutil
import uuid

import ezdxf
from ezdxf.path import make_path
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakePolygon
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder, BRepPrimAPI_MakePrism
from OCP.Bnd import Bnd_Box
from OCP.BRepBndLib import BRepBndLib
from OCP.IFSelect import IFSelect_RetDone
from OCP.Quantity import Quantity_Color, Quantity_TOC_RGB
from OCP.STEPCAFControl import STEPCAFControl_Writer
from OCP.STEPControl import STEPControl_AsIs, STEPControl_Reader
from OCP.TCollection import TCollection_ExtendedString
from OCP.TDataStd import TDataStd_Name
from OCP.TDocStd import TDocStd_Document
from OCP.XCAFDoc import XCAFDoc_DocumentTool, XCAFDoc_ColorGen
from OCP.gp import gp_Pnt, gp_Vec, gp_Ax2, gp_Dir


ROOT = Path(__file__).resolve().parents[3]
NAME = "MicroPhase_ANTSDR_E200_Mounting_80x50mm_Planning"
MODEL = "MicroPhase_ANTSDR_E200_DrawingDerived_Planning.step"
SOURCE_COMMIT = "ea2cd8d9a0c83157a3ecfbc60ee6bee2f3e73d8f"
SOURCE_SHA256 = "0be2aa2cbf8167257d46da50219f0951fc4716ca5deb64f6cc7467be5d5e2277"
SOURCE_URL = f"https://github.com/MicroPhase/antsdr_doc_en/blob/{SOURCE_COMMIT}/mechanical/E200_Mechanical.dxf"


def uid():
    return str(uuid.uuid4())


def bounds(shape):
    box = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape, box, False, False)
    lo, hi = box.CornerMin(), box.CornerMax()
    return [lo.X(), lo.Y(), lo.Z(), hi.X(), hi.Y(), hi.Z()]


def generate(out, drill, gap):
    source = out / "E200_Mechanical.dxf"
    assert hashlib.sha256(source.read_bytes()).hexdigest() == SOURCE_SHA256
    dxf = ezdxf.readfile(source)
    ms = dxf.modelspace()
    # Confirm mm interpretation against the known 2.54-mm header pitch.
    jtag = [e for e in ms.query("CIRCLE") if e.dxf.layer == "PIN_TOP"
            and 0 < e.dxf.center.x < 18 and 0 < e.dxf.center.y < 3]
    assert any(abs(a.dxf.center.x-b.dxf.center.x-2.54) < .001
               for a in jtag for b in jtag)
    holes = sorted((e.dxf.center.x, e.dxf.center.y) for e in ms.query("CIRCLE")
                   if e.dxf.layer == "PIN_TOP" and abs(e.dxf.radius-2.5) < .001)
    assert len(holes) == 4
    assert abs(holes[2][0]-holes[0][0]-53) < .001
    assert abs(holes[1][1]-holes[0][1]-45) < .001
    outlines = [e for e in ms.query("POLYLINE") if e.dxf.layer == "BG_OUTLINE"]
    # The last complete perimeter includes the MMCX notch and rounded corners.
    perimeter = max(outlines, key=lambda e: len(e.vertices))
    raw_points = [(v.x, v.y) for v in make_path(perimeter).flattening(.002)]
    if math.dist(raw_points[0], raw_points[-1]) < 1e-6:
        raw_points.pop()
    # STEP uses X-right/Y-up. KiCad footprint Y points down.
    model_points = [(x-40, y-25) for x, y in raw_points]
    footprint_points = [(x-40, 25-y) for x, y in raw_points]
    footprint_holes = [(x-40, 25-y) for x, y in holes]

    doc = TDocStd_Document(TCollection_ExtendedString("BinXCAF"))
    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    color_tool = XCAFDoc_DocumentTool.ColorTool_s(doc.Main())
    shapes = []

    def add(name, shape, rgb):
        assert BRepCheck_Analyzer(shape).IsValid(), name
        label = shape_tool.AddShape(shape, False)
        TDataStd_Name.Set_s(label, TCollection_ExtendedString(name))
        color_tool.SetColor(label, Quantity_Color(*rgb, Quantity_TOC_RGB), XCAFDoc_ColorGen)
        shapes.append({"name": name, "bounds_mm": bounds(shape)})

    def box_raw(x, y, z, dx, dy, dz):
        return BRepPrimAPI_MakeBox(gp_Pnt(x-40, y-25, z), dx, dy, dz).Shape()

    def cylinder_raw(x, y, z, radius, length, direction=(0, 0, 1)):
        return BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(x-40, y-25, z),
                                              gp_Dir(*direction)), radius, length).Shape()

    polygon = BRepBuilderAPI_MakePolygon()
    for x, y in model_points:
        polygon.Add(gp_Pnt(x, y, 0))
    polygon.Close()
    pcb = BRepPrimAPI_MakePrism(BRepBuilderAPI_MakeFace(polygon.Wire()).Face(), gp_Vec(0, 0, 1.6)).Shape()
    for x, y in holes:
        pcb = BRepAlgoAPI_Cut(pcb, cylinder_raw(x, y, -.1, drill/2, 1.8)).Shape()
    add("PCB_1p6mm_ASSUMED", pcb, (.08, .32, .22))
    for i, (x, y) in enumerate(holes, 1):
        annulus = BRepAlgoAPI_Cut(cylinder_raw(x, y, 1.6, 2.5, .04),
                                 cylinder_raw(x, y, 1.59, drill/2, .06)).Shape()
        add(f"Mount_land_{i}_DXF_5mm_OD_DRILL_PROVISIONAL", annulus, (.78, .67, .35))

    # Connector heights below are planning estimates, not proven upper bounds.
    # RJ45 XY outer limits come from the two J21 silkscreen polylines in the DXF.
    rj = box_raw(-2.150237, 23.621593, 1.6, 21.45007, 16.35032, 16)
    cavity = box_raw(-2.250237, 25.20, 4.2, 10, 13.2, 10)
    rj = BRepAlgoAPI_Cut(rj, cavity).Shape()
    add("J21_RJ45_BODY_XY_FROM_DXF_HEIGHT16mm_ESTIMATE", rj, (.70, .72, .74))
    add("RJ45_port_insert_REPRESENTATIVE", box_raw(6.8, 25.2, 4.2, .25, 13.2, 10), (.09, .09, .10))
    for i in range(8):
        add(f"RJ45_contact_{i+1}_REPRESENTATIVE", box_raw(-.8, 25.8+i*1.6, 12.7, 7.2, .35, .35), (.82, .67, .25))
    add("RJ45_green_LED_REPRESENTATIVE", box_raw(-2.20, 24.1, 14.4, .12, 1.5, 1.6), (.20, .66, .15))
    add("RJ45_yellow_LED_REPRESENTATIVE", box_raw(-2.20, 37.9, 14.4, .12, 1.5, 1.6), (.92, .72, .08))

    # RF connector locations and barrel length are read directly from drawing.
    for ref, cy in [("J3_RX1", 15.730736), ("J4_TX1", 33.561536)]:
        add(f"{ref}_SMA_support_REPRESENTATIVE", box_raw(75.3, cy-3.2, 1.6, 5.1, 6.4, 6.4), (.76, .62, .23))
        barrel = cylinder_raw(80.36, cy, 4.8, 3.175, 13.102094, (1, 0, 0))
        bore = cylinder_raw(80.35, cy, 4.8, 2.25, 13.13, (1, 0, 0))
        add(f"{ref}_SMA_barrel_HEIGHT_ESTIMATE", BRepAlgoAPI_Cut(barrel, bore).Shape(), (.78, .65, .27))
        # Decorative thread crests represent a screw thread, not mating geometry.
        for j in range(9):
            ring = cylinder_raw(87+j*.62, cy, 4.8, 3.28, .22, (1, 0, 0))
            ring = BRepAlgoAPI_Cut(ring, cylinder_raw(86.99+j*.62, cy, 4.8, 3.12, .24, (1, 0, 0))).Shape()
            add(f"{ref}_thread_marker_{j}_REPRESENTATIVE", ring, (.67, .54, .19))
        add(f"{ref}_RF_dielectric_REPRESENTATIVE", cylinder_raw(91.9, cy, 4.8, 2.1, .7, (1, 0, 0)), (.92, .91, .82))
        add(f"{ref}_RF_contact_REPRESENTATIVE", cylinder_raw(92.0, cy, 4.8, .55, .8, (1, 0, 0)), (.78, .65, .27))

    add("J18_MMCX_body_XY_FROM_DXF_HEIGHT_ESTIMATE", box_raw(-1.636, 42.545, 1.6, 8.075, 6.35, 6.35), (.75, .63, .26))
    mmcx = cylinder_raw(-1.64, 45.72, 4.775, 1.8, 3, (1, 0, 0))
    bore = cylinder_raw(-1.65, 45.72, 4.775, 1.25, 3.1, (1, 0, 0))
    add("MMCX_port_REPRESENTATIVE", BRepAlgoAPI_Cut(mmcx, bore).Shape(), (.48, .47, .39))
    usb = box_raw(-.52, 6.38, 1.6, 7.7, 9.05, 3.4)
    usb = BRepAlgoAPI_Cut(usb, box_raw(-.53, 6.85, 2.15, 5.5, 8.1, 2.3)).Shape()
    add("J2_USB_C_HEIGHT_ESTIMATE", usb, (.68, .69, .70))
    add("USB_C_tongue_REPRESENTATIVE", box_raw(-.25, 7.1, 3.05, 5.0, 7.6, .5), (.10, .10, .11))

    # Identifiable IC locations from manufacturer package-outline layers.
    for ref, x, y, dx, dy, h in [
        ("U1_ZYNQ", 28.763, 14.713, 17.15, 17.15, 2),
        ("U11_AD936x", 52.456, 18.213, 10.15, 10.15, 1.5),
        ("U4_DDR", 30.898, 5.319, 14.15, 8.15, 1.5),
        ("U12_ETH_PHY", 23.05, 33.2, 5.5, 5.5, 1.2),
    ]:
        add(ref+"_OUTLINE_WITH_ESTIMATED_HEIGHT", box_raw(x, y, 1.6, dx, dy, h), (.13, .14, .15))
    for ref, x, y, dx, dy in [
        ("L1",28.343,42.675,2.75,3.55),("L10",31.626,40.789,3.55,2.75),
        ("L4",17.529,7.435,3.55,2.75),("L3",25.911,7.943,3.55,2.75),
        ("L2",17.504,16.147,3.55,2.75),
    ]:
        add(ref+"_OUTLINE_WITH_ESTIMATED_HEIGHT", box_raw(x,y,1.6,dx,dy,2), (.24,.25,.26))
    for ref, cx, cy in [("J5_TX2",51.689,2.587),("J6_RX2",70.223,2.46)]:
        add(ref+"_UFL_REPRESENTATIVE", cylinder_raw(cx,cy,1.6,1.35,1.8), (.78,.74,.56))

    model_path = out/MODEL
    writer = STEPCAFControl_Writer()
    writer.SetColorMode(True)
    assert writer.Transfer(doc, STEPControl_AsIs)
    assert writer.Write(str(model_path)) == IFSelect_RetDone
    reader = STEPControl_Reader()
    assert reader.ReadFile(str(model_path)) == IFSelect_RetDone
    reader.TransferRoots()
    assert BRepCheck_Analyzer(reader.OneShape()).IsValid()

    fp = [f'(footprint "{NAME}" (version 20240108) (generator "pcbnew") (layer "F.Cu")',
          '(descr "E200 bare-board fallback mounting. XY from MicroPhase DXF: 80x50mm, 53x45mm hole centres offset toward SMA edge. Carrier drill and all Z sizes provisional; see e200-mounting-manifest.json.")',
          '(tags "ANTSDR E200 MicroPhase mechanical mounting fallback planning")',
          '(attr board_only exclude_from_pos_files exclude_from_bom)',
          '(property "Reference" "REF**" (at 0 -27 0) (layer "F.Fab") (effects (font (size 1 1) (thickness .15))))',
          '(property "Value" "ANTSDR E200 - planning" (at 0 27 0) (layer "F.Fab") (effects (font (size 1 1) (thickness .15))))',
          '(fp_text user "DRILL / HEIGHT VERIFY" (at 0 23 0) (layer "Dwgs.User") (effects (font (size .9 .9) (thickness .12))))']
    for x,y in footprint_holes:
        fp.append(f'(pad "" np_thru_hole circle (at {x:.6f} {y:.6f}) (size {drill} {drill}) (drill {drill}) (layers "*.Cu" "*.Mask") (clearance .5) (uuid "{uid()}"))')
        fp.append(f'(fp_circle (center {x:.6f} {y:.6f}) (end {x+3.5:.6f} {y:.6f}) (stroke (width .1) (type default)) (fill none) (layer "Dwgs.User"))')
        fp.append(f'(fp_circle (center {x:.6f} {y:.6f}) (end {x+3.75:.6f} {y:.6f}) (stroke (width .05) (type default)) (fill none) (layer "F.CrtYd"))')
    # The module's perimeter is drawing geometry, never carrier Edge.Cuts.
    for a,b in zip(footprint_points, footprint_points[1:]+footprint_points[:1]):
        fp.append(f'(fp_line (start {a[0]:.6f} {a[1]:.6f}) (end {b[0]:.6f} {b[1]:.6f}) (stroke (width .12) (type default)) (layer "F.Fab"))')
    def rect_raw(x1,y1,x2,y2,layer="Dwgs.User"):
        fp.append(f'(fp_rect (start {x1-40:.6f} {25-y2:.6f}) (end {x2-40:.6f} {25-y1:.6f}) (stroke (width .1) (type default)) (fill none) (layer "{layer}"))')
    rect_raw(-2.150237,23.621593,19.299833,39.971913)
    rect_raw(-.52,6.38,7.18,15.43)
    rect_raw(-1.636,42.545,6.439,48.895)
    for cy in [15.730736,33.561536]: rect_raw(75.3,cy-3.28,93.462094,cy+3.28)
    # Raised module envelope is drawing geometry; reserve local screw space in
    # the courtyard, allowing carrier components under the standoff-mounted PCB.
    rect_raw(-2.71,-.5,93.962094,50.5)
    for value,x,y in [("ETH",8.5,31.8),("USB",3.4,10.9),("PPS",2.4,45.7),("RX1",86.8,15.73),("TX1",86.8,33.56)]:
        fp.append(f'(fp_text user "{value}" (at {x-40:.3f} {25-y:.3f} 0) (layer "Dwgs.User") (effects (font (size .85 .85) (thickness .12))))')
    fp.append(f'(model "${{KIPRJMOD}}/../../libraries/3dmodels/{MODEL}" (offset (xyz 0 0 {gap})) (scale (xyz 1 1 1)) (rotate (xyz 0 0 0)))')
    fp.append(')')
    footprint_path = out/(NAME+".kicad_mod")
    footprint_path.write_text('\n'.join(fp)+'\n')
    proof = {
        "type": "Drawing-derived mounting footprint and simplified planning model; not manufacturer STEP",
        "source_url": SOURCE_URL, "source_commit": SOURCE_COMMIT, "source_sha256": SOURCE_SHA256,
        "coordinate_system": {"origin": "Nominal E200 bare PCB centre", "model": "x=dxf_x-40; y=dxf_y-25; z=0 at E200 PCB underside", "footprint": "x=dxf_x-40; y=25-dxf_y", "model_offset_z_mm": gap},
        "verified_from_dxf": {"nominal_pcb_size_mm": [80,50], "mounting_centres_dxf_xy_mm": holes, "mounting_centres_footprint_xy_mm": footprint_holes, "nominal_mounting_pitch_mm": [53,45], "mounting_pattern_offset_from_pcb_centre_mm": [(holes[0][0]+holes[2][0])/2-40,0], "mount_land_outer_diameter_mm":5, "units": "DXF INSUNITS=0; mm interpretation independently checked using the 2.54-mm JTAG pitch", "pcb_outline": "DXF rounded corners and MMCX notch, arc chord error <=0.002mm"},
        "provisional": {"carrier_npth_drill_mm":drill,"intended_fastener":"M2.5 planning only; actual E200 bore is not specified in source", "model_pcb_thickness_mm":1.6, "standoff_gap_mm":gap,"rj45_height_above_e200_pcb_mm":16,"other_component_heights":"Representative estimates; connector exact mating details, pins, bottom microSD assembly and cable envelopes omitted", "fastener_outline_mm":7, "fastener_courtyard_diameter_mm":7.5},
        "limitations": ["Check the actual E200 revision, drill fit and heights before fabrication", "Height assumptions are for reservation, not proven maximum mechanical envelopes", "The existing carrier PCB and schematic are not modified", "Dwgs.User shows module/connector envelope, excluding plug bodies and cable bends; F.CrtYd reserves only local fastener space", "Check clearance of carrier components under the module in 3D; the courtyard does not enforce height clearance", "Hardware is not selected; mounting is mechanical with no assigned electrical net", "All holes are unnumbered NPTH to avoid schematic net warnings"],
        "step_bounds_mm": bounds(reader.OneShape()), "solids":shapes,
        "sha256":{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in [footprint_path,model_path]},
        "license":"GPL-3.0; derived from MicroPhase/antsdr_doc_en; full upstream LICENSE accompanies assets",
    }
    (out/'e200-mounting-manifest.json').write_text(json.dumps(proof,indent=2)+'\n')
    print(json.dumps({"footprint":str(footprint_path),"model":str(model_path),"mount_pitch_mm":[53,45],"board_mm":[80,50],"drill_mm":drill,"gap_mm":gap,"step_bounds_mm":proof['step_bounds_mm']},indent=2))


if __name__ == '__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--workdir',type=Path,default=ROOT/'build/e200-mounting-2026-10-09')
    ap.add_argument('--drill',type=float,default=2.7)
    ap.add_argument('--gap',type=float,default=10)
    ap.add_argument('--publish',action='store_true')
    args=ap.parse_args()
    assert 0 < args.drill < 5 and args.gap >= 0
    generate(args.workdir,args.drill,args.gap)
    if args.publish:
        lib=ROOT/'hardware/libraries'
        shutil.copy2(args.workdir/(NAME+'.kicad_mod'),lib/'PigeonVision.pretty'/(NAME+'.kicad_mod'))
        shutil.copy2(args.workdir/MODEL,lib/'3dmodels'/MODEL)
        shutil.copy2(args.workdir/'LICENSE',lib/'3dmodels/MicroPhase_ANTSDR_E200_LICENSE.txt')
        shutil.copy2(args.workdir/'e200-mounting-manifest.json',lib/'review/e200-mounting-manifest.json')
