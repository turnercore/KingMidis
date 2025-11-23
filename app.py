from flask import Flask, render_template, send_from_directory, abort, request, jsonify, redirect, url_for
from pathlib import Path
from collections.abc import Callable
from openai import OpenAI
import os
import io
import zipfile
import re
import json
import yaml
import requests
from urllib.parse import urljoin
from bs4 import BeautifulSoup

app = Flask(__name__)

# Base directory for MIDI files inside the container
BASE_DIR = Path(os.environ.get("MIDI_ROOT", "/midis")).resolve()
ADMIN_MODE = os.environ.get("ADMIN_MODE", "").lower() in ("1", "true", "yes")

META_SUFFIXES = [".meta", ".yml", ".yaml"]
DEFAULT_META_SUFFIX = ".yml"
SHEET_SUFFIX = ".pdf"
MUTOPIA_SEARCH_ENDPOINT = "https://www.mutopiaproject.org/cgibin/make-table.cgi"
SCRUB_SOURCES = [
    {"id": "mutopia", "label": "Mutopia Project"},
]
REQUEST_HEADERS = {"User-Agent": "KingMidis/1.0"}
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5-nano")
openai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
AI_ENABLED = openai_client is not None

INSTRUMENT_PRESETS = [
    {"id": "piano", "label": "Grand Piano", "emoji": "🎹"},
    {"id": "digital", "label": "Digital Synth", "emoji": "🎛️"},
    {"id": "strings", "label": "Strings", "emoji": "🎻"},
    {"id": "guitar", "label": "Guitar", "emoji": "🎸"},
    {"id": "harp", "label": "Harp", "emoji": "🪕"},
    {"id": "brass", "label": "Brass", "emoji": "🎺"},
    {"id": "voice", "label": "Choir/Voice", "emoji": "🎤"},
    {"id": "organ", "label": "Organ", "emoji": "⛪"},
    {"id": "woodwind", "label": "Woodwinds", "emoji": "🎷"},
    {"id": "percussion", "label": "Percussion", "emoji": "🥁"},
    {"id": "pad", "label": "Ambient Pad", "emoji": "🌀"},
]
DEFAULT_INSTRUMENT_ID = "piano"
INSTRUMENT_LOOKUP = {preset["id"]: preset for preset in INSTRUMENT_PRESETS}
INSTRUMENT_KEYWORDS = [
    (["guitar"], "guitar"),
    (["harp"], "harp"),
    (["choir", "voice", "vocal", "singer", "soprano", "alto", "tenor", "bass"], "voice"),
    (["violin", "viola", "cello", "string", "orchestra", "ensemble"], "strings"),
    (["flute", "oboe", "clarinet", "sax", "woodwind", "recorder"], "woodwind"),
    (["organ", "pipe", "church"], "organ"),
    (["brass", "trumpet", "trombone", "horn", "cornet"], "brass"),
    (["drum", "percussion", "snare", "timpani", "cymbal"], "percussion"),
    (["pad", "ambient", "sustain"], "pad"),
    (["harpsichord", "synth", "keyboard"], "digital"),
]

MIDI_EXTENSIONS = [".mid", ".midi"]


def get_safe_path(subpath: str) -> Path:
    """
    Resolve a subpath under BASE_DIR and prevent directory traversal.
    """
    full = (BASE_DIR / subpath).resolve()
    if full != BASE_DIR and BASE_DIR not in full.parents:
        abort(404)
    return full


def slugify(value: str) -> str:
    value = (value or "").strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-{2,}", "-", value).strip("-")
    return value or "midi"


def guess_instrument_from_text(text: str) -> str:
    haystack = (text or "").lower()
    for keywords, preset_id in INSTRUMENT_KEYWORDS:
        for keyword in keywords:
            if keyword in haystack:
                return preset_id
    return DEFAULT_INSTRUMENT_ID


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


