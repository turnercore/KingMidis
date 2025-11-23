from flask import Flask, render_template, send_from_directory, abort, request, jsonify, redirect, url_for, session
import hmac
from pathlib import Path
from collections.abc import Callable
from openai import OpenAI
import threading
import os
import io
import zipfile
import re
import json
import yaml
import requests
from urllib.parse import urljoin
from bs4 import BeautifulSoup
import secrets

app = Flask(__name__)

# Base directory for MIDI files inside the container
BASE_DIR = Path(os.environ.get("MIDI_ROOT", "/midis")).resolve()
ADMIN_MODE = os.environ.get("ADMIN_MODE", "").lower() in ("1", "true", "yes")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD")
SECRET_KEY = os.environ.get("SECRET_KEY")
if not SECRET_KEY:
    SECRET_KEY = ADMIN_PASSWORD or secrets.token_hex(32)
app.secret_key = SECRET_KEY
ADMIN_UNLOCK_TOKEN = (
    hmac.new(
        SECRET_KEY.encode("utf-8"),
        (ADMIN_PASSWORD or "").encode("utf-8"),
        "sha256",
    ).hexdigest()
    if ADMIN_PASSWORD
    else None
)

META_SUFFIXES = [".meta", ".yml", ".yaml"]
DEFAULT_META_SUFFIX = ".yml"
SHEET_SUFFIX = ".pdf"
ATTACHMENT_FIELDS = ["attachments"]
AI_FLAG_FIELD = "ai_scraped"
MUTOPIA_SEARCH_ENDPOINT = "https://www.mutopiaproject.org/cgibin/make-table.cgi"
SCRUB_SOURCES = [
    {"id": "mutopia", "label": "Mutopia Project"},
]
SCRUB_CONCURRENCY = max(1, int(os.environ.get("SCRUB_THREADS", "3")))
AI_CONCURRENCY = max(1, int(os.environ.get("AI_THREADS", "2")))
REQUEST_HEADERS = {"User-Agent": "KingMidis/1.0"}
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5-nano")
openai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
AI_ENABLED = openai_client is not None
mass_ai_state = {
    "lock": threading.Lock(),
    "jobs": [],
    "folder": None,
    "log": [],
    "cancel": None,
}
def mass_ai_log_append(message: str):
    with mass_ai_state["lock"]:
        mass_ai_state["log"].append(message)
        if len(mass_ai_state["log"]) > 500:
            mass_ai_state["log"] = mass_ai_state["log"][-500:]
scrub_state = {
    "lock": threading.Lock(),
    "jobs": [],
    "log": [],
    "summary": [],
    "error": None,
}


def has_admin_access() -> bool:
    return (
        ADMIN_MODE
        and ADMIN_UNLOCK_TOKEN is not None
        and session.get("admin_token") == ADMIN_UNLOCK_TOKEN
    )


def require_admin_access():
    if not has_admin_access():
        abort(403)


def human_readable_bytes(num: int) -> str:
    step = 1024.0
    for unit in ["bytes", "KB", "MB", "GB", "TB"]:
        if num < step:
            if unit == "bytes":
                return f"{num} bytes"
            return f"{num:.1f} {unit}"
        num /= step
    return f"{num:.1f} PB"


