"""Build an original layered optical archive, without reading any source mesh.

The three parallel optical channels, routed substrate and rounded frame are
analytic geometry. Surface names are the existing MIT renderer's interface.
Coordinates retain the accepted scene's envelope, label and extraction pivot.
"""
import json
import math
from pathlib import Path
import struct

ROOT = Path(__file__).resolve().parents[1]


class Surface:
    def __init__(self):
        self.positions = []
        self.normals = []

    def triangle(self, points, normals):
        self.positions.extend(points)
        self.normals.extend(normals)

    def quad(self, points, normals):
        for ids in ((0, 1, 2), (0, 2, 3)):
            self.triangle([points[i] for i in ids], [normals[i] for i in ids])

    @staticmethod
    def outline(cx, cy, w, h, radius, steps=8):
        points = []
        for sx, sy, angle in ((1, 1, 0), (-1, 1, 90), (-1, -1, 180), (1, -1, 270)):
            for step in range(steps + 1):
                a = math.radians(angle + step * 90 / steps)
                nx, ny = math.cos(a), math.sin(a)
                points.append((cx + sx * (w / 2 - radius) + radius * nx,
                               cy + sy * (h / 2 - radius) + radius * ny, nx, ny))
        return points

    def skin(self, rings):
        for lower, upper in zip(rings, rings[1:]):
            for i in range(len(lower)):
                j = (i + 1) % len(lower)
                vertices = [lower[i], lower[j], upper[j], upper[i]]
                self.quad([v[:3] for v in vertices], [v[3:] for v in vertices])

    def solid(self, cx, cy, w, h, radius, low, high, bevel=.006):
        """Closed rounded solid with four-segment rolled edge highlights."""
        bevel = min(bevel, (high - low) / 2.01, radius / 2)
        rings = []
        for top in (False, True):
            for step in range(5):
                a = (step / 4 if top else 1 - step / 4) * math.pi / 2
                inset = bevel * (1 - math.cos(a))
                z = high - bevel + bevel * math.sin(a) if top else low + bevel - bevel * math.sin(a)
                nz = math.sin(a) * (1 if top else -1)
                ring = [(x, y, z, nx * math.cos(a), ny * math.cos(a), nz)
                        for x, y, nx, ny in self.outline(cx, cy, w - 2 * inset, h - 2 * inset, radius - inset)]
                rings.append(ring)
        self.skin(rings)
        for ring, z, sign in ((rings[0], low, -1), (rings[-1], high, 1)):
            for i in range(len(ring)):
                j = (i + 1) % len(ring)
                points = [(cx, cy, z), ring[i][:3], ring[j][:3]]
                if sign < 0:
                    points.reverse()
                self.triangle(points, [(0, 0, sign)] * 3)

    def frame(self, cx, cy, w, h, radius, border, low, high):
        """Hollow closed frame; rolled outer/inner lips, no opaque front slab."""
        bevel = min(.009, (high - low) / 4, border / 4)
        profile = [(0, low + bevel, 1, 0), (0, high - bevel, 1, 0),
                   (bevel, high, .707, .707), (border - bevel, high, -.707, .707),
                   (border, high - bevel, -1, 0), (border, low + bevel, -1, 0),
                   (border - bevel, low, -.707, -.707), (bevel, low, .707, -.707)]
        rings = []
        for inset, z, radial, nz in profile:
            rings.append([(x, y, z, nx * radial, ny * radial, nz)
                          for x, y, nx, ny in self.outline(cx, cy, w - 2 * inset, h - 2 * inset, radius - inset)])
        self.skin(rings + [rings[0]])

    def washer(self, cx, cy, outer, inner, low, high, segments=32):
        rings = []
        for radius, z, radial, nz in ((outer, low, 1, 0), (outer, high, 1, 0),
                                       (inner, high, 0, 1), (inner, low, -1, 0)):
            rings.append([(cx + radius * math.cos(a), cy + radius * math.sin(a), z,
                           radial * math.cos(a), radial * math.sin(a), nz)
                          for a in [i * 2 * math.pi / segments for i in range(segments)]])
        self.skin(rings + [rings[0]])


