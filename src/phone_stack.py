"""Generate stackable phone trays in millimetres using Shapely 2.1+."""

from argparse import ArgumentParser
from base64 import b64encode
from collections import Counter, defaultdict
from gzip import compress
from json import dumps
from math import ceil, cos, dist, pi, sin, sqrt
from pathlib import Path
from re import subn
from struct import pack
from xml.etree import ElementTree as ET
from zipfile import ZIP_DEFLATED, ZipFile

from shapely import constrained_delaunay_triangles
from shapely.geometry import LineString, MultiPolygon, Polygon, box
from shapely.geometry.polygon import orient
from shapely.ops import unary_union

from stack_locks import (
    PULL_SNAP, PULL_SNAP_TOP, PULL_SOCKET, SIDE_WIDTH, SNAP_BARBS,
    front_locks, lock_pockets, pull_snap_prongs, shape_receiver_roof,
)


SLOT_LENGTH = 180.0
SLOT_DEPTH = 90.0
SLOT_HEIGHT = 19.0
BACK_WIDTH = 4.0
BEARING_HEIGHT = 3.2
STOP_HEIGHT = 3.0
POST_DEPTH = 12.0
OUTER_RIB = 3.2
PATTERNS = ("honeycomb", "diamond", "triangle")
OPEN_AREA_STEPS = tuple(range(35, 61, 5))
DEFAULT_OPEN_AREA = 50
PHONE_LENGTH = 176.0
PHONE_WIDTH = 87.0
PHONE_THICKNESS = 16.0
OPEN_VARIANTS = {
    "phone-stack-long-edge": (SLOT_LENGTH, SLOT_DEPTH, 58.0, True),
    "phone-stack-short-edge": (SLOT_DEPTH, SLOT_LENGTH, 49.0, False),
    "phone-stack-short-overhang": (SLOT_DEPTH, round(PHONE_LENGTH * 2 / 3, 3), 0.0, False),
}

PITCH = BEARING_HEIGHT + SLOT_HEIGHT
PIN_PROFILE = ((PITCH, 3.0), (PITCH + 0.8, 3.0), (PITCH + 2.0, 1.8))
# The socket tapers at 45° and closes over a 2.6 mm span.
SOCKET_PROFILE = ((0.0, 3.5), (1.0, 3.5), (3.2, 1.3))


def polygons(shape):
    if not shape.is_empty:
        yield from shape.geoms if isinstance(shape, MultiPolygon) else (shape,)


def rings(shape):
    def coordinates(ring):
        snapped = [(round(x, 9), round(y, 9)) for x, y in ring.coords]
        return tuple(a for a, b in zip(snapped, snapped[1:]) if a != b)

    for polygon in polygons(shape):
        oriented = orient(polygon, sign=1.0)
        yield coordinates(oriented.exterior)
        yield from (coordinates(hole) for hole in oriented.interiors)


def circular_section(x, y, radius, segments=40):
    return tuple(
        (x + radius * cos(i * 2 * pi / segments), y + radius * sin(i * 2 * pi / segments))
        for i in range(segments)
    )


def clipped_rectangle(left, front, right, back, corner):
    return Polygon([
        (left + corner, front), (right - corner, front),
        (right, front + corner), (right, back - corner),
        (right - corner, back), (left + corner, back),
        (left, back - corner), (left, front + corner),
    ])


def front_rail(length, finger_width):
    edge = OUTER_RIB / 2
    if not finger_width:
        return LineString([(edge, edge), (length - edge, edge)])
    half = length / 2
    # Inset the continuous rail below the phone's edge to leave a place for fingertips.
    rise = 20.0
    curve = [(half - finger_width / 2 + finger_width * i / 24,
              edge + rise * (1 - cos(2 * pi * i / 24)) / 2)
             for i in range(25)]
    return LineString([(edge, edge), *curve, (length - edge, edge)])