INSTRUMENT_PRESETS = [
    {"id": "piano", "label": "Grand Piano", "emoji": "🎹"},
    {"id": "digital", "label": "Digital Synth", "emoji": "🎛️"},
    {"id": "strings", "label": "Strings", "emoji": "🎻"},
    {"id": "guitar", "label": "Guitar", "emoji": "🎸"},
    {"id": "harp", "label": "Harp", "emoji": "🪕"},
    {"id": "accordion", "label": "Accordion", "emoji": "🪗"},
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
    (["accordion"], "accordion"),
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
WORK_ID_FIELD = "work_id"
PART_LABEL_FIELD = "part_label"
DEFAULT_PART_LABEL = "main"
MAX_PART_SUFFIX_TOKENS = 5
PART_PREFIXES = {
    "score",
    "solo",
    "string",
    "strings",
    "violin",
    "violin1",
    "violin2",
    "violino",
    "violone",
    "viola",
    "cello",
    "cellos",
    "violoncello",
    "continuo",
    "basso",
    "bass",
    "contrabass",
    "doublebass",
    "harpsichord",
    "clavier",
    "clavichord",
    "keyboard",
    "organ",
    "piano",
    "guitar",
    "harp",
    "lute",
    "flute",
    "oboe",
    "clarinet",
    "trumpet",
    "trombone",
    "horn",
    "sax",
    "saxophone",
    "piccolo",
    "bassoon",
    "fagotto",
    "choir",
    "chorus",
    "chorale",
    "voice",
    "soprano",
    "alto",
    "tenor",
    "baritone",
    "percussion",
    "drum",
    "drums",
    "part",
    "parts",
}
SHARED_WORK_FIELDS = {
    "name",
    "composer",
    "license",
    "source",
    "contributor",
    "bpm",
    "genre",
    "tags",
    "attachments",
    AI_FLAG_FIELD,
}


def token_is_part_indicator(token: str) -> bool:
    clean = (token or "").strip().lower()
    if not clean:
        return False
    if clean in PART_PREFIXES:
        return True
    for prefix in PART_PREFIXES:
        if prefix in {"part", "parts"}:
            continue
        if clean.startswith(prefix):
            return True
    if clean.startswith("part") and any(ch.isdigit() for ch in clean):
        return True
    return False


def gather_sibling_stems(midi_path: Path) -> list[str]:
    folder = midi_path.parent
    stems: set[str] = set()
    for suffix in MIDI_EXTENSIONS:
        pattern = f"*{suffix}"
        for candidate in folder.glob(pattern):
            if candidate.is_file():
                stems.add(candidate.stem)
    return sorted(stems)


def guess_work_identity(midi_path: Path, sibling_stems: list[str] | None = None) -> tuple[str, str | None]:
    stem = midi_path.stem
    tokens = [token for token in stem.split("-") if token]
    if len(tokens) <= 1:
        return stem, None

    siblings = sibling_stems if sibling_stems is not None else gather_sibling_stems(midi_path)
    max_suffix = min(MAX_PART_SUFFIX_TOKENS, len(tokens) - 1)

    for suffix_len in range(max_suffix, 0, -1):
        prefix_tokens = tokens[:-suffix_len]
        suffix_tokens = tokens[-suffix_len:]
        if not prefix_tokens or not suffix_tokens:
            continue
        first_token = suffix_tokens[0]
        if not token_is_part_indicator(first_token):
            continue
        prefix = "-".join(prefix_tokens)
        has_partner = any(
            other != stem and (other == prefix or other.startswith(prefix + "-"))
            for other in siblings
        )
        if not has_partner:
            continue
        return prefix, "-".join(suffix_tokens)

    return stem, None


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


def ensure_work_identity_fields(midi_path: Path, meta: dict | None) -> tuple[dict, bool]:
    meta = dict(meta or {})
    siblings = gather_sibling_stems(midi_path)
    guessed_work, guessed_part = guess_work_identity(midi_path, siblings)

    work_value = slugify(meta.get(WORK_ID_FIELD) or guessed_work or midi_path.stem)
    if not work_value:
        work_value = slugify(midi_path.stem)

    part_value = slugify(meta.get(PART_LABEL_FIELD) or guessed_part or DEFAULT_PART_LABEL)
    if not part_value:
        part_value = DEFAULT_PART_LABEL

    changed = False
    if meta.get(WORK_ID_FIELD) != work_value:
        meta[WORK_ID_FIELD] = work_value
        changed = True
    if meta.get(PART_LABEL_FIELD) != part_value:
        meta[PART_LABEL_FIELD] = part_value
        changed = True

    return meta, changed


def ensure_meta(midi_path: Path) -> dict:
    meta = load_meta(midi_path)
    if meta is not None:
        meta, changed = ensure_work_identity_fields(midi_path, meta)
        if changed:
            meta_path = meta_path_for_write(midi_path)
            meta_path.write_text(yaml.safe_dump(
                meta, allow_unicode=True, sort_keys=False))
        return meta

    default_meta = {
        "name": midi_path.stem,
        "composer": midi_path.parent.name,
        "license": "Public Domain",
        "instrument": DEFAULT_INSTRUMENT_ID,
    }
    default_meta, _ = ensure_work_identity_fields(midi_path, default_meta)
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


def normalize_attachment_list(value) -> list[str]:
    if value in (None, "", False):
        return []
    if isinstance(value, str):
        parts = re.split(r"[,\n]+", value)
    elif isinstance(value, (list, tuple, set)):
        parts = value
    else:
        return []
    normalized = []
    for item in parts:
        text = str(item).strip()
        if text and text not in normalized:
            normalized.append(text)
    return normalized


def discover_pdf_attachments(midi_path: Path) -> list[str]:
    results: list[str] = []
    pattern = f"{midi_path.stem}*.pdf"
    for candidate in midi_path.parent.glob(pattern):
        if candidate.is_file():
            results.append(candidate.name)
    return results


def merge_attachment_lists(lists: list[list[str]]) -> list[str]:
    seen = set()
    merged: list[str] = []
    for lst in lists:
        for item in lst:
            if item and item not in seen:
                merged.append(item)
                seen.add(item)
    return merged


def collect_attachment_candidates(midi_path: Path, meta_data: dict | None) -> list[str]:
    manual = normalize_attachment_list(
        (meta_data or {}).get("attachments") if meta_data else None
    )
    auto = discover_pdf_attachments(midi_path)
    return merge_attachment_lists([manual, auto])


def ai_flag_value(meta_data: dict | None) -> bool:
    value = (meta_data or {}).get(AI_FLAG_FIELD)
    if isinstance(value, str):
        value = value.strip().lower()
        if value in ("1", "true", "yes", "on"):
            return True
        if value in ("0", "false", "no", "off"):
            return False
    return bool(value)


def derive_collection_title(subpath: str) -> str:
    if not subpath:
        return "King Midis"
    root = subpath.strip("/").split("/", 1)[0]
    clean = re.sub(r"[-_]+", " ", root).strip()
    if clean:
        return clean.title()
    return "King Midis"


def build_meta_context(
    midi_path: Path,
    meta_data: dict | None,
    rel_path: str,
    work_groups: dict[str, list[Path]] | None = None,
) -> dict:
    meta_data = meta_data or {}
    parent_name = midi_path.parent.name if midi_path.parent != midi_path else ""
    display_name = meta_data.get("name") or midi_path.stem

    composer = meta_data.get("composer") or parent_name or midi_path.stem
    contributor = meta_data.get("contributor") or meta_data.get("editor")
    source = meta_data.get("source")
    source_url = meta_data.get("source_url")
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

    attachments = collect_attachment_candidates(midi_path, meta_data)
    work_id_value = meta_data.get(WORK_ID_FIELD) or midi_path.stem
    part_label_value = meta_data.get(PART_LABEL_FIELD) or DEFAULT_PART_LABEL
    linked_midis = list_linked_midis(midi_path, work_id_value, work_groups)
    linked_count = len(linked_midis)
    part_display = format_part_label(part_label_value)

    lines = [f"{display_name} by {composer}"]
    if contributor:
        lines.append(f"Contributed by {contributor}")
    if source:
        lines.append(f"Source: {source}")
    lines.append(f"Licensed under {license_value}")

    form_defaults = {
        "name": meta_data.get("name", "") or display_name,
        "composer": meta_data.get("composer", "") or composer,
        "contributor": contributor or "",
        "source": meta_data.get("source", ""),
        "source_url": source_url or "",
        "license": meta_data.get("license") or meta_data.get("liscense") or "Public Domain",
        "instrument": instrument_choice,
        "bpm": meta_data.get("bpm") or meta_data.get("tempo") or "",
        "attachments": attachments,
        "genre": genre_value,
        "tags": ", ".join(tags_list),
        "ai_scraped": "1" if ai_flag_value(meta_data) else "",
        "work_id": work_id_value,
        "part_label": part_label_value,
        "part_display": part_display,
        "linked_part_count": linked_count,
    }

    preset = INSTRUMENT_LOOKUP.get(
        instrument_choice) or INSTRUMENT_LOOKUP[DEFAULT_INSTRUMENT_ID]

    return {
        "has_meta": bool(meta_data),
        "display_name": display_name,
        "composer": composer,
        "contributor": contributor,
        "source": source,
        "source_url": source_url,
        "instrument": instrument_choice,
        "license": license_value,
        "attribution_text": "\n".join(lines),
        "rel_path": rel_path,
        "form_defaults": form_defaults,
        "instrument_icon": preset["emoji"],
        "instrument_label": preset["label"],
        "genre": genre_value,
        "tags": tags_list,
        "attachments": attachments,
        "work_id": work_id_value,
        "part_label": part_label_value,
        "part_display": part_display,
        "linked_part_count": linked_count,
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


def list_linked_midis(
    midi_path: Path,
    work_id: str,
    work_groups: dict[str, list[Path]] | None = None,
) -> list[Path]:
    normalized = slugify(work_id or "")
    if not normalized:
        return []
    if work_groups is not None:
        return list(work_groups.get(normalized, []))
    folder = midi_path.parent
    linked: list[Path] = []
    for suffix in MIDI_EXTENSIONS:
        for candidate in folder.glob(f"*{suffix}"):
            if not candidate.is_file():
                continue
            meta = ensure_meta(candidate)
            candidate_work = slugify(meta.get(WORK_ID_FIELD) or "")
            if candidate_work == normalized:
                linked.append(candidate)
    return linked


def propagate_work_metadata(origin_path: Path, base_meta: dict) -> None:
    work_id = slugify(base_meta.get(WORK_ID_FIELD) or "")
    if not work_id:
        return
    shared_payload = {
        key: base_meta.get(key)
        for key in SHARED_WORK_FIELDS
        if key in base_meta
    }
    for sibling in list_linked_midis(origin_path, work_id):
        if sibling == origin_path:
            continue
        meta = ensure_meta(sibling)
        changed = False
        for key, value in shared_payload.items():
            if value in (None, "", [], {}):
                if key in meta:
                    meta.pop(key, None)
                    changed = True
                continue
            if meta.get(key) != value:
                meta[key] = value
                changed = True
        if changed:
            meta_path = meta_path_for_write(sibling)
            meta_path.write_text(yaml.safe_dump(meta, allow_unicode=True, sort_keys=False))


def format_part_label(part_label: str | None) -> str:
    label = (part_label or DEFAULT_PART_LABEL).replace("-", " ").strip()
    if not label:
        return "Full score"
    return label.title()


def rename_sidecars(original_path: Path, new_stem: str, attachments: list[str] | None = None) -> list[str]:
    for suffix in META_SUFFIXES + [SHEET_SUFFIX]:
        old_path = original_path.with_suffix(suffix)
        if old_path.exists() and old_path.is_file():
            old_path.rename(old_path.with_name(new_stem + suffix))

    updated_attachments: list[str] = []
    attachments = attachments or []
    parent = original_path.parent
    for rel_path in attachments:
        old_file = parent / rel_path
        if not old_file.exists() or not old_file.is_file():
            updated_attachments.append(rel_path)
            continue
        if rel_path.startswith(original_path.stem):
            new_name = new_stem + rel_path[len(original_path.stem):]
        else:
            new_name = rel_path
        if new_name != rel_path:
            old_file.rename(parent / new_name)
        updated_attachments.append(new_name)

    return updated_attachments


def locate_sheet_path(midi_path: Path, attachments: list[str] | None = None) -> Path | None:
    attachments = attachments or []
    for rel in attachments:
        candidate = midi_path.parent / rel
        if candidate.exists() and candidate.suffix.lower() == SHEET_SUFFIX:
            return candidate

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

    work_slug = slugify(listing.get("title") or midi_path.stem)
    if not work_slug:
        work_slug = midi_path.stem
    merged[WORK_ID_FIELD] = work_slug
    inferred_part = slugify(part_label) if part_label else DEFAULT_PART_LABEL
    if not inferred_part:
        inferred_part = DEFAULT_PART_LABEL
    merged[PART_LABEL_FIELD] = inferred_part

    updates = {
        "name": f"{listing['title']} - {part_label}" if part_label else listing["title"],
        "composer": listing.get("composer"),
        "license": listing.get("license"),
        "source": listing.get("source", "Mutopia Project"),
        "source_url": listing.get("source_url"),
        "instrument": default_instrument or listing.get("instrument_id"),
        "style": listing.get("style"),
        "period": listing.get("period"),
        "catalog": listing.get("catalog"),
        "instrumentation": listing.get("instrumentation"),
        "notes": listing.get("notes"),
        "contributor": listing.get("contributor") or listing.get("editor"),
    }

    changed = False
    for key, value in updates.items():
        if key in ("license", "contributor", "source", "source_url"):
            if value and merged.get(key) != value:
                merged[key] = value
                changed = True
            continue
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
    info_cell = cell(2, 2)
    info_link = info_cell.find("a") if info_cell else None
    source_url = None
    if info_link and info_link.get("href"):
        source_url = urljoin(MUTOPIA_SEARCH_ENDPOINT, info_link["href"])

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
    contributor_text = cell_text(2, 0)

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
        "contributor": contributor_text,
        "source_url": source_url,
    }


def scrobble_mutopia(
    search_term: str,
    download_pdf: bool = True,
    mode: str = "full",
    *,
    log: Callable[[str], None] | None = None,
    source: str = "mutopia",
    cancel_event: threading.Event | None = None,
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
        if cancel_event and cancel_event.is_set():
            log_fn("Cancelled.")
            break

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
            if cancel_event and cancel_event.is_set():
                log_fn("Cancelled.")
                break

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
                    log_fn(
                        "Entry unchanged during test run; searching for another example.")
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
    children = sorted(full_path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    work_groups: dict[str, list[Path]] = {}
    meta_cache: dict[Path, dict] = {}

    for child in children:
        if not is_midi_file(child):
            continue
        meta_data = ensure_meta(child)
        meta_cache[child] = meta_data
        work_key = slugify(meta_data.get(WORK_ID_FIELD) or child.stem)
        if work_key:
            work_groups.setdefault(work_key, []).append(child)

    for child in children:
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
            meta_data = meta_cache.get(child) or ensure_meta(child)
            meta_context = build_meta_context(child, meta_data, str(rel), work_groups)
            entry["meta"] = meta_context
            entry["display_name"] = meta_context["display_name"]
            entry["instrument_icon"] = meta_context["instrument_icon"]
            entry["instrument_id"] = meta_context["form_defaults"].get(
                "instrument", DEFAULT_INSTRUMENT_ID)
            attachments = meta_context.get("attachments") or []
            sheet_path = locate_sheet_path(child, attachments)
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
        admin_mode=has_admin_access(),
        admin_enabled=ADMIN_MODE and bool(ADMIN_PASSWORD),
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
    require_admin_access()

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

    attachments_value = normalize_attachment_list(metadata.get("attachments"))
    attachments_value = merge_attachment_lists(
        [attachments_value, discover_pdf_attachments(midi_path)]
    )

    updated_path = midi_path
    if sanitized_slug != midi_path.stem:
        target_path = midi_path.with_name(sanitized_slug + midi_path.suffix)
        if target_path.exists():
            return jsonify({"error": "A file with that name already exists"}), 400
        old_path = midi_path
        midi_path.rename(target_path)
        attachments_value = rename_sidecars(
            old_path, sanitized_slug, attachments_value
        )
        updated_path = target_path
    attachments_value = merge_attachment_lists(
        [attachments_value, discover_pdf_attachments(updated_path)]
    )

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

    guessed_work, guessed_part = guess_work_identity(updated_path)
    work_id_value = slugify(metadata.get(WORK_ID_FIELD) or guessed_work or sanitized_slug)
    if not work_id_value:
        work_id_value = sanitized_slug
    part_label_value = slugify(metadata.get(PART_LABEL_FIELD) or guessed_part or DEFAULT_PART_LABEL)
    if not part_label_value:
        part_label_value = DEFAULT_PART_LABEL

    meta_payload = {
        "name": metadata.get("name", ""),
        "composer": metadata.get("composer", ""),
        "contributor": metadata.get("contributor", ""),
        "source": metadata.get("source", ""),
        "license": metadata.get("license") or "Public Domain",
        "instrument": instrument_choice,
        WORK_ID_FIELD: work_id_value,
        PART_LABEL_FIELD: part_label_value,
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
    if attachments_value:
        meta_payload["attachments"] = attachments_value
    if metadata.get("ai_scraped"):
        meta_payload[AI_FLAG_FIELD] = True

    meta_path = meta_path_for_write(updated_path)
    meta_path.write_text(
        yaml.safe_dump(meta_payload, allow_unicode=True, sort_keys=False)
    )
    propagate_work_metadata(updated_path, meta_payload)

    return jsonify(
        {
            "success": True,
            "rel_path": str(updated_path.relative_to(BASE_DIR)),
        }
    )


@app.post("/api/entry/delete")
def delete_entry():
    require_admin_access()

    payload = request.get_json(silent=True) or {}
    rel_path = payload.get("rel_path")
    if not rel_path:
        return jsonify({"error": "Missing rel_path"}), 400

    midi_path = get_safe_path(rel_path)
    if not is_midi_file(midi_path):
        abort(404)

    attachments = payload.get("attachments") or []
    attachments = normalize_attachment_list(attachments)

    parent = midi_path.parent

    files_to_remove = [midi_path]
    for suffix in META_SUFFIXES + [SHEET_SUFFIX]:
        candidate = midi_path.with_suffix(suffix)
        if candidate.exists():
            files_to_remove.append(candidate)

    for rel in attachments:
        candidate = parent / rel
        if candidate.exists() and candidate.is_file():
            files_to_remove.append(candidate)

    for target in files_to_remove:
        try:
            target.unlink(missing_ok=True)
        except Exception as exc:
            return jsonify({"error": f"Failed to delete {target.name}: {exc}"}), 500

    return jsonify({"success": True})


@app.post("/api/entry/ai-suggest")
def ai_suggest_metadata():
    require_admin_access()
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

    suggestion, error = request_ai_suggestion(context_blob)
    if error:
        return jsonify({"error": error}), 500

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
        "bpm": bpm_value if (bpm_value and bpm_value > 0) else None,
        "genre": suggestion.get("genre", ""),
        "tags": tags_list,
        "ai_scraped": True,
    }

    return jsonify({"success": True, "suggestion": result})


@app.get("/api/entry/meta")
def fetch_entry_meta():
    require_admin_access()
    rel_path = request.args.get("rel_path")
    if not rel_path:
        return jsonify({"error": "Missing rel_path"}), 400
    midi_path = get_safe_path(rel_path)
    if not is_midi_file(midi_path):
        abort(404)
    meta_data = load_meta(midi_path)
    context = build_meta_context(midi_path, meta_data, rel_path)
    return jsonify({"success": True, "meta": context["form_defaults"]})


@app.post("/admin/unlock")
def admin_unlock():
    if not ADMIN_MODE:
        abort(404)
    if not ADMIN_PASSWORD or ADMIN_UNLOCK_TOKEN is None:
        return jsonify({"error": "Admin password is not configured."}), 400
    payload = request.get_json(silent=True) or {}
    password = payload.get("password", "")
    if not password:
        return jsonify({"error": "Password required."}), 400
    if not hmac.compare_digest(password, ADMIN_PASSWORD):
        return jsonify({"error": "Invalid password."}), 403
    session["admin_token"] = ADMIN_UNLOCK_TOKEN
    return jsonify({"success": True})


@app.post("/admin/lock")
def admin_lock():
    if not ADMIN_MODE:
        abort(404)
    session.pop("admin_token", None)
    return jsonify({"success": True})


@app.route("/admin")
def admin_home():
    if not has_admin_access():
        return redirect(url_for("browse"))

    stats = compute_library_stats()
    return render_template("admin_index.html", admin_mode=True, admin_enabled=ADMIN_MODE, stats=stats)


def list_top_level_folders():
    folders = []
    for child in sorted(BASE_DIR.iterdir(), key=lambda p: p.name.lower()):
        if child.is_dir():
            folders.append(child)
    return folders


def iter_midi_files(folder: Path):
    for path in sorted(folder.glob("*.mid")):
        if path.is_file():
            yield path
    for path in sorted(folder.glob("*.midi")):
        if path.is_file():
            yield path


def mass_ai_process_folder(folder: Path, log_lines: list[str], cancel_event=None) -> int:
    processed = 0
    for midi in iter_midi_files(folder):
        if cancel_event and cancel_event.is_set():
            log_lines.append("Mass AI cancelled.")
            print("[MassAI] Cancelled run.")
            break
        meta = ensure_meta(midi)
        if ai_flag_value(meta):
            log_lines.append(f"Skipping {midi.name}: already AI processed.")
            continue
        try:
            rel_path = str(midi.relative_to(BASE_DIR))
        except ValueError:
            rel_path = midi.name
        context_blob = {
            "file_name": midi.name,
            "folder": midi.parent.name,
            "rel_path": rel_path,
            "metadata": {
                "name": meta.get("name", ""),
                "composer": meta.get("composer", ""),
                "bpm": meta.get("bpm", ""),
                "genre": meta.get("genre", ""),
                "tags": meta.get("tags", []),
            },
        }
        message = f"Running AI for {midi.name}"
        print(f"[MassAI] {message}")
        log_lines.append(message)
        suggestion, error = request_ai_suggestion(context_blob)
        if error or not suggestion:
            log_lines.append(f"AI failed for {midi.name}: {error}")
            print(f"[MassAI] AI failed for {midi.name}: {error}")
            continue
        updated = dict(meta)
        if suggestion.get("name"):
            updated["name"] = suggestion["name"]
        if suggestion.get("composer"):
            updated["composer"] = suggestion["composer"]
        if suggestion.get("bpm"):
            updated["bpm"] = suggestion["bpm"]
        if suggestion.get("genre"):
            updated["genre"] = suggestion["genre"]
        if suggestion.get("tags"):
            updated["tags"] = suggestion["tags"]
        updated[AI_FLAG_FIELD] = True
        meta_path = meta_path_for_write(midi)
        meta_path.write_text(yaml.safe_dump(
            updated, allow_unicode=True, sort_keys=False))
        propagate_work_metadata(midi, updated)
        processed += 1
        log_lines.append(f"Updated {midi.name}")
        print(f"[MassAI] Updated {midi.name}")
    if cancel_event and cancel_event.is_set():
        log_lines.append(f"Stopped after updating {processed} files.")
    else:
        log_lines.append(
            f"Completed {folder.name}: {processed} files updated.")
    return processed


def cleanup_pdfs_in_folder(folder: Path) -> int:
    removed = 0
    bytes_saved = 0
    for pdf in folder.glob("*.pdf"):
        if not pdf.is_file():
            continue
        try:
            bytes_saved += pdf.stat().st_size
        except OSError:
            pass
        pdf.unlink(missing_ok=True)
        removed += 1
    for midi in iter_midi_files(folder):
        meta = load_meta(midi)
        if not meta:
            continue
        attachments = [
            item for item in normalize_attachment_list(meta.get("attachments"))
            if not item.lower().endswith(".pdf")
        ]
        meta["attachments"] = attachments
        meta_path = meta_path_for_write(midi)
        meta_path.write_text(yaml.safe_dump(
            meta, allow_unicode=True, sort_keys=False))
    return removed, bytes_saved


def collect_link_candidates(folder: Path) -> list[dict]:
    midi_files = list(iter_midi_files(folder))
    sibling_stems = [path.stem for path in midi_files]
    candidates: list[dict] = []

    for midi in midi_files:
        meta = ensure_meta(midi)
        current_work = slugify(meta.get(WORK_ID_FIELD) or midi.stem) or midi.stem
        current_part = slugify(meta.get(PART_LABEL_FIELD) or DEFAULT_PART_LABEL) or DEFAULT_PART_LABEL
        guess_work, guess_part = guess_work_identity(midi, sibling_stems)
        suggested_work = slugify(guess_work or current_work) or current_work
        suggested_part = slugify(guess_part or current_part) or current_part
        needs_review = (current_work != suggested_work) or (current_part != suggested_part)
        candidates.append(
            {
                "name": midi.name,
                "rel_path": str(midi.relative_to(BASE_DIR)),
                "work_id": current_work,
                "part_label": current_part,
                "suggested_work_id": suggested_work,
                "suggested_part_label": suggested_part,
                "initial_work_id": current_work or suggested_work,
                "initial_part_label": current_part or suggested_part,
                "needs_review": needs_review,
                "part_display": format_part_label(current_part or suggested_part),
            }
        )

    return candidates


def collect_composer_counts(folder: Path) -> list[dict]:
    counts: dict[str, int] = {}
    for midi in iter_midi_files(folder):
        meta = ensure_meta(midi)
        composer = meta.get("composer") or midi.parent.name
        counts[composer] = counts.get(composer, 0) + 1
    return [
        {"name": name, "count": count}
        for name, count in sorted(counts.items(), key=lambda item: item[0].lower())
    ]


def merge_composers_in_folder(folder: Path, sources: list[str], target: str) -> int:
    sources_set = {s for s in sources if s}
    if not sources_set:
        return 0
    updated_files = 0
    for midi in iter_midi_files(folder):
        meta = ensure_meta(midi)
        composer = meta.get("composer") or midi.parent.name
        if composer not in sources_set:
            continue
        meta["composer"] = target
        meta_path = meta_path_for_write(midi)
        meta_path.write_text(yaml.safe_dump(
            meta, allow_unicode=True, sort_keys=False))
        updated_files += 1
    return updated_files


def reset_ai_flags(folder: Path | None) -> int:
    count = 0
    targets = []
    if folder:
        targets.append(folder)
    else:
        targets = [child for child in BASE_DIR.iterdir() if child.is_dir()]

    for target in targets:
        for midi in iter_midi_files(target):
            meta = ensure_meta(midi)
            if AI_FLAG_FIELD in meta:
                meta.pop(AI_FLAG_FIELD, None)
                meta_path = meta_path_for_write(midi)
                meta_path.write_text(yaml.safe_dump(meta, allow_unicode=True, sort_keys=False))
                count += 1
    return count


def folder_pdf_stats(folder: Path) -> dict:
    count = 0
    total = 0
    for pdf in folder.glob("*.pdf"):
        if not pdf.is_file():
            continue
        count += 1
        try:
            total += pdf.stat().st_size
        except OSError:
            continue
    return {"pdf_count": count, "pdf_size": total, "pdf_size_h": human_readable_bytes(total)}


def compute_library_stats() -> dict:
    composers = set()
    midi_count = 0
    sheet_count = 0
    total_size = 0
    for path in BASE_DIR.rglob("*"):
        if not path.is_file():
            continue
        try:
            total_size += path.stat().st_size
        except OSError:
            pass
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            sheet_count += 1
        elif suffix in (".mid", ".midi"):
            midi_count += 1
            meta = load_meta(path) or {}
            composer = meta.get("composer") or path.parent.name
            composers.add(composer)
    return {
        "composers": len(composers),
        "midis": midi_count,
        "sheets": sheet_count,
        "size": human_readable_bytes(total_size),
    }


def request_ai_suggestion(context_blob: dict):
    if not AI_ENABLED or not openai_client:
        return None, "AI assistance is not configured."

    # Compact JSON to reduce tokens
    input_json = json.dumps(
        context_blob,
        ensure_ascii=False,
        separators=(",", ":"),
    )

    try:
        completion = openai_client.chat.completions.create(
            model=OPENAI_MODEL,  # e.g. "gpt-5-nano"
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You clean up classical music metadata for a MIDI library. "
                        "Given some input metadata as JSON, respond ONLY with JSON having:\n"
                        " - name: title string\n"
                        " - composer: string\n"
                        " - bpm: number (or null if unknown)\n"
                        " - genre: short string\n"
                        " - tags: array of 3–6 short descriptive strings\n"
                        "Keep the existing composer unless you are certain it should change."
                    ),
                },
                {
                    "role": "user",
                    "content": input_json,
                },
            ],
        )

        ai_text = completion.choices[0].message.content.strip()
        suggestion = json.loads(ai_text)
        return suggestion, None

    except Exception as exc:
        app.logger.exception("AI suggestion failed")
        return None, str(exc)