def ensure_meta(midi_path: Path) -> dict:
    meta = load_meta(midi_path)
    if meta is not None:
        return meta

    default_meta = {
        "name": midi_path.stem,
        "composer": midi_path.parent.name,
        "license": "Public Domain",
        "instrument": DEFAULT_INSTRUMENT_ID,
    }
    meta_path = meta_path_for_write(midi_path)
    meta_path.write_text(yaml.safe_dump(
        default_meta, allow_unicode=True, sort_keys=False))
    return default_meta


def normalize_instrument_choice(value) -> str:
    if isinstance(value, str) and value.strip():
        candidate = value.strip().lower()
        for preset in INSTRUMENT_PRESETS:
            if preset["id"] == candidate:
                return preset["id"]

        # legacy, check for emoji or label containing substring
        for preset in INSTRUMENT_PRESETS:
            if candidate in preset["label"].lower() or candidate in preset["emoji"]:
                return preset["id"]

    return DEFAULT_INSTRUMENT_ID


def parse_instrument_parts(text: str) -> list[str]:
    if not text:
        return []
    work = text.strip()
    if work.lower().startswith("for "):
        work = work[4:].strip()
    if ":" in work:
        work = work.split(":", 1)[1]
    parts = [part.strip() for part in work.split(",") if part.strip()]
    return parts


def derive_collection_title(subpath: str) -> str:
    if not subpath:
        return "King Midis"
    root = subpath.strip("/").split("/", 1)[0]
    clean = re.sub(r"[-_]+", " ", root).strip()
    if clean:
        return clean.title()
    return "King Midis"