def pattern_cells(pattern, length, depth):
    size = 13.0 if pattern == "honeycomb" else 23.0
    if pattern == "honeycomb":
        row_height = sqrt(3) * size
        for i in range(-1, ceil(length / (1.5 * size)) + 1):
            for j in range(-1, ceil(depth / row_height) + 1):
                x, y = 1.5 * size * i, row_height * (j + (i % 2) / 2)
                yield Polygon([(x + size * cos(k * pi / 3), y + size * sin(k * pi / 3))
                               for k in range(6)])
    elif pattern == "diamond":
        radius = size / sqrt(2)
        for i in range(-1, ceil(length / radius) + 1):
            for j in range(-1, ceil(depth / radius) + 1):
                if (i + j) % 2 == 0:
                    x, y = i * radius + 2.7, j * radius + 4.1
                    yield Polygon([(x - radius, y), (x, y - radius),
                                   (x + radius, y), (x, y + radius)])
    else:
        row_height = sqrt(3) * size / 2
        for j in range(-1, ceil(depth / row_height) + 1):
            for i in range(-2 - j // 2, ceil(length / size) + 1 - j // 2):
                x, y = size * (i + j / 2), j * row_height
                a, b = (x, y), (x + size, y)
                c, d = (x + size / 2, y + row_height), (x + 1.5 * size, y + row_height)
                yield Polygon([a, b, c])
                yield Polygon([b, d, c])


def perforate(panel, posts, pattern, open_area):
    _, _, length, depth = panel.bounds
    inset = panel.buffer(-OUTER_RIB, join_style="mitre").difference(posts.buffer(1.2, join_style="mitre"))
    cells = [cell for cell in pattern_cells(pattern, length, depth) if cell.intersects(inset)]
    goal = panel.area * open_area / 100
    narrow, wide = 0.95, 8.0
    for _ in range(14):
        radius = (narrow + wide) / 2
        holes = [cell.buffer(-radius, join_style="mitre").intersection(inset) for cell in cells]
        holes = [hole for hole in holes if hole.area >= 9]
        if sum(hole.area for hole in holes) > goal:
            narrow = radius
        else:
            wide = radius
    return panel.difference(unary_union(holes))


def tray_mesh(slot_across, slot_in, finger_width, long_entry,
              pattern="honeycomb", open_area=DEFAULT_OPEN_AREA, *, lock=PULL_SNAP, front_corner=False):
    length = slot_across + 2 * SIDE_WIDTH
    depth = slot_in + BACK_WIDTH
    half = length / 2
    perimeter = clipped_rectangle(0, 0, length, depth, 2.5)
    edge = OUTER_RIB / 2
    sides = [(0, SIDE_WIDTH), (length - SIDE_WIDTH, length)]
    ends = [(0, POST_DEPTH), (depth - POST_DEPTH, depth)]
    posts = perimeter.intersection(unary_union([
        box(x0, y0, x1, y1) for x0, x1 in sides for y0, y1 in ends
    ]))
    front = list(front_rail(length, finger_width).coords)[1:-1]
    panel = perimeter.intersection(Polygon([(0, 0), *[
        (x, y - edge) for x, y in front
    ], (length, 0), (length, depth), (0, depth)]))
    base = perforate(panel, posts, pattern, open_area)
    opening_center, opening_width = (
        (0.55 * slot_in, 53.0) if long_entry else (slot_in - 41, 38.0)
    )
    low, high = opening_center - opening_width / 2, opening_center + opening_width / 2
    stops = unary_union([
        box(left, start, right, end)
        for left, right in ((0, OUTER_RIB), (length - OUTER_RIB, length))
        for start, end in ((0, low), (high, depth))
    ] + [box(0, depth - OUTER_RIB, length, depth)])
    if not long_entry:
        stops = stops.difference(box(half - 15, depth - OUTER_RIB - 1, half + 15, depth + 1))
    rails = base.intersection(unary_union([posts, stops]))
    centers = [((x0 + x1) / 2, (y0 + y1) / 2)
               for x0, x1 in sides for y0, y1 in ends]
    if front_corner:
        corner = box(0, 0, SIDE_WIDTH, POST_DEPTH)
        base, rails, posts = (shape.intersection(corner) for shape in (base, rails, posts))
        centers = centers[:1]
    front_centers = [(x, y) for x, y in centers if y < POST_DEPTH]
    if lock == PULL_SNAP:
        sockets = [[(z, circular_section(x, y, radius, 24)) for z, radius in PULL_SOCKET]
                   for x, y in centers]
        pins = pull_snap_prongs(centers, PITCH)
        latches = []
    else:
        pin_centers = [(x, y) for x, y in centers if lock not in SNAP_BARBS or y >= POST_DEPTH]
        sockets = [[(z, circular_section(x, y, radius)) for z, radius in SOCKET_PROFILE]
                   for x, y in pin_centers]
        pins = [[(z, circular_section(x, y, radius)) for z, radius in PIN_PROFILE]
                for x, y in pin_centers]
        latches = front_locks(lock, front_centers, PITCH)
    ledge = BEARING_HEIGHT + STOP_HEIGHT
    sections = [(0, base), (BEARING_HEIGHT, rails), (ledge, posts)]
    pockets = lock_pockets(lock, front_centers, PITCH)
    levels = sorted({0, BEARING_HEIGHT, ledge, PITCH, *(z for low, high, _ in pockets for z in (low, high))})
    slabs = []
    for lo, hi in zip(levels, levels[1:]):
        shape = next(shape for z, shape in reversed(sections) if z <= lo)
        cuts = [cut for low, high, cut in pockets if low <= lo < high]
        slabs.append((shape.difference(unary_union(cuts)) if cuts else shape, lo, hi))
    faces = [
        (slabs[0][0].difference(unary_union([Polygon(profile[0][1]) for profile in sockets])), 0, False),
        (slabs[-1][0].difference(unary_union(
            [Polygon(profile[0][1]) for profile in pins])), PITCH, True),
    ]
    for (under, _, z), (over, _, _) in zip(slabs, slabs[1:]):
        if not under.equals(over):
            attached = [Polygon(profile[0][1]) for profile in latches if profile[0][0] == z]
            faces.extend([
                (under.difference(unary_union([over, *attached])), z, True),
                (over.difference(under), z, False),
            ])
    connector_faces = [(Polygon(profile[-1][1]), profile[-1][0], False) for profile in sockets]
    connector_faces.extend((Polygon(profile[-1][1]), profile[-1][0], True) for profile in pins + latches)

    # Adjacent surfaces must share boundary vertices where a rail meets a post.
    boundary_points = {
        point for shape in [*(shape for shape, _, _ in slabs), *(shape for shape, _, _ in faces)]
        for ring in rings(shape) for point in ring
    }

    def split_edges(ring):
        result = []
        for a, b in zip(ring, ring[1:] + ring[:1]):
            dx, dy = b[0] - a[0], b[1] - a[1]
            points = [
                c for c in boundary_points if c != a and c != b
                and abs(dx * (c[1] - a[1]) - dy * (c[0] - a[0])) < 2e-7
                and 0 < dx * (c[0] - a[0]) + dy * (c[1] - a[1]) < dx * dx + dy * dy
            ]
            result.extend((a, *sorted(points, key=lambda c: dist(a, c))))
        return tuple(result)

    def at_height(ring, z):
        return tuple((x, y, z) for x, y in ring)

    def join(lower, upper):
        for i, a in enumerate(lower):
            j = (i + 1) % len(lower)
            yield a, lower[j], upper[j]
            yield a, upper[j], upper[i]

    triangles = []
    cap_faces = [(shape, z, upward, lock != PULL_SNAP) for shape, z, upward in connector_faces]
    tray_faces = [(shape, z, upward, True) for shape, z, upward in faces]
    for shape, z, upward, split_boundary in tray_faces + cap_faces:
        for polygon in polygons(shape):
            boundary, *holes = [split_edges(ring) if split_boundary else ring for ring in rings(polygon)]
            filled = constrained_delaunay_triangles(Polygon(boundary, holes))
            for piece in filled.geoms:
                ring = next(rings(piece))
                triangles.append(at_height(ring if upward else ring[::-1], z))
    for shape, low, high in slabs:
        for ring in rings(shape):
            rim = split_edges(ring)
            triangles.extend(join(at_height(rim, low), at_height(rim, high)))
    for profiles, inward in ((sockets, True), (pins + latches, False)):
        for profile in profiles:
            sections = [at_height(ring[::-1] if inward else ring, z) for z, ring in profile]
            for low, high in zip(sections, sections[1:]):
                triangles.extend(join(low, high))
    if lock in SNAP_BARBS:
        triangles = [tuple(shape_receiver_roof(vertex, front_centers) for vertex in face)
                     for face in triangles]
    triangles = [tuple(tuple(round(d, 5) for d in vertex) for vertex in face) for face in triangles]
    actual_open_area = 100 * (1 - base.area / panel.area)
    return triangles, round(actual_open_area, 1)


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def normal(a, b, c):
    return cross(tuple(y - x for x, y in zip(a, b)),
                 tuple(z - x for x, z in zip(a, c)))


def check_mesh(triangles, *, report=True):
    directed = Counter()
    neighbors = defaultdict(set)
    volume = 0.0
    for a, b, c in triangles:
        if not any(normal(a, b, c)):
            raise ValueError("Degenerate triangle")
        volume += sum(x * y for x, y in zip(a, cross(b, c))) / 6
        for start, end in ((a, b), (b, c), (c, a)):
            directed[start, end] += 1
            neighbors[start].add(end)
    unmatched = [edge for edge, count in directed.items()
                 if count != 1 or directed.get(edge[::-1]) != 1]
    if unmatched:
        raise ValueError(f"{len(unmatched)} unmatched edges, including {unmatched[:4]}")
    visited = set()
    pending = [triangles[0][0]]
    while pending:
        vertex = pending.pop()
        if vertex not in visited:
            visited.add(vertex)
            pending.extend(neighbors[vertex] - visited)
    if len(visited) != len(neighbors) or volume <= 0:
        raise ValueError("Expected one connected, outward-facing solid")
    bounds = [(min(axis), max(axis)) for axis in zip(*neighbors)]
    if report:
        print(f"{len(triangles)} triangles | {bounds} mm | {volume:.1f} mm³")


def write_stl(path, triangles):
    with path.open("wb") as stream:
        stream.write(pack("<80sI", b"Stackable phone tray, millimetres", len(triangles)))
        for a, b, c in triangles:
            n = normal(a, b, c)
            magnitude = sqrt(sum(component ** 2 for component in n))
            stream.write(pack("<12fH", *(component / magnitude for component in n),
                              *a, *b, *c, 0))


def indexed_mesh(triangles):
    vertices = list(dict.fromkeys(vertex for face in triangles for vertex in face))
    index = {vertex: i for i, vertex in enumerate(vertices)}
    return vertices, [index[vertex] for face in triangles for vertex in face]


def write_3mf(path, triangles):
    write_3mf_plate(path, [("Stackable phone tray", triangles, ((0, 0),))])


def write_3mf_plate(path, parts):
    core = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
    model = ET.Element("model", {"xmlns": core, "unit": "millimeter"})
    resources = ET.SubElement(model, "resources")
    build = ET.SubElement(model, "build")
    for index, (name, triangles, placements) in enumerate(parts, 1):
        object_id = str(index)
        obj = ET.SubElement(resources, "object", id=object_id, type="model", name=name)
        mesh = ET.SubElement(obj, "mesh")
        vertices = ET.SubElement(mesh, "vertices")
        coords, indices = indexed_mesh(triangles)
        for vertex in coords:
            ET.SubElement(vertices, "vertex", dict(zip("xyz", map(str, vertex))))
        facets = ET.SubElement(mesh, "triangles")
        for offset in range(0, len(indices), 3):
            face = indices[offset:offset + 3]
            ET.SubElement(facets, "triangle", {f"v{i}": str(v) for i, v in enumerate(face, 1)})
        for x, y in placements:
            ET.SubElement(build, "item", objectid=object_id, transform=f"1 0 0 0 1 0 0 0 1 {x} {y} 0")
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr("3D/3dmodel.model", ET.tostring(model, encoding="utf-8"))
        archive.writestr("[Content_Types].xml",
                         '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                         '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                         '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
                         '</Types>')
        archive.writestr("_rels/.rels",
                         '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                         '<Relationship Id="rel0" Target="/3D/3dmodel.model"'
                         ' Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>'
                         '</Relationships>')


def pack_preview_mesh(triangles, percent):
    vertices = sorted(set(vertex for face in triangles for vertex in face))
    index = {vertex: i for i, vertex in enumerate(vertices)}
    faces = []
    for triangle in triangles:
        indices = [index[vertex] for vertex in triangle]
        first = indices.index(min(indices))
        faces.append(indices[first:] + indices[:first])
    faces.sort()
    data = bytearray(pack("<HHH", len(vertices), len(faces), round(percent * 10)))
    for axis in zip(*vertices):
        previous = 0
        for coordinate in axis:
            current = round(coordinate * 100_000)
            data.extend(pack("<i", current - previous))
            previous = current
    for indices in zip(*faces):
        previous = 0
        for index in indices:
            data.extend(pack("<h", index - previous))
            previous = index
    return data


def write_preview_data(path):
    data = {"bearing": BEARING_HEIGHT, "pitch": PITCH, "postWidth": SIDE_WIDTH,
            "postDepth": POST_DEPTH, "socketDepth": PULL_SOCKET[-1][0],
            "phone": {"length": PHONE_LENGTH, "width": PHONE_WIDTH, "thickness": PHONE_THICKNESS},
            "variants": {}}
    designs = [("long", "横向长边取机"), ("full", "侧向全收纳"), ("overhang", "侧向外悬三分之一")]
    meshes = bytearray()
    for (key, label), dims in zip(designs, OPEN_VARIANTS.values()):
        width, insertion, *_ = dims
        variant = {"label": label, "across": width + 2 * SIDE_WIDTH,
                   "depth": insertion + BACK_WIDTH, "height": round(PITCH + PULL_SNAP_TOP, 2),
                   "insertion": insertion, "patterns": {}}
        for pattern in PATTERNS:
            variants = {}
            for step in OPEN_AREA_STEPS:
                try:
                    mesh, percent = tray_mesh(*dims, pattern, step)
                    check_mesh(mesh, report=False)
                except ValueError as error:
                    raise ValueError(f"{key}/{pattern}/{step}: {error}") from error
                variants[step] = len(meshes)
                meshes.extend(pack_preview_mesh(mesh, percent))
            variant["patterns"][pattern] = variants
        data["variants"][key] = variant
    metadata = dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
    content = pack("<I", len(metadata)) + metadata + meshes
    payload = b64encode(compress(content, mtime=0))
    output, count = subn(r"(?<=data-tray-meshes>).*?(?=</script>)",
                         payload.decode("ascii"), path.read_text(), count=1)
    if count != 1 or len(output.encode()) >= 1_000_000:
        raise ValueError(f"Preview placeholder or size is invalid ({len(output.encode())} bytes)")
    path.write_text(output)


if __name__ == "__main__":
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--pattern", choices=PATTERNS, default="honeycomb")
    parser.add_argument("--open-area", type=int, choices=OPEN_AREA_STEPS, default=DEFAULT_OPEN_AREA)
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parents[1] / "models")
    parser.add_argument("--preview", type=Path, help="Embed verified mesh presets in the preview HTML.")
    args = parser.parse_args()
    folder = args.output_dir
    folder.mkdir(parents=True, exist_ok=True)
    for name, dimensions in OPEN_VARIANTS.items():
        mesh, percentage = tray_mesh(*dimensions, args.pattern, args.open_area)
        print(f"{name}: {percentage}% open area")
        check_mesh(mesh)
        write_stl(folder / f"{name}.stl", mesh)
        write_3mf(folder / f"{name}.3mf", mesh)
    if args.preview:
        write_preview_data(args.preview)
