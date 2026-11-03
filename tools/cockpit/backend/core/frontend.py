"""Serves the static frontend export (tools/cockpit/frontend/out) and the robot meshes the
login background loads (docs/specs/spec_cockpit.md §4, §7.3)."""

from __future__ import annotations

import mimetypes
from pathlib import Path

from django.conf import settings
from django.http import FileResponse, Http404, HttpResponse, HttpResponseRedirect

# package:// roots of the meshes in public/login/ar3.urdf. The meshes are the open-source
# upstream AR4 ones; they are public, so the login page may load them before sign-in.
MESH_PACKAGES = {
    "annin_ar4_description": "src/external/ar4_ros_driver/annin_ar4_description",
    "ar3_description": "src/ar3_description",
}
MESH_SUFFIXES = {".stl", ".dae"}

mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("model/stl", ".stl")


def _inside(root: Path, rel: str) -> Path | None:
    root = root.resolve()
    target = (root / rel).resolve()
    return target if target == root or root in target.parents else None


def _file(path: Path, *, immutable: bool = False, status: int = 200) -> FileResponse:
    ctype, _ = mimetypes.guess_type(path.name)
    resp = FileResponse(path.open("rb"), content_type=ctype or "application/octet-stream")
    resp.status_code = status
    resp["Cache-Control"] = "public, max-age=31536000, immutable" if immutable else "no-cache"
    return resp


def mesh(request, package: str, path: str):
    root = MESH_PACKAGES.get(package)
    target = _inside(settings.REPO_DIR / root, path) if root else None
    if target is None or target.suffix.lower() not in MESH_SUFFIXES or not target.is_file():
        raise Http404("no such mesh")
    resp = _file(target)
    resp["Cache-Control"] = "public, max-age=3600"
    return resp


def frontend(request, path: str = ""):
    root: Path = settings.FRONTEND_DIR
    if not root.is_dir():
        return HttpResponse(
            "The cockpit frontend is not built. Run ./cockpit.sh (it runs npm run build), "
            "or cd tools/cockpit/frontend && npm ci && npm run build.\n",
            status=503,
            content_type="text/plain",
        )
    target = _inside(root, path)
    if target is None:
        raise Http404()
    if target.is_file():
        return _file(target, immutable=path.startswith("_next/static/"))
    if target.is_dir() and (target / "index.html").is_file():
        if path and not request.path.endswith("/"):
            return HttpResponseRedirect(request.path + "/")
        return _file(target / "index.html")
    if (root / f"{path.rstrip('/')}.html").is_file():
        return _file(root / f"{path.rstrip('/')}.html")
    not_found = root / "404.html"
    if not_found.is_file():
        return _file(not_found, status=404)
    raise Http404()
