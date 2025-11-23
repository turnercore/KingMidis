from flask import Flask, render_template, send_from_directory, abort, request, jsonify
from pathlib import Path
import os
import re
import yaml

app = Flask(__name__)

# Base directory for MIDI files inside the container
BASE_DIR = Path(os.environ.get("MIDI_ROOT", "/midis")).resolve()
ADMIN_MODE = os.environ.get("ADMIN_MODE", "").lower() in ("1", "true", "yes")

META_SUFFIXES = [".meta", ".yml", ".yaml"]
DEFAULT_META_SUFFIX = ".yml"
SHEET_SUFFIX = ".pdf"

INSTRUMENT_ICONS = {
    "piano": "🎹",
    "keyboard": "🎹",
    "organ": "🎹",
    "violin": "🎻",
    "strings": "🎻",
    "cello": "🎻",
    "orchestra": "🎻",
    "guitar": "🎸",
    "bass": "🎸",
    "drum": "🥁",
    "percussion": "🥁",
    "flute": "🪈",
    "clarinet": "🪈",
    "sax": "🎷",
    "saxophone": "🎷",
    "trumpet": "🎺",
    "horn": "🎺",
    "trombone": "🎺",
    "voice": "🎤",
    "choir": "🎤",
    "harp": "🎼",
}
DEFAULT_INSTRUMENT_ICON = "🎹"

MIDI_EXTENSIONS = [".mid", ".midi"]


def get_safe_path(subpath: str) -> Path:
    """
    Resolve a subpath under BASE_DIR and prevent directory traversal.
    """
    full = (BASE_DIR / subpath).resolve()
    if full != BASE_DIR and BASE_DIR not in full.parents:
        abort(404)
    return full


def load_meta(midi_path: Path) -> dict | None:
    for suffix in META_SUFFIXES:
        meta_path = midi_path.with_suffix(suffix)
        if not meta_path.exists() or not meta_path.is_file():
            continue

        try:
            data = yaml.safe_load(meta_path.read_text()) or {}
            if not isinstance(data, dict):
                return {}
            return data
        except Exception:
            return {}
    return None


def normalize_instruments(instruments_field) -> list[str]:
    if isinstance(instruments_field, str):
        return [instruments_field.strip()] if instruments_field.strip() else []
    if isinstance(instruments_field, (list, tuple)):
        cleaned = []
        for inst in instruments_field:
            if not inst:
                continue
            cleaned.append(str(inst).strip())
        return [inst for inst in cleaned if inst]
    return []


def instrument_icon_for(instruments: list[str]) -> str:
    for inst in instruments:
        key = inst.lower()
        for alias, icon in INSTRUMENT_ICONS.items():
            if alias in key:
                return icon
    return DEFAULT_INSTRUMENT_ICON


def build_meta_context(midi_path: Path, meta_data: dict | None, rel_path: str) -> dict:
    meta_data = meta_data or {}
    parent_name = midi_path.parent.name if midi_path.parent != midi_path else ""
    display_name = meta_data.get("name") or midi_path.stem

    composer = meta_data.get("composer") or parent_name or midi_path.stem
    editor = meta_data.get("editor")
    modified_by = meta_data.get("modified_by")
    source = meta_data.get("source")
    instruments = normalize_instruments(meta_data.get("instruments"))

    license_value = (
        meta_data.get("license")
        or meta_data.get("liscense")
        or "Public Domain"
    )

    lines = [f"{display_name} by {composer}"]
    if editor:
        lines.append(f"Edited by {editor}")
    if modified_by:
        lines.append(f"Modified by {modified_by}")
    if source:
        lines.append(f"Source: {source}")
    lines.append(f"Licensed under {license_value}")

    form_defaults = {
        "name": meta_data.get("name", ""),
        "composer": meta_data.get("composer", ""),
        "editor": meta_data.get("editor", ""),
        "modified_by": meta_data.get("modified_by", ""),
        "source": meta_data.get("source", ""),
        "license": meta_data.get("license") or meta_data.get("liscense") or "Public Domain",
        "instruments": ", ".join(instruments),
    }

    return {
        "has_meta": bool(meta_data),
        "display_name": display_name,
        "composer": composer,
        "editor": editor,
        "modified_by": modified_by,
        "source": source,
        "instruments": instruments,
        "license": license_value,
        "attribution_text": "\n".join(lines),
        "rel_path": rel_path,
        "form_defaults": form_defaults,
        "instrument_icon": instrument_icon_for(instruments),
    }


def sanitize_slug(value: str) -> str:
    slug = (value or "").strip()
    for ext in MIDI_EXTENSIONS:
        if slug.lower().endswith(ext):
            slug = slug[: -len(ext)]
            break

    if not slug:
        raise ValueError("Filename is required")
    if any(ch in slug for ch in r'\/:*?"<>|'):
        raise ValueError("Filename contains invalid characters")
    return slug