def start_mass_ai_thread(folder: Path):
    with mass_ai_state["lock"]:
        if len(mass_ai_state["jobs"]) >= AI_CONCURRENCY:
            return False, "Maximum concurrent AI jobs running."
        cancel_event = threading.Event()
        log = [f"Starting AI for {folder.name}"]
        mass_ai_state["jobs"].append(
            {"folder": folder.name, "cancel": cancel_event, "log": mass_ai_state["log"]}
        )
        mass_ai_state["log"].append(f"Starting AI for {folder.name}")

    job_entry = mass_ai_state["jobs"][-1]
    def worker():
        try:
            mass_ai_process_folder(
                folder, job_entry["log"], cancel_event)
        except Exception as exc:
            job_entry["log"].append(f"Error: {exc}")
        finally:
            cancel_event.set()
            with mass_ai_state["lock"]:
                mass_ai_state["jobs"] = [
                    item for item in mass_ai_state["jobs"] if item is not job_entry
                ]
                mass_ai_state["log"].append(f"Finished AI for {folder.name}")

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return True, None


def scrub_log_append(message: str):
    print(f"[Magic Midi Finder] {message}")
    with scrub_state["lock"]:
        scrub_state["log"].append(message)
        # keep memory bounded
        if len(scrub_state["log"]) > 500:
            scrub_state["log"] = scrub_state["log"][-500:]


