"""Stacking connectors for the two corners flanking the tray opening."""

from math import acos, cos, sin

from shapely import constrained_delaunay_triangles
from shapely.geometry import Polygon, box
from shapely.geometry.polygon import orient
from shapely.ops import unary_union


LOCK_STYLES = ("round", "snap-light", "snap-firm", "slide")
SNAP_BARBS = {"snap-light": 3.05, "snap-firm": 3.25}
RECEIVER_THROAT = 2.68
SNAP_ARM_RECESS = 5.4
RECEIVER_HALF_WIDTH = 3.72
RECEIVER_ROOF_START = 6.55
RECEIVER_ROOF_END = 9.97
RECEIVER_ROOF_HALF_WIDTH = 0.30
RECEIVER_DEPTH = 7.15
ARM_RECESS_OUTSIDE_WALL = 1.2
RELEASE_TAB_REACH = 2.4
SIDE_WIDTH = 9.0
PULL_SNAP = "pull-snap"
PULL_SNAP_TOP = 4.85
PULL_SOCKET = ((0, 3.05), (3.3, 3.05), (3.68, 3.43), (4.9, 3.43), (7.9, .43))
PULL_PRONG = (
    (0, 2.9), (.65, 2.9), (1.40, 2.3), (2.65, 2.3),
    (3.65, 3.2), (3.95, 3.2), (PULL_SNAP_TOP, 2.4),
)
CLIP_SLOT = (4.4, 7.2)
CLIP_DEPTH = 5.6
CLIP_WIDTH = 5.5
CLIP_TONGUE_DEPTH = 5.25
CLIP_TONGUE_WIDTH = 5.1


def pull_snap_prongs(centers, pitch):
    """Build both flexing arms directly on the column's bearing face."""
    prongs = []
    half_gap = .65
    for x, y in centers:
        for direction in (-1, 1):
            profile = []
            for height, radius in PULL_PRONG:
                angle = acos(half_gap / radius)
                ring = []
                for i in range(11):
                    t = angle * (i / 5 - 1)
                    ring.append((round(x + direction * radius * cos(t), 9),
                                 round(y + direction * radius * sin(t), 9)))
                profile.append((pitch + height, tuple(ring)))
            prongs.append(profile)
    return prongs


def outside_bounds(x, inner, outer):
    if x <= SIDE_WIDTH:
        outer_edge = x - SIDE_WIDTH / 2
        return outer_edge + outer, outer_edge + inner
    outer_edge = x + SIDE_WIDTH / 2
    return outer_edge - inner, outer_edge - outer


def chamfered_ring(left, front, right, back):
    bevel = 0.24
    return (
        (left + bevel, front), (right - bevel, front),
        (right, front + bevel), (right, back - bevel),
        (right - bevel, back), (left + bevel, back),
        (left, back - bevel), (left, front + bevel),
    )


def front_locks(style, centers, pitch):
    if style not in SNAP_BARBS:
        return []
    profiles = []
    barb = SNAP_BARBS[style]
    for x, y in centers:
        for side in (-1, 1):
            # The shoulder blocks withdrawal. The longer tip taper guides insertion.
            shape = [
                (0.55, 2.45, 1.75, 6.5, -SNAP_ARM_RECESS),
                (0.80, 2.45, 1.60, 6.5, -SNAP_ARM_RECESS + 0.6),
                (0.80, 2.48, 1.60, 6.5, 0),
                (0.80, 2.48, 0.35, 6.5, 2.72),
                (0.80, barb, -0.45, 6.5, 3.52),
                (0.80, barb, -1.13, 6.5, 4.20),
                # The ramp exposes the gripping faces beyond the side window without a bridge.
                (0.80, 2.70, -RELEASE_TAB_REACH, 6.35, 5.47),
                (0.80, 2.40, -RELEASE_TAB_REACH, 6.30, 5.88),
                (0.80, 1.85, -1.95, 6.2, 6.25),
            ]
            profile = []
            for inside_y, outside_y, outside_x, inside_x, z in shape:
                left, right = outside_bounds(x, inside_x, outside_x)
                front, back = sorted((y + side * inside_y, y + side * outside_y))
                profile.append((pitch + z, chamfered_ring(left, front, right, back)))
            profiles.append(profile)
    return profiles


