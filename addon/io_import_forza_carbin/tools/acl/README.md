# ACL 2.1 helper for FH6 Mojo Autovista bake

`forza_acl.dll` — native decompress of BSI `ACLAnimationData.CompressedTransformData`.

FH6 Mojo import **requires** this DLL. There is no mid-only fallback.

## Source

Checked-in wrapper:

```text
tools/acl/forza_acl_native.cpp
```

Built against [nfrechette/acl](https://github.com/nfrechette/acl) 2.1.x (headers + RTM).

## Build

Requires Zig (`zig c++`) or any C++17 toolchain that can compile against ACL 2.1 headers.

Example (Zig), from the addon root, with ACL 2.1 checked out locally:

```text
zig c++ -shared -O2 -std=c++17 ^
  -I<path-to-acl>/includes ^
  -I<path-to-acl>/external/rtm/includes ^
  -o tools/acl/forza_acl.dll ^
  tools/acl/forza_acl_native.cpp
```

On this workspace the ACL 2.1 tree is typically:

```text
H:\Documents\Forza Import\_tools\acl-2.1.0
```

so:

```text
zig c++ -shared -O2 -std=c++17 ^
  -I"H:\Documents\Forza Import\_tools\acl-2.1.0\includes" ^
  -I"H:\Documents\Forza Import\_tools\acl-2.1.0\external\rtm\includes" ^
  -o tools/acl/forza_acl.dll ^
  tools/acl/forza_acl_native.cpp
```

`scripts/build_release.py` packages the compiled `tools/acl/forza_acl.dll`. The C++ source stays in the development repository (it is small and not proprietary).

## Exports

| Symbol | Role |
|--------|------|
| `forza_acl_info` | Query tracks / samples / rate / duration / version |
| `forza_acl_decompress_sample` | Decompress one sample (endpoint / matching) |
| `forza_acl_decompress_all` | Decompress every sample in one call |

Older locally built DLLs without `forza_acl_decompress_all` still load: Python detects the missing export and falls back to a per-sample loop.

### C ABI — bulk decompress

```cpp
extern "C" int forza_acl_decompress_all(
    const void* compressed_data,
    int compressed_size,
    float* output,
    int output_float_capacity,
    int* out_num_tracks,
    int* out_num_samples
);
```

Flat output layout (sample-major):

```text
output[(sample_index * num_tracks + track_index) * 12 + component]
```

Each track is 12 floats:

```text
0  qx
1  qy
2  qz
3  qw
4  tx
5  ty
6  tz
7  reserved/padding
8  sx
9  sy
10 sz
11 reserved/padding
```

Sample times and `sample_rounding_policy::nearest` match `forza_acl_decompress_sample` (`time = sample_index / sample_rate` when `num_samples > 1`, else `0`).

## Python

`parsing/mojo_acl.py`:

- Prefer `forza_acl_decompress_all` inside `decompress_all_samples()` (still `@lru_cache`).
- Endpoint helpers keep calling `decompress_sample()`.
- `acl_bulk_supported()` reports whether the loaded DLL has the bulk export.

## Benchmark (dev only)

```text
python scripts/benchmark_acl.py PATH\to\carclips.clipd
```

## Optional live test

```text
set FORZA_ACL_INTEGRATION=1
set FORZA_ACL_CLIPD=PATH\to\carclips.clipd
python -m unittest tests.test_mojo_acl
```