def start_scrub_thread(term: str, download_pdf: bool, test_mode: bool, source: str):
    with scrub_state["lock"]:
        if len(scrub_state["jobs"]) >= SCRUB_CONCURRENCY:
            return False, "Maximum concurrent scrubs running."
        cancel_event = threading.Event()
        scrub_state["jobs"].append({"term": term, "cancel": cancel_event})
        scrub_state["log"].append(f"Starting search for '{term}'")
        scrub_state["error"] = None

    def log_fn(message: str):
        scrub_log_append(message)

    def worker():
        try:
            summary = scrobble_mutopia(
                term,
                download_pdf=download_pdf,
                mode="test" if test_mode else "full",
                log=log_fn,
                source=source,
                cancel_event=cancel_event,
            )
            with scrub_state["lock"]:
                scrub_state["summary"].append({"term": term, "result": summary})
                scrub_state["jobs"] = [
                    job for job in scrub_state["jobs"] if job["cancel"] is not cancel_event
                ]
        except Exception as exc:
            scrub_log_append(f"Error: {exc}")
            with scrub_state["lock"]:
                scrub_state["error"] = str(exc)
                scrub_state["jobs"] = [
                    job for job in scrub_state["jobs"] if job["cancel"] is not cancel_event
                ]
            return
        finally:
            scrub_log_append(f"Finished job for '{term}'")

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return True, None


