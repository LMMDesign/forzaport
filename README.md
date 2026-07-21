# Import Forza Car (.carbin) — ForzaPort

Blender 4.1+ addon that imports ForzaTech cars (`.carbin` / `.modelbin`) from
Forza Horizon and Forza Motorsport.

## Supported workflow

1. Copy car `.zip` files from the game `Content/media/cars/` into a folder you own.
2. Point the addon at that **Car Library** folder and at each game’s **install**
   folder (for shared Media libraries).
3. **File → Import → Forza Car** (or browse to a `.carbin` / `.zip`).

Materials for **exact contracted shaderbin SHAs** evaluate game sample sites into
an IR graph, then Blender nodes. Unknown SHAs fail closed.

## Install

**From a release zip (recommended)**

1. Download `io_import_forza_carbin-*.zip` from
   [Releases](https://github.com/LMMDesign/forzaport/releases).
2. Blender 4.2+ / 5.x: **Edit → Preferences → Get Extensions → drop-down →
   Install from Disk…** and choose the zip.
   (Older builds: **Add-ons → Install Legacy Add-on…**.)
3. Enable **Import Forza Car (.carbin)**.

The package includes `blender_manifest.toml` for the Extensions system.

**From this repository**

1. Copy `addon/io_import_forza_carbin/` into Blender’s `scripts/addons/` directory
   (folder name must remain `io_import_forza_carbin`).
2. Enable the add-on in Preferences.

Or build a zip locally from the repository root:

```text
python scripts/build_release.py
```

Optional: decrypted GameDB `.slt` for wheel layout; `dxc` for DXIL binding
extraction (`FORZA_DXC`). See `THIRD_PARTY.md`.

## Usage

- **Car Library Folders** — your copied cars
- **Game Installations** — each game folder that contains `Content`
- Import via the Forza Car menu or file browser

## Tests

From this repository root:

```text
set PYTHONPATH=addon
python -m unittest discover -s tests -p "test_*.py"
```

On Unix: `PYTHONPATH=addon python -m unittest discover -s tests -p "test_*.py"`

Some cases skip when optional game media is not available.

## Repository layout

```text
addon/io_import_forza_carbin/   # Blender package
tests/                          # product tests
scripts/build_release.py        # release zip
```

## Limitations

- Not every shader family or sample site is contracted; unresolved sites stay
  fail-closed or rejected.
- Alpha/discard coverage is incomplete for the full corpus.
- Production material sharing is disabled; tyre IR work is not in production.

## License

GNU GPL v3 — see `LICENSE`.