def build_meta_context(midi_path: Path, meta_data: dict | None, rel_path: str) -> dict:
    meta_data = meta_data or {}
    parent_name = midi_path.parent.name if midi_path.parent != midi_path else ""
    display_name = meta_data.get("name") or midi_path.stem

    composer = meta_data.get("composer") or parent_name or midi_path.stem
    editor = meta_data.get("editor")
    modified_by = meta_data.get("modified_by")
    source = meta_data.get("source")
    instrument_choice = normalize_instrument_choice(
        meta_data.get("instrument") or meta_data.get("instruments"))
    preset_lookup = {preset["id"]: preset for preset in INSTRUMENT_PRESETS}
    preset = preset_lookup.get(
        instrument_choice, preset_lookup[DEFAULT_INSTRUMENT_ID])

    license_value = (
        meta_data.get("license")
        or meta_data.get("liscense")
        or "Public Domain"
    )

    genre_value = meta_data.get("genre") or ""
    tags_value = meta_data.get("tags") or []
    if isinstance(tags_value, str):
        tags_list = [tag.strip()
                     for tag in tags_value.split(",") if tag.strip()]
    elif isinstance(tags_value, (list, tuple)):
        tags_list = [str(tag).strip()
                     for tag in tags_value if str(tag).strip()]
    else:
        tags_list = []

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
        "instrument": instrument_choice,
        "bpm": meta_data.get("bpm") or meta_data.get("tempo") or "",
        "genre": genre_value,
        "tags": ", ".join(tags_list),
    }

    preset = INSTRUMENT_LOOKUP.get(
        instrument_choice) or INSTRUMENT_LOOKUP[DEFAULT_INSTRUMENT_ID]

    return {
        "has_meta": bool(meta_data),
        "display_name": display_name,
        "composer": composer,
        "editor": editor,
        "modified_by": modified_by,
        "source": source,
        "instrument": instrument_choice,
        "license": license_value,
        "attribution_text": "\n".join(lines),
        "rel_path": rel_path,
        "form_defaults": form_defaults,
        "instrument_icon": preset["emoji"],
        "instrument_label": preset["label"],
        "genre": genre_value,
        "tags": tags_list,
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


def locate_sheet_path(midi_path: Path) -> Path | None:
    base = midi_path.with_suffix(SHEET_SUFFIX)
    if base.exists():
        return base

    possible_suffixes = ["-a4", "-letter"]
    for suffix in possible_suffixes:
        candidate = midi_path.with_name(
            f"{midi_path.stem}{suffix}{SHEET_SUFFIX}")
        if candidate.exists():
            return candidate

    return None


def download_file(url: str, destination: Path) -> None:
    resp = requests.get(url, timeout=30, headers=REQUEST_HEADERS)
    resp.raise_for_status()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(resp.content)


def update_metadata_for_path(midi_path: Path, listing: dict, part_label: str | None = None, default_instrument: str | None = None) -> str:
    existing = load_meta(midi_path)
    merged = dict(existing) if existing else {}

    updates = {
        "name": f"{listing['title']} - {part_label}" if part_label else listing["title"],
        "composer": listing.get("composer"),
        "license": listing.get("license"),
        "source": listing.get("source", "Mutopia Project"),
        "instrument": default_instrument or listing.get("instrument_id"),
        "style": listing.get("style"),
        "period": listing.get("period"),
        "catalog": listing.get("catalog"),
        "instrumentation": listing.get("instrumentation"),
        "notes": listing.get("notes"),
        "editor": listing.get("editor"),
    }

    changed = False
    for key, value in updates.items():
        if value and not merged.get(key):
            merged[key] = value
            changed = True

    meta_path = meta_path_for_write(midi_path)
    meta_path.write_text(yaml.safe_dump(
        merged, allow_unicode=True, sort_keys=False))

    if not existing:
        return "Metadata created"
    return "Metadata updated" if changed else "Metadata unchanged"


def statuses_have_changes(statuses: list[str]) -> bool:
    haystack = " ".join(statuses).lower()
    for keyword in ("downloaded", "created", "updated"):
        if keyword in haystack:
            return True
    return False


def clean_composer_name(raw: str) -> str:
    text = re.sub(r"\(.*?\)", "", raw or "")
    text = re.sub(r"^by\s+", "", text, flags=re.IGNORECASE)
    return " ".join(text.split())


def parse_mutopia_table(table) -> dict | None:
    rows = table.find_all("tr")
    if len(rows) < 4:
        return None

    def cell(row_index: int, col_index: int):
        try:
            return rows[row_index].find_all("td")[col_index]
        except Exception:
            return None

    def cell_text(row_index: int, col_index: int) -> str:
        c = cell(row_index, col_index)
        if not c:
            return ""
        return " ".join(c.stripped_strings)

    title = cell_text(0, 0)
    if not title:
        return None

    composer = clean_composer_name(cell_text(0, 1))
    catalog = cell_text(0, 2)
    instrumentation_text = cell_text(1, 0)
    period = cell_text(1, 1)
    style = cell_text(1, 2)
    notes = cell_text(2, 0)
    license_cell = cell(2, 1)
    license_text = ""
    if license_cell:
        link = license_cell.find("a")
        license_text = link.get_text(strip=True) if link else cell_text(2, 1)

    midi_cell = cell(3, 1)
    midi_url = None
    midi_zip_url = None
    if midi_cell:
        midi_link = midi_cell.find("a")
        if midi_link:
            href = urljoin(MUTOPIA_SEARCH_ENDPOINT, midi_link["href"])
            if "zipped" in midi_cell.get("class", []) or href.lower().endswith(".zip"):
                midi_zip_url = href
            else:
                midi_url = href
    if not midi_url and not midi_zip_url:
        return {"title": title, "skip": "no midi link"}

    pdf_url = None
    pdf_row = rows[4] if len(rows) > 4 else None
    pdf_zip_url = None
    if pdf_row:
        pdf_cells = pdf_row.find_all("td")
        for td in pdf_cells:
            link = td.find("a")
            if not link:
                continue
            href = urljoin(MUTOPIA_SEARCH_ENDPOINT, link["href"])
            if "zipped" in td.get("class", []) or href.lower().endswith(".zip"):
                if "pdf" in href.lower():
                    pdf_zip_url = href
            else:
                if href.lower().endswith(".pdf"):
                    pdf_url = href
                    if "-a4" in href.lower():
                        break

    instrument_id = guess_instrument_from_text(instrumentation_text or title)
    editor_text = cell_text(2, 0)

    return {
        "title": title,
        "composer": composer or "Unknown",
        "catalog": catalog,
        "instrumentation": instrumentation_text,
        "period": period,
        "style": style,
        "notes": notes,
        "license": license_text,
        "midi_url": midi_url,
        "midi_zip_url": midi_zip_url,
        "pdf_url": pdf_url,
        "pdf_zip_url": pdf_zip_url,
        "instrument_id": instrument_id,
        "editor": editor_text,
    }


def scrobble_mutopia(
    search_term: str,
    download_pdf: bool = True,
    mode: str = "full",
    *,
    log: Callable[[str], None] | None = None,
    source: str = "mutopia",
) -> list[dict]:
    if source != "mutopia":
        raise ValueError("Unsupported source")

    log_fn = log or (lambda msg: None)
    log_fn(f"Scrubbing Mutopia for '{search_term}' (mode={mode})")

    sanitized_term = slugify(search_term)
    target_dir = BASE_DIR / sanitized_term
    target_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict] = []
    start_offset = 0
    test_state = {"first": False, "second": False, "zip": False}

    while True:
        params = {
            "searchingfor": search_term,
            "Composer": "",
            "Instrument": "",
            "Style": "",
            "collection": "",
            "id": "",
            "solo": "",
            "recent": "",
            "timelength": 1,
            "timeunit": "week",
            "lilyversion": "",
            "preview": "",
            "startat": start_offset,
        }
        resp = requests.get(
            MUTOPIA_SEARCH_ENDPOINT,
            params=params,
            timeout=30,
            headers=REQUEST_HEADERS,
        )
        resp.raise_for_status()
        if resp.apparent_encoding:
            resp.encoding = resp.apparent_encoding
        soup = BeautifulSoup(resp.text, "html.parser")
        tables = soup.select("table.result-table")
        if not tables:
            if start_offset == 0:
                log_fn("No listings returned.")
                results.append(
                    {"title": "No results", "status": "Nothing found"})
            break

        for table in tables:
            listing = parse_mutopia_table(table)
            if not listing:
                continue
            listing["source"] = "Mutopia Project"

            if listing.get("skip"):
                log_fn(f"Skipping {listing.get('title')}: {listing['skip']}")
                results.append({"title": listing.get(
                    "title"), "status": listing["skip"]})
                continue

            target_phase = None
            if mode == "test":
                if not test_state["first"]:
                    target_phase = "first"
                elif not test_state["second"]:
                    if start_offset == 0:
                        continue
                    target_phase = "second"
                elif not test_state["zip"]:
                    if not listing.get("midi_zip_url"):
                        continue
                    target_phase = "zip"
                else:
                    continue

            log_fn(f"Processing {listing['title']}")
            statuses = process_listing(
                listing, target_dir, download_pdf, log_fn)
            summary_status = "; ".join(statuses)
            log_fn(f"Finished {listing['title']}: {summary_status}")
            results.append(
                {"title": listing["title"], "status": summary_status})

            if mode == "test" and target_phase:
                if statuses_have_changes(statuses):
                    test_state[target_phase] = True
                    if all(test_state.values()):
                        log_fn("Test run complete.")
                        return results
                else:
                    log_fn("Entry unchanged during test run; searching for another example.")
                    continue

        next_link = soup.find(
            "a", string=lambda text: text and "Next 10" in text)
        if next_link:
            start_offset += 10
            log_fn("Moving to next page…")
        else:
            break

    if mode == "test":
        if not test_state["zip"]:
            log_fn("Test run finished without encountering a multi-part entry.")
        else:
            log_fn("Test run complete.")
    else:
        log_fn("Scrubbing complete.")
    return results