def get_scrub_status():
    with scrub_state["lock"]:
        return {
            "running": len(scrub_state["jobs"]) > 0,
            "log": list(scrub_state["log"]),
            "summary": list(scrub_state["summary"]),
            "error": scrub_state["error"],
            "can_cancel": bool(scrub_state["jobs"]),
            "active_jobs": [job["term"] for job in scrub_state["jobs"]],
        }


def cancel_mass_ai_thread():
    with mass_ai_state["lock"]:
        if not mass_ai_state["jobs"]:
            return False, "No job running."
        mass_ai_state["log"].append("Cancelling all AI jobs…")
        for job in mass_ai_state["jobs"]:
            job["cancel"].set()
        mass_ai_state["jobs"] = []
        return True, None


def get_mass_ai_status():
    with mass_ai_state["lock"]:
        running = len(mass_ai_state["jobs"]) > 0
        current_folder = mass_ai_state["jobs"][0]["folder"] if running else None
        return {
            "running": running,
            "folder": current_folder,
            "folders": [job["folder"] for job in mass_ai_state["jobs"]],
            "log": list(mass_ai_state["log"]),
        }


@app.route("/admin/mass-ai", methods=["GET", "POST"])
def admin_mass_ai():
    if not has_admin_access() or not AI_ENABLED:
        return redirect(url_for("browse"))

    folders = [{"name": f.name} for f in list_top_level_folders()]
    status = get_mass_ai_status()

    return render_template(
        "admin_mass_ai.html",
        admin_mode=True,
        admin_enabled=ADMIN_MODE,
        folders=folders,
        status=status,
    )


