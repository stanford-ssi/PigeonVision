#!/usr/bin/env python3
"""Propose carrier GND stitching vias using a filled KiCad board.

Run with the Python bundled with KiCad, or any Python with pcbnew available.
Write the output into a copy of the KiCad project so DRC can load the rules,
then refill zones and run DRC before applying the result.
"""
import argparse
from collections import defaultdict
from math import dist, hypot
from pathlib import Path

import pcbnew

from edit_camera_buses import patch, uid


MM = 1_000_000
VIA_LAND = 0.50
VIA_DRILL = 0.30
EDGE_COPPER = 0.50
ARTWORK_ERROR = 0.005
REFERENCE_LAYERS = (pcbnew.In1_Cu, pcbnew.In3_Cu, pcbnew.In4_Cu)
CELL = 5 * MM


def vector(xy):
    return pcbnew.VECTOR2I(*(round(c * MM) for c in xy))


def xy(point):
    return point.x / MM, point.y / MM


def outline_chains(polyset):
    for i in range(polyset.OutlineCount()):
        yield polyset.Outline(i)
        for j in range(polyset.HoleCount(i)):
            yield polyset.Hole(i, j)


class CopperIndex:
    def __init__(self):
        self.cells = defaultdict(list)

    def add(self, shape, clearance_mm):
        if not shape.OutlineCount():
            return
        clearance = round(clearance_mm * MM)
        bounds = shape.BBox(clearance)
        for i in range(bounds.GetLeft() // CELL, bounds.GetRight() // CELL + 1):
            for j in range(bounds.GetTop() // CELL, bounds.GetBottom() // CELL + 1):
                self.cells[i, j].append((shape, clearance))

    def blocks(self, point):
        return any(shape.Collide(point, clearance)
                   for shape, clearance in self.cells.get((point.x // CELL, point.y // CELL), ()))


def copper_shape(item, layer):
    shape = pcbnew.SHAPE_POLY_SET()
    item.TransformShapeToPolygon(shape, layer, 0, round(ARTWORK_ERROR * MM), pcbnew.ERROR_OUTSIDE)
    return shape


def perimeter_samples(chain, pitch):
    points = [xy(chain.CPoint(i)) for i in range(chain.PointCount())]
    target = pitch / 2
    traversed = 0.0
    for start, end in zip(points, points[1:] + points[:1]):
        dx, dy = end[0] - start[0], end[1] - start[1]
        span = hypot(dx, dy)
        while span and target < traversed + span:
            fraction = (target - traversed) / span
            x, y = start[0] + fraction * dx, start[1] + fraction * dy
            # Try both normals. The exact board outline decides which side is copper.
            yield [(x - side * dy / span, y + side * dx / span) for side in (1.25, -1.25)]
            target += pitch
        traversed += span


def stitch(board, field_pitch, edge_pitch):
    if board.GetCopperLayerCount() != 6:
        raise ValueError("The carrier stitching tool expects a six-layer board.")
    outline = pcbnew.SHAPE_POLY_SET()
    if not board.GetBoardPolygonOutlines(outline, False):
        raise ValueError("The board outline must be closed.")
    boundaries = list(outline_chains(outline))
    footprints = list(board.GetFootprints())
    zones = list(board.Zones())
    planes = {}
    for layer in REFERENCE_LAYERS:
        planes[layer] = [z.GetFilledPolysList(layer) for z in zones
                         if z.GetNetname() == "GND" and z.IsOnLayer(layer) and not z.GetIsRuleArea()]
        if not any(poly.OutlineCount() for poly in planes[layer]):
            raise ValueError(f"Refill GND zones on {board.GetLayerName(layer)} before stitching.")

    def on_reference_planes(pos):
        return all(any(poly.Contains(pos) for poly in planes[layer]) for layer in REFERENCE_LAYERS)

    copper = (pcbnew.F_Cu, pcbnew.In1_Cu, pcbnew.In2_Cu, pcbnew.In3_Cu, pcbnew.In4_Cu, pcbnew.B_Cu)
    land_radius = VIA_LAND / 2
    hole_radius = VIA_DRILL / 2
    obstacles = CopperIndex()
    for footprint in footprints:
        for pad in footprint.Pads():
            for layer in copper:
                if pad.IsOnLayer(layer):
                    # Leave room even beside GND pads so drills stay out of assembly lands.
                    gap = 0.20 if pad.GetNetname() == "GND" else 0.53
                    obstacles.add(copper_shape(pad, layer), land_radius + gap)
            if pad.HasHole():
                hole = pcbnew.SHAPE_POLY_SET()
                pad.TransformHoleToPolygon(hole, 0, round(ARTWORK_ERROR * MM), pcbnew.ERROR_OUTSIDE)
                obstacles.add(hole, hole_radius + 0.48)

    existing_ground = []
    reference_ground = []
    transitions = []
    for item in board.GetTracks():
        net = item.GetNetname()
        if isinstance(item, pcbnew.PCB_VIA):
            widest_layer = max((l for l in copper if item.IsOnLayer(l)), key=item.GetWidth)
            obstacles.add(copper_shape(item, widest_layer), land_radius + (0.20 if net == "GND" else 0.53))
            if net == "GND" and item.IsOnLayer(pcbnew.F_Cu) and item.IsOnLayer(pcbnew.B_Cu):
                position = item.GetPosition()
                existing_ground.append(xy(position))
                if on_reference_planes(position):
                    reference_ground.append(xy(position))
            elif any(mark in net for mark in ("/MIPI", "/ETH", "/USB2_", "/USB_D_")):
                transitions.append((net, xy(item.GetPosition())))
        else:
            obstacles.add(copper_shape(item, item.GetLayer()), land_radius + (0.12 if net == "GND" else 0.53))

    for zone in zones + [z for footprint in footprints for z in footprint.Zones()]:
        if zone.GetIsRuleArea() and zone.GetDoNotAllowVias():
            obstacles.add(zone.Outline(), land_radius + 0.03)
        elif not zone.GetIsRuleArea() and zone.GetNetname() != "GND":
            for layer in copper:
                if zone.IsOnLayer(layer):
                    obstacles.add(zone.GetFilledPolysList(layer), land_radius + 0.53)

    added = []
    counts = defaultdict(int)

    def place(candidate, spacing, purpose):
        point = tuple(round(c, 3) for c in candidate)
        if any(dist(point, other) < spacing for other in existing_ground):
            return False
        pos = vector(point)
        if not outline.Contains(pos):
            return False
        if any(chain.Distance(pos, True) < round((EDGE_COPPER + land_radius + 0.05) * MM)
               for chain in boundaries):
            return False
        if obstacles.blocks(pos) or not on_reference_planes(pos):
            return False
        existing_ground.append(point)
        reference_ground.append(point)
        added.append(point)
        counts[purpose] += 1
        return True

    # Handle individual transitions and let nearby P/N vias share a return via.
    offsets = sorted(((x / 10, y / 10) for x in range(-19, 20, 2)
                      for y in range(-19, 20, 2) if 0.8 <= hypot(x, y) / 10 <= 2.0),
                     key=lambda offset: hypot(*offset))
    for _, (x, y) in sorted(transitions):
        if any(dist((x, y), g) <= 2.0 for g in reference_ground):
            continue
        for dx, dy in offsets:
            if place((x + dx, y + dy), 0.9, "signal returns"):
                break

    for chain in boundaries:
        for sample in perimeter_samples(chain, edge_pitch):
            for candidate in sample:
                if place(candidate, edge_pitch * 0.57, "perimeter and cutout"):
                    break

    limits = outline.BBox()
    min_x, min_y = limits.GetLeft() / MM, limits.GetTop() / MM
    max_x, max_y = limits.GetRight() / MM, limits.GetBottom() / MM
    y = min_y + field_pitch / 2
    row = 0
    while y < max_y:
        x = min_x + field_pitch / 2 + (row % 2) * field_pitch / 2
        while x < max_x:
            place((x, y), field_pitch * 0.57, "plane field")
            x += field_pitch
        y += field_pitch * 0.866
        row += 1
    covered = sum(any(dist(p, ground) <= 2.0 for ground in reference_ground) for _, p in transitions)
    return added, counts, covered, len(transitions)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("board", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--field-pitch", type=float, default=8.0, help="Field grid pitch in mm.")
    parser.add_argument("--edge-pitch", type=float, default=5.0, help="Edge and cutout pitch in mm.")
    args = parser.parse_args()
    if args.board.resolve() == args.output.resolve():
        parser.error("Choose a separate output board.")
    if min(args.field_pitch, args.edge_pitch) < 2:
        parser.error("Pitch must be at least 2 mm.")
    text = args.board.read_text()
    board = pcbnew.LoadBoard(str(args.board))
    added, counts, covered, total = stitch(board, args.field_pitch, args.edge_pitch)
    vias = [
        f'(via (at {x:g} {y:g}) (size {VIA_LAND:g}) (drill {VIA_DRILL:g}) '
        f'(layers "F.Cu" "B.Cu") (free yes) (net "GND") (uuid "{uid(f"carrier-stitch-{x:g}-{y:g}")}"))'
        for x, y in added
    ]
    args.output.write_text(patch(text, [], vias) if vias else text)
    print(f"Added {len(vias)} GND vias: {dict(counts)}.")
    print(f"{covered}/{total} high-speed via sites within 2 mm of a GND via.")
    print("Refill the output board's zones and run DRC.")


if __name__ == "__main__":
    main()