def process_listing(listing: dict, target_dir: Path, download_pdf: bool, log_fn) -> list[str]:
    if listing.get("midi_zip_url"):
        return process_zip_listing(listing, target_dir, download_pdf, log_fn)
    return process_single_listing(listing, target_dir, download_pdf)


def ensure_pdf_download(url: str, destination: Path) -> str:
    if destination.exists():
        return "PDF exists"
    download_file(url, destination)
    return "PDF downloaded"


def process_single_listing(listing: dict, target_dir: Path, download_pdf: bool) -> list[str]:
    statuses: list[str] = []
    slug = slugify(listing["title"])
    midi_path = target_dir / f"{slug}.mid"

    if midi_path.exists():
        statuses.append("MIDI exists")
    else:
        download_file(listing["midi_url"], midi_path)
        statuses.append("MIDI downloaded")

    if download_pdf:
        pdf_status = download_pdf_for_listing(
            listing, target_dir / f"{slug}-a4.pdf")
        if pdf_status:
            statuses.append(pdf_status)

    statuses.append(update_metadata_for_path(midi_path, listing))
    return statuses


def download_pdf_for_listing(listing: dict, destination: Path) -> str | None:
    if destination.exists():
        return "PDF exists"
    if listing.get("pdf_url"):
        try:
            download_file(listing["pdf_url"], destination)
            return "PDF downloaded"
        except Exception as exc:
            return f"PDF failed: {exc}"
    if listing.get("pdf_zip_url"):
        try:
            resp = requests.get(
                listing["pdf_zip_url"], timeout=30, headers=REQUEST_HEADERS)
            resp.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(resp.content)) as pdf_zip:
                pdf_names = sorted(
                    [n for n in pdf_zip.namelist() if n.lower().endswith(".pdf")])
                if pdf_names:
                    destination.write_bytes(pdf_zip.read(pdf_names[0]))
                    return "PDF downloaded"
        except Exception as exc:
            return f"PDF failed: {exc}"
    return None


