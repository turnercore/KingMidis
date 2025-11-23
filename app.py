from flask import Flask, render_template, send_from_directory, abort
from pathlib import Path
import os

app = Flask(__name__)

# Base directory for MIDI files inside the container
BASE_DIR = Path(os.environ.get("MIDI_ROOT", "/midis")).resolve()


def get_safe_path(subpath: str) -> Path:
    """
    Resolve a subpath under BASE_DIR and prevent directory traversal.
    """
    full = (BASE_DIR / subpath).resolve()
    if full != BASE_DIR and BASE_DIR not in full.parents:
        abort(404)
    return full


@app.route("/")
@app.route("/browse/", defaults={"subpath": ""})
@app.route("/browse/<path:subpath>")
def browse(subpath: str):
    full_path = get_safe_path(subpath)

    if full_path.is_file():
        # Optional: directly serve if someone hits /browse/file.mid
        return send_from_directory(full_path.parent, full_path.name)

    if not full_path.exists() or not full_path.is_dir():
        abort(404)

    entries = []
    for child in sorted(full_path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        rel = child.relative_to(BASE_DIR)
        entries.append({
            "name": child.name,
            "rel_path": str(rel),
            "is_dir": child.is_dir(),
            "is_midi": child.is_file() and child.suffix.lower() in [".mid", ".midi"],
        })

    parent_rel = None
    if full_path != BASE_DIR:
        parent_rel = str(full_path.parent.relative_to(BASE_DIR))

    return render_template(
        "browse.html",
        entries=entries,
        subpath=subpath,
        parent_rel=parent_rel
    )


@app.route("/midi/<path:subpath>")
def midi_file(subpath: str):
    full_path = get_safe_path(subpath)

    if not full_path.is_file() or full_path.suffix.lower() not in [".mid", ".midi"]:
        abort(404)

    return send_from_directory(full_path.parent, full_path.name)


if __name__ == "__main__":
    # For local (non-Docker) testing
    app.run(host="0.0.0.0", port=5000, debug=True)
