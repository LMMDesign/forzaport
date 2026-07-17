# ACL 2.1 helper for FH6 Mojo Autovista bake

`forza_acl.dll` — native decompress of BSI `ACLAnimationData.CompressedTransformData`.

FH6 Mojo import **requires** this DLL. There is no mid-only fallback.

Built with Zig + [nfrechette/acl](https://github.com/nfrechette/acl) 2.1.x and a thin C++ wrapper:

```text
zig c++ -shared -O2 -std=c++17
  -I<path-to-acl>/includes -I<path-to-acl>/external/rtm/includes
  -o tools/acl/forza_acl.dll
  forza_acl_native.cpp
```

Exports:

- `forza_acl_info`
- `forza_acl_decompress_sample`
