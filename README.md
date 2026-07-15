# Import Forza Car (.carbin)

Blender 4.1+ addon for importing ForzaTech car models from **Forza Horizon** and **Forza Motorsport** games that use `.carbin` / `.modelbin` packaging.

Supports:

- Extracted `Media\Cars\...` trees (FH4 / FH5 style)
- **FH6** Media installs where cars, materials, textures, and tires ship as **`.zip`** archives (resolved on demand)
- Data-driven materials via bundled `data/material_table.json` (+ FH6 overlay)
- Optional **GameDB** `.slt` for accurate wheel / tire sizing
- Optional **Granny `.gr2` animations** via LSLib (FH5-era cars)

Based on the parsing and import approach from [Doliman100/ForzaTech-extraction-tools](https://github.com/Doliman100/ForzaTech-extraction-tools).

## Install

1. Zip the `io_import_forza_carbin` folder so the archive root contains `__init__.py` (not a nested extra folder).
2. In Blender: **Edit → Preferences → Add-ons → Install…** and choose the zip.
3. Enable **Import Forza Car (.carbin)**.

Or copy `io_import_forza_carbin` into your Blender `scripts/addons` directory.

## Setup (any machine)

Nothing is hardcoded to a particular PC. Point the addon at **your** game files via preferences and/or the import dialog.

### Preferences

**Edit → Preferences → Add-ons → Import Forza Car**

| Setting | Purpose |
|--------|---------|
| **Car Library Folders** | Roots that contain `Media\Cars` or `Content\media\cars` (for quick-import menus) |
| **GameDB Folder** | Folder of *decrypted* `.slt` databases |
| **Tires / Materials** | Optional overrides; leave empty to auto-detect under Media |
| **LSLib divine.exe** | Optional; FH5 `.gr2` → `.dae` for animations |
| Default LOD / draw group / wheel positioning / materials | Used by library quick-import |

### What you need on disk

**Cars**

- FH5-style: `...\Media\Cars\<MediaName>\<MediaName>.carbin` (+ `scene\`… )
- FH6: `...\Content\media\cars\<MediaName>.zip` (or an extracted car folder)

Browse **File → Import → Forza Car (.carbin/.zip)…** and select the `.carbin` or car `.zip`.

The importer resolves `GAME:\Media\...` paths against the Media root next to the car (including `Materials.zip`, `Textures.zip`, `tires\tire_*.zip`).

**GameDB (optional but recommended for wheels)**

- Must be a readable SQLite `.slt` (header `SQLite format 3`).
- The encrypted `media\stripped\gamedbRC.slt` from the game install is **not** usable as-is.
- Use a decrypted dump (or a community runtime dump) and set **GameDB Path** or **GameDB Folder**.
- Without GameDB: disable **Use GameDB** and set **Wheel Positioning → Carbin**.

**Animations (optional)**

- FH5: `Animations\*.gr2` + LSLib `divine.exe` (with `granny2.dll` beside it).
- FH6: Mojo `.clipd` / `.skeld` under `Scene\animations\Mojo\` — **not supported** yet (the operator reports this clearly).

## Caches (portable)

| Cache | Location |
|-------|----------|
| Zip extracts | `~/.cache/forza_import/zipfs` (`%USERPROFILE%\.cache\...` on Windows) |
| DDS staging for textures | `%TEMP%\forza_import_dds` |

Safe to delete; files are re-extracted / re-converted as needed.

## Environment (optional)

| Variable | Effect |
|----------|--------|
| `FORZA_TABLE_PATH` | Override path to a custom `material_table.json` |
| `FORZA_ADDON_DEV=1` | Hot-reload material/importer modules on enable (developers only) |

## License

GNU GPL v3 — see `LICENSE`. Upstream parsing/import foundations: Doliman100 ForzaTech extraction tools (GPL-3.0).

## Credits

- Doliman100 — original ForzaTech carbin/modelbin research and importers
- Community GameDB dumps / decryption tools (not bundled)
