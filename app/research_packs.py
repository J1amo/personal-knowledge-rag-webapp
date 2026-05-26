from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import config
from .db import connect, init_db, json_dumps, utc_now

PACKS_DIR = config.PROJECT_ROOT / "research_packs"


def _read_data_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Pack data file not found: {path}")
    text = path.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        try:
            import yaml  # type: ignore
        except ImportError as exc:
            raise ValueError(
                f"{path.name} must use JSON-compatible YAML unless PyYAML is installed"
            ) from exc
        data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError(f"Pack data file must contain an object: {path}")
    return data


def _pack_dir(pack_id: str) -> Path:
    if not pack_id or "/" in pack_id or "\\" in pack_id or pack_id.startswith("."):
        raise ValueError("Invalid pack_id")
    path = (PACKS_DIR / pack_id).resolve()
    root = PACKS_DIR.resolve()
    if root != path and root not in path.parents:
        raise ValueError("Invalid pack path")
    return path


def load_pack_manifest(pack_id: str) -> dict[str, Any]:
    return _read_data_file(_pack_dir(pack_id) / "manifest.yaml")


def load_pack_ontology(pack_id: str) -> dict[str, Any]:
    return _read_data_file(_pack_dir(pack_id) / "ontology.yaml")


def _resolve_pack_file(pack_id: str, relative_path: str) -> Path:
    base = _pack_dir(pack_id).resolve()
    path = (base / relative_path).resolve()
    if base != path and base not in path.parents:
        raise ValueError("Pack file path escapes pack directory")
    return path


def load_pack_template(pack_id: str, template_name: str) -> str:
    manifest = load_pack_manifest(pack_id)
    outputs = manifest.get("outputs") or {}
    if template_name not in outputs:
        raise KeyError(f"Unknown pack template: {template_name}")
    path = _resolve_pack_file(pack_id, outputs[template_name])
    if not path.exists():
        raise FileNotFoundError(f"Pack template not found: {path}")
    return path.read_text(encoding="utf-8")


def list_pack_templates(pack_id: str) -> dict[str, dict[str, str]]:
    manifest = load_pack_manifest(pack_id)
    outputs = manifest.get("outputs") or {}
    titles = manifest.get("output_titles") or {}
    result = {}
    for output_type, relative_path in outputs.items():
        result[output_type] = {
            "output_type": output_type,
            "title": titles.get(output_type) or output_type.replace("_", " ").title(),
            "path": relative_path,
        }
    return result


def load_pack(pack_id: str) -> dict[str, Any]:
    manifest = load_pack_manifest(pack_id)
    ontology = load_pack_ontology(pack_id)
    return {
        "pack_id": pack_id,
        "manifest": manifest,
        "ontology": ontology,
        "templates": list_pack_templates(pack_id),
    }


def _scan_pack_manifests() -> list[dict[str, Any]]:
    if not PACKS_DIR.exists():
        return []
    packs = []
    for manifest_path in sorted(PACKS_DIR.glob("*/manifest.yaml")):
        pack_dir = manifest_path.parent
        try:
            manifest = _read_data_file(manifest_path)
        except Exception as exc:
            packs.append(
                {
                    "pack_id": pack_dir.name,
                    "name": pack_dir.name,
                    "version": "unknown",
                    "enabled": False,
                    "error": str(exc),
                    "manifest": {},
                }
            )
            continue
        pack_id = manifest.get("pack_id") or pack_dir.name
        packs.append(
            {
                "pack_id": pack_id,
                "name": manifest.get("name") or pack_id,
                "version": manifest.get("version") or "0.0.0",
                "enabled": True,
                "error": None,
                "manifest": manifest,
            }
        )
    return packs


def sync_research_packs() -> list[dict[str, Any]]:
    init_db()
    scanned = _scan_pack_manifests()
    now = utc_now()
    with connect() as con:
        for pack in scanned:
            if pack.get("error"):
                continue
            con.execute(
                """
                INSERT INTO research_packs (
                  pack_id, name, version, manifest_json, enabled, installed_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(pack_id) DO UPDATE SET
                  name=excluded.name,
                  version=excluded.version,
                  manifest_json=excluded.manifest_json,
                  enabled=excluded.enabled,
                  updated_at=excluded.updated_at
                """,
                (
                    pack["pack_id"],
                    pack["name"],
                    pack["version"],
                    json_dumps(pack["manifest"]),
                    1 if pack["enabled"] else 0,
                    now,
                    now,
                ),
            )
    return scanned


def list_packs() -> list[dict[str, Any]]:
    return [
        {
            "pack_id": pack["pack_id"],
            "name": pack["name"],
            "version": pack["version"],
            "enabled": pack["enabled"],
            "error": pack.get("error"),
            "outputs": pack.get("manifest", {}).get("outputs", {}),
            "output_titles": pack.get("manifest", {}).get("output_titles", {}),
        }
        for pack in sync_research_packs()
    ]