def select_pdf_entry(
    pdf_entries: list[str], midi_name: str, part_label: str | None
) -> str | None:
    if not pdf_entries:
        return None
    midi_stem = Path(midi_name).stem.lower()
    part_slug = slugify(part_label) if part_label else ""
    best_match = None

    for entry_name in pdf_entries:
        entry_stem = Path(entry_name).stem.lower()
        if midi_stem and (midi_stem in entry_stem or entry_stem in midi_stem):
            return entry_name
        if part_slug and part_slug in entry_stem:
            best_match = entry_name

    return best_match or pdf_entries[0]


def process_zip_listing(listing: dict, target_dir: Path, download_pdf: bool, log_fn) -> list[str]:
    statuses: list[str] = []
    slug = slugify(listing["title"])

    resp = requests.get(listing["midi_zip_url"],
                        timeout=30, headers=REQUEST_HEADERS)
    resp.raise_for_status()
    midi_zip = zipfile.ZipFile(io.BytesIO(resp.content))
    midi_entries = sorted([n for n in midi_zip.namelist()
                          if n.lower().endswith(".mid")])

    pdf_zip = None
    pdf_entries: list[str] = []
    pdf_single_data = None

    if download_pdf:
        if listing.get("pdf_zip_url"):
            try:
                pdf_resp = requests.get(
                    listing["pdf_zip_url"], timeout=30, headers=REQUEST_HEADERS)
                pdf_resp.raise_for_status()
                pdf_zip = zipfile.ZipFile(io.BytesIO(pdf_resp.content))
                pdf_entries = sorted(
                    [n for n in pdf_zip.namelist() if n.lower().endswith(".pdf")])
            except Exception as exc:
                statuses.append(f"PDF zip failed: {exc}")
        elif listing.get("pdf_url"):
            try:
                pdf_single_data = requests.get(
                    listing["pdf_url"], timeout=30, headers=REQUEST_HEADERS).content
            except Exception as exc:
                statuses.append(f"PDF failed: {exc}")

    parts = parse_instrument_parts(
        listing.get("instrumentation") or listing.get("title") or ""
    )

    for idx, midi_name in enumerate(midi_entries):
        part_label = parts[idx] if idx < len(parts) else Path(midi_name).stem
        safe_label = slugify(part_label) or f"part-{idx + 1}"
        midi_path = target_dir / f"{slug}-{safe_label}.mid"

        if midi_path.exists():
            statuses.append(f"{part_label}: MIDI exists")
        else:
            midi_path.write_bytes(midi_zip.read(midi_name))
            statuses.append(f"{part_label}: MIDI downloaded")

        if download_pdf:
            pdf_dest = target_dir / f"{slug}-{safe_label}-a4.pdf"
            if not pdf_dest.exists():
                pdf_bytes = None
                if pdf_zip and pdf_entries:
                    entry_name = select_pdf_entry(
                        pdf_entries, midi_name, part_label)
                    if entry_name:
                        pdf_bytes = pdf_zip.read(entry_name)
                elif pdf_single_data and idx == 0:
                    pdf_bytes = pdf_single_data
                if pdf_bytes:
                    pdf_dest.write_bytes(pdf_bytes)
                    statuses.append(f"{part_label}: PDF downloaded")
                else:
                    statuses.append(f"{part_label}: PDF unavailable")
            else:
                statuses.append(f"{part_label}: PDF exists")

        meta_status = update_metadata_for_path(
            midi_path,
            listing,
            part_label=part_label,
            default_instrument=guess_instrument_from_text(part_label),
        )
        statuses.append(f"{part_label}: {meta_status}")

    return statuses


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
            meta_data = ensure_meta(child)
            meta_context = build_meta_context(child, meta_data, str(rel))
            entry["meta"] = meta_context
            entry["display_name"] = meta_context["display_name"]
            entry["instrument_icon"] = meta_context["instrument_icon"]
            entry["instrument_id"] = meta_context["form_defaults"].get(
                "instrument", DEFAULT_INSTRUMENT_ID)
            sheet_path = locate_sheet_path(child)
            entry["sheet_rel_path"] = str(
                sheet_path.relative_to(BASE_DIR)) if sheet_path else None
        else:
            entry["meta"] = None
            entry["sheet_rel_path"] = None
            entry["instrument_icon"] = INSTRUMENT_LOOKUP[DEFAULT_INSTRUMENT_ID]["emoji"]

        entries.append(entry)

    parent_rel = None
    if full_path != BASE_DIR:
        parent_rel = str(full_path.parent.relative_to(BASE_DIR))

    return render_template(
        "browse.html",
        entries=entries,
        subpath=subpath,
        parent_rel=parent_rel,
        admin_mode=ADMIN_MODE,
        instrument_presets=INSTRUMENT_PRESETS,
        openai_enabled=AI_ENABLED,
        collection_title=derive_collection_title(subpath),
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
        sanitized_slug = sanitize_slug(
            new_slug) if new_slug else midi_path.stem
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

    instrument_choice = normalize_instrument_choice(metadata.get("instrument"))

    bpm_value = metadata.get("bpm") or metadata.get("tempo")
    if isinstance(bpm_value, str):
        bpm_value = bpm_value.strip()
        bpm_value = bpm_value or None
    if bpm_value is not None:
        try:
            bpm_value = float(bpm_value)
        except ValueError:
            bpm_value = None

    meta_payload = {
        "name": metadata.get("name", ""),
        "composer": metadata.get("composer", ""),
        "editor": metadata.get("editor", ""),
        "modified_by": metadata.get("modified_by", ""),
        "source": metadata.get("source", ""),
        "license": metadata.get("license") or "Public Domain",
        "instrument": instrument_choice,
    }
    if bpm_value:
        meta_payload["bpm"] = bpm_value
    genre_value = metadata.get("genre")
    if isinstance(genre_value, str):
        genre_value = genre_value.strip()
    if genre_value:
        meta_payload["genre"] = genre_value

    tags_value = metadata.get("tags")
    tags_list: list[str] = []
    if isinstance(tags_value, str):
        tags_list = [tag.strip()
                     for tag in tags_value.split(",") if tag.strip()]
    elif isinstance(tags_value, (list, tuple)):
        tags_list = [str(tag).strip()
                     for tag in tags_value if str(tag).strip()]
    if tags_list:
        meta_payload["tags"] = tags_list

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


@app.post("/api/entry/ai-suggest")
def ai_suggest_metadata():
    if not ADMIN_MODE:
        abort(403)
    if not AI_ENABLED or not openai_client:
        return jsonify({"error": "AI assistance is not configured."}), 400

    payload = request.get_json(silent=True) or {}
    rel_path = payload.get("rel_path")
    if not rel_path:
        return jsonify({"error": "Missing rel_path"}), 400

    midi_path = get_safe_path(rel_path)
    if not is_midi_file(midi_path):
        abort(404)

    working_meta = ensure_meta(midi_path)
    incoming_meta = payload.get("metadata") or {}
    for key, value in incoming_meta.items():
        if value in (None, ""):
            continue
        working_meta[key] = value

    context_blob = {
        "file_name": midi_path.name,
        "folder": midi_path.parent.name,
        "metadata": working_meta,
    }
    prompt = (
        "You are an assistant that cleans up classical music metadata. "
        "Given the JSON payload below, return improved values as JSON with the keys "
        "`name` (title string), `composer` (string), `bpm` (number), "
        "`genre` (string), and `tags` (array of 3-6 short descriptive strings). "
        "Only respond with valid JSON and do not include prose. "
        "If the metadata already includes a composer, keep that value unless you are certain it should change.\n\n"
        f"Input:\n{json.dumps(context_blob, ensure_ascii=False, indent=2)}"
    )

    try:
        completion = openai_client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": "You refine music metadata for a MIDI library.",
                },
                {"role": "user", "content": prompt},
            ],
        )
        ai_text = completion.choices[0].message.content.strip()
        suggestion = json.loads(ai_text)
    except Exception as exc:
        app.logger.exception("AI suggestion failed")
        return jsonify({"error": f"AI request failed: {exc}"}), 500

    bpm_value = suggestion.get("bpm")
    try:
        bpm_value = float(bpm_value) if bpm_value is not None else None
    except (TypeError, ValueError):
        bpm_value = None

    tags_value = suggestion.get("tags")
    if isinstance(tags_value, str):
        tags_list = [tag.strip()
                     for tag in tags_value.split(",") if tag.strip()]
    elif isinstance(tags_value, (list, tuple)):
        tags_list = [str(tag).strip()
                     for tag in tags_value if str(tag).strip()]
    else:
        tags_list = []

    result = {
        "name": suggestion.get("name", ""),
        "composer": suggestion.get("composer") or working_meta.get("composer", ""),
        "bpm": bpm_value,
        "genre": suggestion.get("genre", ""),
        "tags": tags_list,
    }

    return jsonify({"success": True, "suggestion": result})