@app.route("/admin/pdf-cleanup", methods=["GET", "POST"])
def admin_pdf_cleanup():
    if not has_admin_access():
        return redirect(url_for("browse"))

    status_message = None
    folders = list_top_level_folders()
    selected = folders[0].name if folders else None
    folder_stats = [{"name": f.name, **folder_pdf_stats(f)} for f in folders]

    if request.method == "POST":
        folder_name = request.form.get("folder") or selected
        selected = folder_name
        target_folder = BASE_DIR / folder_name
        if not folder_name or not target_folder.exists() or not target_folder.is_dir():
            status_message = "Invalid folder."
        else:
            try:
                removed, bytes_saved = cleanup_pdfs_in_folder(target_folder)
                status_message = f"Removed {removed} PDFs in {folder_name} ({human_readable_bytes(bytes_saved)} freed)."
            except Exception as exc:
                status_message = f"Error: {exc}"
        folder_stats = [
            {"name": f.name, **folder_pdf_stats(f)} for f in folders]

    return render_template(
        "admin_pdf_cleanup.html",
        admin_mode=True,
        admin_enabled=ADMIN_MODE,
        folders=folder_stats,
        selected=selected,
        status=status_message,
    )


@app.route("/admin/link-parts", methods=["GET", "POST"])
def admin_link_parts():
    if not has_admin_access():
        return redirect(url_for("browse"))

    folders = list_top_level_folders()
    selected = (
        request.args.get("folder")
        or request.form.get("folder")
        or (folders[0].name if folders else None)
    )
    status_message = None
    entries: list[dict] = []

    if selected:
        target = BASE_DIR / selected
        if target.exists() and target.is_dir():
            entries = collect_link_candidates(target)
        else:
            status_message = "Invalid folder."
            entries = []

    if request.method == "POST" and selected and entries:
        rel_paths = request.form.getlist("rel_path")
        work_ids = request.form.getlist("work_id")
        part_labels = request.form.getlist("part_label")
        updated = 0

        for rel_path, work_value, part_value in zip(rel_paths, work_ids, part_labels):
            if not rel_path:
                continue
            try:
                midi_path = get_safe_path(rel_path)
            except Exception:
                continue
            if not is_midi_file(midi_path):
                continue
            meta = load_meta(midi_path) or {}
            normalized_work = slugify(work_value or meta.get(WORK_ID_FIELD) or midi_path.stem) or midi_path.stem
            normalized_part = slugify(part_value or meta.get(PART_LABEL_FIELD) or DEFAULT_PART_LABEL) or DEFAULT_PART_LABEL
            if meta.get(WORK_ID_FIELD) == normalized_work and meta.get(PART_LABEL_FIELD) == normalized_part:
                continue
            meta[WORK_ID_FIELD] = normalized_work
            meta[PART_LABEL_FIELD] = normalized_part
            meta_path = meta_path_for_write(midi_path)
            meta_path.write_text(
                yaml.safe_dump(meta, allow_unicode=True, sort_keys=False)
            )
            updated += 1

        status_message = f"Linked {updated} files." if updated else "No changes applied."
        if selected:
            target = BASE_DIR / selected
            if target.exists() and target.is_dir():
                entries = collect_link_candidates(target)

    return render_template(
        "admin_link_parts.html",
        admin_mode=True,
        admin_enabled=ADMIN_MODE,
        folders=[{"name": folder.name} for folder in folders],
        selected=selected,
        entries=entries,
        status=status_message,
    )


