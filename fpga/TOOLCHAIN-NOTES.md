# ECP5 toolchain dependency finding

Upstream report: [YosysHQ/nextpnr#1807](https://github.com/YosysHQ/nextpnr/issues/1807).

Building nextpnr `3edea68ef37eddbe9fc4556de7d51ba08af201a0` on macOS
26.5.1 arm64, Apple clang 21.0.0 (`clang-2100.1.1.101`), CMake 4.4.3,
and Python 3.14.7 selected Conda's Boost 1.73.0 even though Homebrew's
Boost 1.92.0 was installed. CMake accepted that selection. Compilation then
failed in `bba/main.cc` through `boost/container_hash/hash.hpp:131`:

```
error: no template named 'unary_function' in namespace 'std'; did you mean '__unary_function'?
struct hash_base : std::unary_function<T, std::size_t> {};
```

The [nextpnr build instructions](https://github.com/YosysHQ/nextpnr#nextpnr-ecp5)
were used with `ARCH=ecp5`, `TRELLIS_INSTALL_PREFIX=/opt/homebrew`,
`BUILD_GUI=OFF`, `BUILD_PYTHON=OFF`, and
`Python3_EXECUTABLE=/opt/homebrew/opt/python@3.14/bin/python3.14` (the interpreter
linked by this host's `pytrellis.so`). The failed build exited 2.

A fresh build directory with these additional CMake arguments selects the
installed compatible Boost and builds the previously failing `bbasm` target:

```
-DBoost_DIR=/opt/homebrew/opt/boost/lib/cmake/Boost-1.92.0
-DCMAKE_IGNORE_PREFIX_PATH=/opt/homebrew/anaconda3
```

No nextpnr source patch was needed. This establishes the workaround for the
observed dependency failure; it is not evidence of instrument correctness.
The minimum compatible Boost version has not been established.
