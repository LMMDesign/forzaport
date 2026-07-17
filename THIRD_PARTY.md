# Third-party components

This addon bundles a small set of native helpers. Game cars, textures, GameDBs,
and other Forza assets are **not** included.

## forza_acl.dll (FH6 Mojo ACL)

- Purpose: decompress nfrechette ACL 2.1 tracks embedded in Mojo `.clipd`
- Location: `tools/acl/forza_acl.dll`
- Built from [nfrechette/acl](https://github.com/nfrechette/acl) 2.1.x plus a
  thin native wrapper (see `tools/acl/README.md`)
- License: ACL is MIT; the wrapper in this project is GPL-3.0 (same as the addon)

## gr2dump (FH5 Granny matrix dump)

- Purpose: read Autovista `.gr2` animation/skeleton data and emit JSON matrix
  tracks for Blender baking
- Location: `tools/gr2dump/`
- Runtime: Windows x64 + [.NET 8](https://dotnet.microsoft.com/download/dotnet/8.0)
- Depends on [LSLib](https://github.com/Norbyte/lslib) assemblies shipped beside
  `gr2dump.exe` (see `tools/gr2dump/REQUIREMENTS.txt`)

### granny2.dll (not bundled)

`granny2.dll` is a proprietary RAD Game Tools / Granny runtime. It is **not**
redistributed with this addon.

For FH5 animation import, place a legally obtained `granny2.dll` next to
`tools/gr2dump/gr2dump.exe` (same folder as on a machine that already has a
lawful Granny-based tool install). Static car import and FH6 Mojo ACL animation
work without it.

## Material tables

`data/material_table.json` and `data/material_table_fh6.json` are derived lookup
tables used to rebuild approximate Blender materials. They are not game assets.

## Upstream acknowledgements

- Doliman100 — ForzaTech carbin/modelbin research and importers (GPL-3.0)
- Norbyte — LSLib
- nfrechette — Animation Compression Library (ACL)