@app.post("/admin/mass-ai/start")
def admin_mass_ai_start():
    if not has_admin_access() or not AI_ENABLED:
        abort(403)
    payload = request.get_json(silent=True) or {}
    folder_name = payload.get("folder", "")
    folder_path = BASE_DIR / folder_name
    if not folder_name or not folder_path.exists() or not folder_path.is_dir():
        return jsonify({"error": "Invalid folder."}), 400
    success, error = start_mass_ai_thread(folder_path)
    if not success:
        return jsonify({"error": error}), 400
    return jsonify({"success": True})


@app.post("/admin/scrubber/start")
def admin_scrubber_start():
    require_admin_access()
    payload = request.get_json(silent=True) or {}
    term = (payload.get("term") or "").strip()
    download_pdf = bool(payload.get("download_pdf"))
    test_mode = bool(payload.get("test_mode"))
    source = payload.get("source") or SCRUB_SOURCES[0]["id"]
    if not term:
        return jsonify({"error": "Please enter a search term."}), 400
    success, error = start_scrub_thread(term, download_pdf, test_mode, source)
    if not success:
        return jsonify({"error": error or "Already running."}), 400
    return jsonify({"success": True})


@app.get("/admin/scrubber/status")
def admin_scrubber_status():
    require_admin_access()
    return jsonify(get_scrub_status())