def lock_pockets(style, centers, pitch):
    profiles = []
    for x, y in centers:
        if style in SNAP_BARBS:
            receiver = outside_bounds(x, RECEIVER_DEPTH, -1)
            arm_recess = outside_bounds(x, SIDE_WIDTH + 1, ARM_RECESS_OUTSIDE_WALL)
            cuts = [
                (0, 2.7, RECEIVER_THROAT, receiver),
                (2.7, RECEIVER_ROOF_START, RECEIVER_HALF_WIDTH, receiver),
                (RECEIVER_ROOF_START, RECEIVER_ROOF_END, RECEIVER_HALF_WIDTH, receiver),
                (pitch - SNAP_ARM_RECESS, pitch, RECEIVER_HALF_WIDTH, arm_recess),
            ]
        elif style == "slide":
            receiver = outside_bounds(x, CLIP_DEPTH, -1)
            lo, hi = CLIP_SLOT
            cuts = [(lo, hi, CLIP_WIDTH / 2, receiver),
                    (pitch - hi, pitch - lo, CLIP_WIDTH / 2, receiver)]
        else:
            continue
        profiles.extend(
            (low, high, box(left, y - half_width, right, y + half_width))
            for low, high, half_width, (left, right) in cuts
        )
    return profiles


def shape_receiver_roof(vertex, centers):
    """Narrow the top of the receiver's rectangular mesh into a 45-degree roof."""
    vx, vy, z = vertex
    if not RECEIVER_ROOF_START < z <= RECEIVER_ROOF_END:
        return vertex
    half_width = RECEIVER_HALF_WIDTH - (z - RECEIVER_ROOF_START)
    for x, y in centers:
        left, right = outside_bounds(x, RECEIVER_DEPTH, -1)
        if left - 1e-7 <= vx <= right + 1e-7 and abs(vy - y) <= RECEIVER_HALF_WIDTH + 1e-7:
            return vx, y + (vy - y) * half_width / RECEIVER_HALF_WIDTH, z
    return vertex


def slide_clip_outline():
    # In assembly this profile lies in the XZ plane with z=0 at the tray seam.
    lo, hi = CLIP_SLOT
    lower, upper = lo + .2, hi - .2
    return orient(unary_union([
        box(-2.6, -upper, -.2, upper),
        box(-1, lower, CLIP_TONGUE_DEPTH, upper),
        box(-1, -upper, CLIP_TONGUE_DEPTH, -lower),
        box(-3.8, -2.4, -2, 2.4),
    ]), 1.0)


def slide_clip_mesh():
    profile = slide_clip_outline()
    triangles = []
    tip = CLIP_TONGUE_DEPTH
    # The mouth starts loose and the last part of insertion seats a shallow friction taper.
    def thickness(x):
        return CLIP_TONGUE_WIDTH + .48 * (tip - max(0, x)) / tip

    def upper_vertex(x, y):
        return x, y, thickness(x)

    for face in constrained_delaunay_triangles(profile).geoms:
        rim = list(orient(face, 1.0).exterior.coords)[:-1]
        triangles.extend([
            tuple((u, v, 0.0) for u, v in reversed(rim)),
            tuple(upper_vertex(u, v) for u, v in rim),
        ])
    rim = list(profile.exterior.coords)
    for a, b in zip(rim, rim[1:]):
        low_a, low_b = (*a, 0.0), (*b, 0.0)
        high_a, high_b = upper_vertex(*a), upper_vertex(*b)
        triangles.extend([(low_a, low_b, high_b), (low_a, high_b, high_a)])
    return triangles
