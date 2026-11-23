# Third-party components

RF Link Lab application and firmware source are MIT licensed. Portable packages also contain third-party software with its own licenses. Preserve the license and distribution metadata shipped with each component.

- CPython: Python Software Foundation license and bundled dependency notices; official portable distribution from [python-build-standalone](https://github.com/astral-sh/python-build-standalone). Linux runtime notices remain in `runtime/python`.
- NumPy and SciPy: BSD-style licenses and notices for bundled numerical libraries, retained in wheel distribution metadata and `.libs` directories.
- scikit-learn, joblib, threadpoolctl, narwhals and cloudpickle: their respective distribution metadata/licenses are retained under `runtime/site-packages` in the Ubuntu archive.
- Qt for Python / PySide6 Essentials and Shiboken6: Qt/PySide component licensing and third-party notices are retained in the installed wheel files. Application code uses the distributed dynamic libraries.
- pyserial: its license and metadata remain with the package.
- PyInstaller: used to build the optional Windows bundle; see [PyInstaller licensing](https://pyinstaller.org/en/stable/license.html).
- Zig/LLVM: build tools for the Cortex-M4 firmware, not required to run the delivered board binary. See the original toolchain notices.

The Ubuntu portable archive includes a private, pinned Nuand bladeRF 2025.10
host-tool/library build and hosted xA9 FPGA v0.16.0 under
`hardware-tools/nuand-2025.10`. Preserve its `MANIFEST.json`, Nuand `COPYING` and
the complete `install/share/nuand-licenses` directory. Nuand's component and
per-file terms apply: Nuand's COPYING identifies GPLv2 for the CLI and LGPLv2.1
for libbladeRF; FPGA IP and bundled source components have their own
notices. The `CORRESPONDING-SOURCE.json` in the application archive identifies
the pinned source commit, submodules and SHA256 of the separate
`Nuand-bladeRF-2025.10-corresponding-source.tar.gz` release companion. Distribute
that source companion with the Ubuntu application archive and retain its
embedded notices, including the Analog Devices source notices. The included
SDK metadata retains its original build prefix; the cache is intended for
runtime use rather than as a relocatable SDK.

ST-LINK tools and system USB libraries are installed separately through Ubuntu.
The explicit hardware launcher loads the selected xA9 FPGA into volatile RAM;
it does not flash FX3 firmware or persistent FPGA storage.

The Ubuntu package is assembled from official precompiled Linux components on a Windows build host. This does not establish that it has been executed on Ubuntu; consult `BUILD-MANIFEST.json` and the validation record.