def main():
    surfaces = {}

    def layer(name):
        surfaces[name] = Surface()
        return surfaces[name]

    # A thin, closed, transmissive front cover over the interior, not a solid box.
    layer('Frosted_Polymer').solid(0, 1.85, 5, 3.7, .11, .174, .206, .009)
    layer('Ivory_Edges').frame(0, 1.85, 4.986, 3.68, .105, .075, -.13, .194)
    layer('Optical_Diffuser').solid(0, 1.86, 4.8, 3.47, .08, -.062, -.038)
    layer('Printed_Label').solid(-1.36, 3.04, 1.04, .48, .035, .179, .218)
    layer('Index_Inlay').solid(-2.16, 3.57, .25, .22, .025, .202, .208, .002)

    ceramic = layer('Internal_Ceramic')
    ceramic.frame(.2, 1.65, 3.8, 2.45, .14, .08, -.025, .085)
    ceramic.solid(.18, .6, 3.35, .18, .045, -.025, .096)
    ceramic.solid(1.92, 1.82, .12, 2.48, .04, -.02, .12)

    shoulders = layer('Optical_Edges')
    glass = layer('Optical_Glass_Body')
    roof = layer('Optical_Glass_Roof')
    edges = layer('Optical_Glass_Edge')
    substrate = layer('Subsurface_Optics')
    amber = layer('Amber_Optical_Inlay')
    grooves = layer('Optical_Film')
    # Three independently routed cartridges replace the source's distinctive
    # double-ring mechanism. Recessed channels stay visible through the cover.
    for column, x in enumerate((-.97, .12, 1.21)):
        shoulders.frame(x, 1.68, .83, 1.88, .21, .055, -.018, .112)
        substrate.solid(x, 1.68, .68, 1.71, .16, -.022, .016)
        glass.solid(x, 1.68, .70, 1.73, .17, .028, .108, .018)
        roof.solid(x, 2.42, .58, .19, .07, .11, .141)
        edges.frame(x, 1.68, .72, 1.75, .18, .019, .108, .137)
        for row in range(17):
            y = 1.02 + row * .082
            grooves.solid(x, y, .48 if row % 4 else .55, .012, .005, .019, .025, .002)
        amber.solid(x, .94, .32, .045, .012, .03, .052)
        amber.solid(x + .23, 1.57, .025, .78, .01, .023, .035)
        ceramic.washer(x, 2.73, .095, .046, .024, .122, 24)
        substrate.solid(x, 2.73, .055, .055, .02, .02, .09)
        for k in range(5):
            ceramic.solid(x - .2 + k * .1, .65, .027, .11, .009, .05, .088)

    bridge = layer('Optical_Bridge_Glass')
    bridge.solid(.12, .61, 3.13, .22, .065, .102, .154, .016)
    # Fine routing, registration grooves and scale marks catch grazing light.
    routes = layer('Optical_Film_Edge')
    for y in (.34, .39, 3.31):
        routes.solid(.12, y, 3.93, .009, .003, .055, .063, .002)
    for i in range(30):
        routes.solid(-1.7 + i * .125, .32, .012, .075 if i % 5 else .12, .003, .055, .065, .002)
    for x in (-2.2, 2.2):
        for i in range(11):
            routes.solid(x, .94 + i * .16, .08, .012, .004, .12, .133, .002)

    fasteners = layer('Titanium_Fasteners')
    for x in (-2.33, 2.33):
        for y in (.16, 3.54):
            fasteners.washer(x, y, .059, .022, .18, .212)
            substrate.solid(x, y, .026, .026, .006, .179, .198)

    binary = bytearray()
    views, accessors, meshes, nodes, materials = [], [], [], [], []

    def attribute(values):
        flat = [v for row in values for v in row]
        offset = len(binary)
        binary.extend(struct.pack('<' + 'f' * len(flat), *flat))
        views.append({'buffer': 0, 'byteOffset': offset, 'byteLength': len(flat) * 4, 'target': 34962})
        accessors.append({'bufferView': len(views) - 1, 'componentType': 5126,
                          'count': len(values), 'type': 'VEC3',
                          'min': [min(v[i] for v in values) for i in range(3)],
                          'max': [max(v[i] for v in values) for i in range(3)]})
        return len(accessors) - 1

    for name, surface in surfaces.items():
        # Share identical position/normal pairs within each surface. Preserve
        # deliberate normal splits at optical lips rather than welding seams.
        unique, positions, normals, indices = {}, [], [], []
        for point, normal in zip(surface.positions, surface.normals):
            key = (*point, *normal)
            if key not in unique:
                unique[key] = len(positions)
                positions.append(point)
                normals.append(normal)
            indices.append(unique[key])
        index_offset = len(binary)
        binary.extend(struct.pack('<' + 'I' * len(indices), *indices))
        views.append({'buffer': 0, 'byteOffset': index_offset, 'byteLength': len(indices) * 4, 'target': 34963})
        accessors.append({'bufferView': len(views) - 1, 'componentType': 5125,
                          'count': len(indices), 'type': 'SCALAR'})
        index_accessor = len(accessors) - 1
        pbr = {'baseColorFactor': [.82, .77, .68, 1], 'metallicFactor': .05, 'roughnessFactor': .45}
        extensions = {'KHR_materials_ior': {'ior': 1.46},
                      'KHR_materials_clearcoat': {'clearcoatFactor': .3, 'clearcoatRoughnessFactor': .25}}
        if name in ('Frosted_Polymer', 'Ivory_Edges'):
            extensions.update(KHR_materials_transmission={'transmissionFactor': .9 if name == 'Frosted_Polymer' else .35},
                              KHR_materials_volume={'thicknessFactor': .12 if name == 'Frosted_Polymer' else .04,
                                                    'attenuationDistance': 2, 'attenuationColor': [.93, .9, .87]})
        materials.append({'name': name, 'pbrMetallicRoughness': pbr, 'extensions': extensions})
        meshes.append({'primitives': [{'attributes': {'POSITION': attribute(positions),
                                                      'NORMAL': attribute(normals)},
                                      'indices': index_accessor, 'material': len(materials) - 1}]})
        nodes.append({'name': 'Radar_' + name, 'mesh': len(meshes) - 1})
    extensions_used = sorted({key for m in materials for key in m['extensions']})
    gltf = {'asset': {'version': '2.0', 'generator': 'Learning Radar original layered optical archive'},
            'scene': 0, 'scenes': [{'nodes': list(range(len(nodes)))}], 'nodes': nodes,
            'meshes': meshes, 'materials': materials, 'extensionsUsed': extensions_used,
            'buffers': [{'byteLength': len(binary)}], 'bufferViews': views, 'accessors': accessors}
    encoded = json.dumps(gltf, separators=(',', ':')).encode()
    encoded += b' ' * (-len(encoded) % 4)
    binary += b'\0' * (-len(binary) % 4)
    data = (struct.pack('<4sII', b'glTF', 2, 28 + len(encoded) + len(binary))
            + struct.pack('<I4s', len(encoded), b'JSON') + encoded
            + struct.pack('<I4s', len(binary), b'BIN\0') + binary)
    out = ROOT / 'public/assets/archive-cassette.glb'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    print(f'Generated original layered optical archive: {len(surfaces)} surfaces, {len(data)} bytes')


if __name__ == '__main__':
    main()
