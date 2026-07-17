# Import Forza Car (.carbin)

Blender 4.1+ addon for importing ForzaTech car models from **Forza Horizon** and **Forza Motorsport** games that use `.carbin` / `.modelbin` packaging.

Based on the parsing and import approach from [Doliman100/ForzaTech-extraction-tools](https://github.com/Doliman100/ForzaTech-extraction-tools).

## Install

1. Download a release zip (`io_import_forza_carbin-<version>.zip`), or build one with
   `python scripts/build_release.py`.
2. In Blender: **Edit → Preferences → Add-ons → Install…** and choose the zip.
3. Enable **Import Forza Car (.carbin)**.

The archive root must contain `io_import_forza_carbin/__init__.py` (one folder deep).

Or copy the `io_import_forza_carbin` folder into your Blender `scripts/addons` directory.

## How to import a car

The addon does **not** ship cars. You copy them from your game install into a folder you control, then point Blender at that folder.

### 1. Copy cars from the game

On Xbox / Microsoft Store installs, each car is a zip under:

`…\Forza Horizon N\Content\media\cars\<MediaName>.zip`

Example: `FER_F80_25.zip`

Copy one or more of those zips into a folder you own, for example:

`D:\ForzaRips\cars\`

You can leave them **zipped**, or extract each zip into a folder with the same name (so you get `FER_F80_25\FER_F80_25.carbin`). Both layouts work.

### 2. Point the addon at your folder

**Edit → Preferences → Add-ons → Import Forza Car → Car Library Folders → Add Folder**

Choose the folder that contains your copied cars (the folder with the `.zip` files, or a parent that contains `media\cars` / `Media\Cars`).

### 3. Import in Blender

Either:

- **File → Import → Forza Car** and pick a car from the list, or  
- **File → Import → Forza Car (.carbin/.zip)…** and browse to a specific `.zip` or `.carbin`

### Materials, tires, and shared library (recommended)

Full paint / tires need the shared `_library` next to the cars (same place as in the game):

`…\Content\media\cars\_library\` (`Materials.zip`, `Textures.zip`, tire zips, shaders, …)

Easiest options:

- Copy `_library` into your cars folder as `…\cars\_library\`, **or**
- Add the game’s `Content\media` folder as a library root (read-only is fine), **or**
- Add per-game **Tire Libraries** in preferences (FH5 / FH6 / Motorsport each get their own tires folder); import picks the matching game from the car path or Mojo vs GR2 media
- Set **Materials Folder** if you keep materials elsewhere

### GameDB (optional, better wheels)

- Needs a *decrypted* SQLite `.slt` (header `SQLite format 3`).
- The encrypted `media\stripped\gamedbRC.slt` from the install will **not** work.
- Set **GameDB Path** or **GameDB Folder**, or disable **Use GameDB** and use **Wheel Positioning → Carbin**.

### Animations (optional)

- **FH5 (and similar):** `Animations\*.gr2` — **matrix pipeline** via bundled `tools/gr2dump` (needs [.NET 8](https://dotnet.microsoft.com/download/dotnet/8.0)). Same bake as Divine Collada local 4×4 samples.
  - Also needs a legally obtained **`granny2.dll`** next to `tools/gr2dump/gr2dump.exe`. That proprietary Granny runtime is **not** redistributed with this addon. Static mesh import still works without it.
- **FH6:** Mojo `.clipd` / `.skeld` under `Scene/animations/Mojo/` — native **ACL 2.1** tracks are required (bundled `tools/acl/forza_acl.dll`). Missing or unmatched ACL is a fatal import error; there is no mid fallback. Not mixed with FH5 GR2. Self-contained (no `granny2.dll`).

## Preferences summary

| Setting | Purpose |
|--------|---------|
| **Car Library Folders** | Your folder(s) of copied car `.zip` / extracted cars (quick-import menus) |
| **GameDB Folder** | Folder of decrypted `.slt` databases |
| **Tire Libraries** | Per-game tire folders (`tire_*.zip` or extracted). Import selects FH5 / FH6 / Motorsport from the car path or Autovista media |
| **Materials Folder** | Optional override if shared materials are not next to the cars |
| **Import Animations** | Bake Autovista clips after car import |
| Default LOD / draw group / wheels / materials | Used by library quick-import |

## Caches (portable)

Car `.zip` files can stay zipped — the addon extracts only what each import needs.

| Cache | Location |
|-------|----------|
| Zip extracts | `~/.cache/forza_import/zipfs` (`%USERPROFILE%\.cache\...` on Windows) |
| DDS staging for textures | `%TEMP%\forza_import_dds` |

- Zip extracts auto-trim to **2 GiB** (oldest unused files first).
- **Edit → Preferences → Add-ons → Import Forza Car → Clear Cache** deletes both trees and shows current sizes.
- Safe to delete manually; files are re-extracted / re-converted as needed.

## Environment (optional)

| Variable | Effect |
|----------|--------|
| `FORZA_TABLE_PATH` | Override path to a custom `material_table.json` |
| `FORZA_ADDON_DEV=1` | Enable research-only hot reload, Mojo diagnostics, and pose oracle |

Normal addon use does not load or run the research hooks. With development mode enabled, `FORZA_MOJO_DEBUG=1` and `FORZA_MOJO_POSE_ORACLE` become available for controlled investigation. FH6 Mojo always requires ACL 2.1.

## Building a release zip

```text
python scripts/build_release.py
```

Uses `bl_info["version"]` from `__init__.py`. Excludes research modules, `.pdb`/`.lib`,
and `granny2.dll`. See `THIRD_PARTY.md` for bundled native notes.

## License

GNU GPL v3 — see `LICENSE`. Upstream parsing/import foundations: Doliman100 ForzaTech extraction tools (GPL-3.0). Third-party native notes: `THIRD_PARTY.md`.

## Credits

- Doliman100 — original ForzaTech carbin/modelbin research and importers
- Community GameDB dumps / decryption tools (not bundled)
- nfrechette ACL / Norbyte LSLib — see `THIRD_PARTY.md`