@app.post("/admin/scrubber/cancel")
def admin_scrubber_cancel():
    require_admin_access()
    with scrub_state["lock"]:
        if not scrub_state["jobs"]:
            return jsonify({"error": "No job running."}), 400
        for job in scrub_state["jobs"]:
            job["cancel"].set()
        scrub_state["jobs"] = []
        scrub_state["log"].append("Cancelling…")
    return jsonify({"success": True})


@app.post("/admin/mass-ai/cancel")
def admin_mass_ai_cancel():
    require_admin_access()
    success, error = cancel_mass_ai_thread()
    if not success:
        return jsonify({"error": error}), 400
    return jsonify({"success": True})


@app.get("/admin/mass-ai/status")
def admin_mass_ai_status():
    require_admin_access()
    return jsonify(get_mass_ai_status())


@app.route("/admin/composer-clean", methods=["GET", "POST"])
def admin_composer_clean():
    if not has_admin_access():
        return redirect(url_for("browse"))

    folders = list_top_level_folders()
    selected = (
        request.args.get("folder")
        or request.form.get("folder")
        or (folders[0].name if folders else None)
    )
    status_message = None
    composers: list[dict] = []

    if selected:
        folder_path = BASE_DIR / selected
        if folder_path.exists() and folder_path.is_dir():
            composers = collect_composer_counts(folder_path)
        else:
            status_message = "Invalid folder."

    if request.method == "POST" and request.form.get("target"):
        folder_name = request.form.get("folder")
        selected = folder_name
        folder_path = BASE_DIR / folder_name
        sources = request.form.getlist("sources")
        target = request.form.get("target", "").strip()
        if not folder_name or not folder_path.exists() or not folder_path.is_dir():
            status_message = "Invalid folder."
        elif not sources or not target:
            status_message = "Select composers and provide a target name."
        else:
            try:
                updated = merge_composers_in_folder(
                    folder_path, sources, target)
                status_message = f"Updated {updated} files."
            except Exception as exc:
                status_message = f"Merge failed: {exc}"
        if folder_path.exists():
            composers = collect_composer_counts(folder_path)

    return render_template(
        "admin_composer_cleanup.html",
        admin_mode=True,
        admin_enabled=ADMIN_MODE,
        folders=[{"name": f.name} for f in folders],
        selected=selected,
        composers=composers,
        status=status_message,
    )


@app.route("/admin/ai-reset", methods=["GET", "POST"])
def admin_ai_reset():
    if not has_admin_access():
        return redirect(url_for("browse"))

    folders = list_top_level_folders()
    selected = "__all__"
    status_message = None

    if request.method == "POST":
        selected = request.form.get("folder") or "__all__"
        folder_path = None
        if selected != "__all__":
            folder_path = BASE_DIR / selected
            if not folder_path.exists() or not folder_path.is_dir():
                folder_path = None
                status_message = "Invalid folder."
        try:
            reset_count = reset_ai_flags(folder_path)
            scope = "entire library" if folder_path is None else selected
            status_message = f"Reset AI flags on {reset_count} files in {scope}."
        except Exception as exc:
            status_message = f"Reset failed: {exc}"

    return render_template(
        "admin_ai_reset.html",
        admin_mode=True,
        admin_enabled=ADMIN_MODE,
        folders=[{"name": f.name} for f in folders],
        selected=selected,
        status=status_message,
    )


@app.route("/admin/scrubber", methods=["GET", "POST"])
def admin_scrubber():
    if not has_admin_access():
        return redirect(url_for("browse"))

    status = get_scrub_status()
    term = request.args.get("term", "")
    return render_template(
        "admin_scrubber.html",
        admin_mode=True,
        admin_enabled=ADMIN_MODE,
        summary=status.get("summary") or [],
        status=None,
        term=term,
        download_pdf=True,
        test_mode=False,
        sources=SCRUB_SOURCES,
        source=SCRUB_SOURCES[0]["id"],
        logs=status.get("log") or [],
        running=status.get("running"),
    )


if __name__ == "__main__":
    # For local (non-Docker) testing
    app.run(host="0.0.0.0", port=5000, debug=True)