@app.route("/admin")
def admin_home():
    if not ADMIN_MODE:
        return redirect(url_for("browse"))

    return render_template("admin_index.html", admin_mode=ADMIN_MODE)


@app.route("/admin/scrubber", methods=["GET", "POST"])
def admin_scrubber():
    if not ADMIN_MODE:
        return redirect(url_for("browse"))

    summary = []
    status_message = None
    term = ""
    download_pdf = True
    test_mode = False
    source = SCRUB_SOURCES[0]["id"]
    log_lines: list[str] = []

    def log_fn(message: str):
        print(f"[Magic Midi Finder {message}")
        log_lines.append(message)

    if request.method == "POST":
        term = (request.form.get("term") or "").strip()
        download_pdf = request.form.get("download_pdf") == "on"
        test_mode = request.form.get("test_mode") == "on"
        source = request.form.get("source") or source
        if not term:
            status_message = "Please enter a search term."
        else:
            try:
                summary = scrobble_mutopia(
                    term,
                    download_pdf=download_pdf,
                    mode="test" if test_mode else "full",
                    log=log_fn,
                    source=source,
                )
                added_count = sum(
                    1 for item in summary if "downloaded" in item.get("status", "").lower()
                )
                status_message = (
                    f"Processed {len(summary)} listings. Added or updated {added_count} entries."
                )
            except Exception as exc:
                status_message = f"Error while scraping: {exc}"
                log_fn(status_message)

    return render_template(
        "admin_scrubber.html",
        admin_mode=ADMIN_MODE,
        summary=summary,
        status=status_message,
        term=term,
        download_pdf=download_pdf,
        test_mode=test_mode,
        sources=SCRUB_SOURCES,
        source=source,
        logs=log_lines,
    )


if __name__ == "__main__":
    # For local (non-Docker) testing
    app.run(host="0.0.0.0", port=5000, debug=True)
