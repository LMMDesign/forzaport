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

### Materials, tires, and shared library

In **Game Installations**, select each game's **install folder** once — the folder
that contains `Content` (for example `…\Forza Horizon 5` or `…\Forza Horizon 6`).
`Content` or `Content\media` are also accepted. The path is stored as you picked it;
the addon resolves Media at import time and loads shared resources from:

`…\Content\media\cars\_library\` (`Materials.zip`, texture zips, tire zips, shaders, …)

**Car Library Folders** remain separate: those are only the copied car `.zip` files or
extracted car folders shown in the quick-import menu.

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
| **Game Installations** | One install folder per game (contains `Content`); materials, textures, shaders, tires, and other shared files are derived from its Media tree |
| **GameDB Folder** | Folder of decrypted `.slt` databases |
| **Import Animations** | Bake Autovista clips after car import |
| Default LOD / draw group / wheels / materials | Used by library quick-import |

## Materials (clean v3, FH6 only)

Materials are built **directly from game data** (fail closed):

1. Parse the **complete serialized schema** from MatI → parent materialbin/shaderbin
   (Local vs Instance params, TXMP/CBMP/SPMP, defaults, companion `shaderbin.xml`
   variant metadata where present).
2. Resolve parameter identity via `data/name_hashes.json` (FTS NameHashService).
3. Analyse **exact pass/variant sample sites** with **dxc**: every `.pcdxil.pso`
   archive member is a distinct identity (shaderbin SHA + full member path +
   variant directory + scenario + stage + PSO SHA). `CarLightScenario` under the
   proven raster variant is the usual **primary surface** pass — never the sole
   source of material information.
4. Exact-SHA **pass contracts** (`materials/shader_pass_contracts/<sha>.json`)
   declare which additional passes contribute which sample sites and whether
   those facts are relevant to Blender (`MAIN_SURFACE_SHADING`, `VISIBILITY`,
   `DEBUG_ONLY`, …). Unknown SHAs fail closed.
5. Evaluate exact sample-site contracts (bindings + typed branch predicates +
   typed UV expressions) into `EvaluatedMaterialSampleSites`, then
   `ForzaMaterialIR`, then a minimal Blender graph.

For exact contracted shaderbin SHAs, evaluated sample sites are authoritative.
Production does **not** discover semantics via register-keyed `TextureBinding`
merges (`PassMergeSpec` / `_merge_pass_sites`). A
`LEGACY_COMPATIBILITY_VIEW` may exist only for explicitly non-contracted or
diagnostic paths and fails closed if reached for a contracted SHA.

Static DXIL analysis is cached by game + shaderbin SHA + full PSO member path +
PSO SHA + parser version + variant + pass + stage. Instance evaluation is never
memoized by shader name alone.

UVChoice (`UVChoice_OnCh1_OffCh2` → TEXCOORD0/1) applies only to exact SHAs with
independent DXIL proof (`8df4836b…` car_standard, `8d4ef07a…`
car_standard_emissive, `af463726…` car_standard_fabric). Unknown SHAs fail closed.

Requires `dxc` (see `THIRD_PARTY.md` / `FORZA_DXC`).

## Caches (portable)

Car `.zip` files can stay zipped — the addon extracts only what each import needs.

| Cache | Location |
|-------|----------|
| Zip extracts | `~/.cache/forza_import/zipfs` (`%USERPROFILE%\.cache\...` on Windows) |
| DXIL binding memo | `~/.cache/forza_import/zipfs/shader_bindings` |
| DDS staging for textures | `%TEMP%\forza_import_dds` |

- Zip extracts auto-trim to **2 GiB** (oldest unused files first).
- **Edit → Preferences → Add-ons → Import Forza Car → Clear Cache** deletes both trees and shows current sizes.
- Safe to delete manually; files are re-extracted / re-converted as needed.

## Environment (optional)

| Variable | Effect |
|----------|--------|
| `FORZA_DXC` | Full path to `dxc.exe` for material DXIL binding extraction |
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
- Nenkai / ForzaTech Studio — NameHashService and bundle research
- Community GameDB dumps / decryption tools (not bundled)
- nfrechette ACL / Norbyte LSLib — see `THIRD_PARTY.md`
