"""Minimal COLLADA 1.4.1 writer for static meshes (Z-up, metres), as BeamNG's TSStatic reads them."""

from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

import numpy as np
from numpy.typing import NDArray


def _normals(vertices: NDArray[np.float64], faces: NDArray[np.int64]) -> NDArray[np.float64]:
    tri = vertices[faces]
    face_n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    normals = np.zeros_like(vertices)
    for i in range(3):
        np.add.at(normals, faces[:, i], face_n)
    length = np.linalg.norm(normals, axis=1, keepdims=True)
    return np.where(length > 1e-12, normals / np.maximum(length, 1e-12), np.array([0.0, 0.0, 1.0]))


def _floats(a: NDArray[np.float64], digits: int) -> str:
    return " ".join(f"{v:.{digits}f}".rstrip("0").rstrip(".") or "0" for v in a.reshape(-1).tolist())


def write_dae(
    path: Path, parts: list[tuple[str, str, NDArray[np.float64], NDArray[np.float64], NDArray[np.int64]]]
) -> None:
    """parts: (mesh name, material name, vertices (n,3), uvs (n,2), faces (m,3)). Coordinates as given."""
    materials = sorted({p[1] for p in parts})
    out: list[str] = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<COLLADA xmlns="http://www.collada.org/2005/11/COLLADASchema" version="1.4.1">',
        '<asset><unit name="meter" meter="1"/><up_axis>Z_UP</up_axis></asset>',
        "<library_effects>",
    ]
    for m in materials:
        out.append(
            f'<effect id="{m}-fx"><profile_COMMON><technique sid="common"><lambert><diffuse>'
            f"<color>0.5 0.5 0.5 1</color></diffuse></lambert></technique></profile_COMMON></effect>"
        )
    out.append("</library_effects><library_materials>")
    for m in materials:
        out.append(f'<material id="{m}" name="{m}"><instance_effect url="#{m}-fx"/></material>')
    out.append("</library_materials><library_geometries>")
    for name, material, vertices, uvs, faces in parts:
        gid = escape(name)
        v = np.asarray(vertices, dtype=np.float64)
        f = np.asarray(faces, dtype=np.int64)
        n = _normals(v, f)
        out.append(f'<geometry id="{gid}" name="{gid}"><mesh>')
        for sid, arr, stride, params, digits in (
            ("pos", v, 3, "XYZ", 3),
            ("nrm", n, 3, "XYZ", 3),
            ("uv", np.asarray(uvs, dtype=np.float64), 2, "ST", 3),
        ):
            accessor = "".join(f'<param name="{c}" type="float"/>' for c in params)
            out.append(
                f'<source id="{gid}-{sid}"><float_array id="{gid}-{sid}-a" count="{arr.size}">'
                f"{_floats(arr, digits)}</float_array><technique_common>"
                f'<accessor source="#{gid}-{sid}-a" count="{len(arr)}" stride="{stride}">'
                f"{accessor}</accessor>"
                "</technique_common></source>"
            )
        out.append(f'<vertices id="{gid}-vtx"><input semantic="POSITION" source="#{gid}-pos"/></vertices>')
        index = " ".join(map(str, np.repeat(f.reshape(-1), 3).tolist()))  # same index for pos, normal, uv
        out.append(
            f'<triangles material="{material}" count="{len(f)}">'
            f'<input semantic="VERTEX" source="#{gid}-vtx" offset="0"/>'
            f'<input semantic="NORMAL" source="#{gid}-nrm" offset="1"/>'
            f'<input semantic="TEXCOORD" source="#{gid}-uv" offset="2" set="0"/>'
            f"<p>{index}</p></triangles></mesh></geometry>"
        )
    out.append('</library_geometries><library_visual_scenes><visual_scene id="scene" name="scene">')
    for name, material, *_ in parts:
        gid = escape(name)
        out.append(
            f'<node id="{gid}-node" name="{gid}" type="NODE"><instance_geometry url="#{gid}">'
            f'<bind_material><technique_common><instance_material symbol="{material}" target="#{material}"/>'
            "</technique_common></bind_material></instance_geometry></node>"
        )
    out.append(
        "</visual_scene></library_visual_scenes>"
        '<scene><instance_visual_scene url="#scene"/></scene></COLLADA>'
    )
    path.write_text("\n".join(out), encoding="utf-8")