def meta_path_for_write(midi_path: Path) -> Path:
    for suffix in META_SUFFIXES:
        meta_candidate = midi_path.with_suffix(suffix)
        if meta_candidate.exists():
            return meta_candidate
    return midi_path.with_suffix(DEFAULT_META_SUFFIX)


def is_midi_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in MIDI_EXTENSIONS


def rename_sidecars(original_path: Path, new_stem: str):
    for suffix in META_SUFFIXES + [SHEET_SUFFIX]:
        old_path = original_path.with_suffix(suffix)
        if old_path.exists() and old_path.is_file():
            old_path.rename(old_path.with_name(new_stem + suffix))


@app.route("/", defaults={"subpath": ""})
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
        is_midi = is_midi_file(child)
        entry = {
            "name": child.name,
            "display_name": child.stem if is_midi else child.name,
            "rel_path": str(rel),
            "is_dir": child.is_dir(),
            "is_midi": is_midi,
            "slug": child.stem if is_midi else child.name,
        }

        if is_midi:
            meta_data = load_meta(child)
            meta_context = build_meta_context(child, meta_data, str(rel))
            entry["meta"] = meta_context
            entry["instrument_icon"] = meta_context["instrument_icon"]
            sheet_path = child.with_suffix(SHEET_SUFFIX)
            if sheet_path.exists() and sheet_path.is_file():
                entry["sheet_rel_path"] = str(sheet_path.relative_to(BASE_DIR))
            else:
                entry["sheet_rel_path"] = None
        else:
            entry["meta"] = None
            entry["sheet_rel_path"] = None
            entry["instrument_icon"] = DEFAULT_INSTRUMENT_ICON

        entries.append(entry)

    parent_rel = None
    if full_path != BASE_DIR:
        parent_rel = str(full_path.parent.relative_to(BASE_DIR))

    return render_template(
        "browse.html",
        entries=entries,
        subpath=subpath,
        parent_rel=parent_rel,
        admin_mode=ADMIN_MODE
    )


@app.route("/midi/<path:subpath>")
def midi_file(subpath: str):
    full_path = get_safe_path(subpath)

    if not is_midi_file(full_path):
        abort(404)

    return send_from_directory(full_path.parent, full_path.name)


@app.route("/attachment/<path:subpath>")
def attachment_file(subpath: str):
    full_path = get_safe_path(subpath)

    if not full_path.is_file() or full_path.suffix.lower() != ".pdf":
        abort(404)

    return send_from_directory(full_path.parent, full_path.name)


@app.post("/api/entry/update")
def update_entry():
    if not ADMIN_MODE:
        abort(403)

    payload = request.get_json(silent=True) or {}
    rel_path = payload.get("rel_path")
    if not rel_path:
        return jsonify({"error": "Missing rel_path"}), 400

    midi_path = get_safe_path(rel_path)
    if not is_midi_file(midi_path):
        abort(404)

    new_slug = payload.get("new_slug")
    metadata = payload.get("metadata") or {}

    try:
        sanitized_slug = sanitize_slug(new_slug) if new_slug else midi_path.stem
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    updated_path = midi_path
    if sanitized_slug != midi_path.stem:
        target_path = midi_path.with_name(sanitized_slug + midi_path.suffix)
        if target_path.exists():
            return jsonify({"error": "A file with that name already exists"}), 400
        old_path = midi_path
        midi_path.rename(target_path)
        rename_sidecars(old_path, sanitized_slug)
        updated_path = target_path

    instruments_field = metadata.get("instruments") or []
    if isinstance(instruments_field, str):
        instruments = [inst.strip() for inst in instruments_field.split(",") if inst.strip()]
    elif isinstance(instruments_field, (list, tuple)):
        instruments = [str(inst).strip() for inst in instruments_field if str(inst).strip()]
    else:
        instruments = []

    meta_payload = {
        "name": metadata.get("name", ""),
        "composer": metadata.get("composer", ""),
        "editor": metadata.get("editor", ""),
        "modified_by": metadata.get("modified_by", ""),
        "source": metadata.get("source", ""),
        "license": metadata.get("license") or "Public Domain",
        "instruments": instruments,
    }

    meta_path = meta_path_for_write(updated_path)
    meta_path.write_text(
        yaml.safe_dump(meta_payload, allow_unicode=True, sort_keys=False)
    )

    return jsonify(
        {
            "success": True,
            "rel_path": str(updated_path.relative_to(BASE_DIR)),
        }
    )


if __name__ == "__main__":
    # For local (non-Docker) testing
    app.run(host="0.0.0.0", port=5000, debug=True)
