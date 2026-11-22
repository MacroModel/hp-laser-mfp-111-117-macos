#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p build
files=()
for source in splix/src/*.cpp; do
    [[ "$source" == */pstoqpdl.cpp ]] || files+=("$source")
done
/usr/bin/clang++ -arch arm64 -O2 -std=gnu++14 -Wno-deprecated-declarations -DDISABLE_JBIG -DDISABLE_THREADS -Isplix/include "${files[@]}" -lcups -lcupsimage -o build/rastertoqpdl
/usr/bin/codesign -f -s - build/rastertoqpdl
/usr/bin/python3 src/make_ppd.py
/usr/bin/cupstestppd build/queue.ppd
/usr/bin/cupstestppd -I filters build/renderer.ppd
[[ ! -f src/Panel.swift ]] || /usr/bin/swiftc -O -parse-as-library src/Panel.swift -o build/Panel
/usr/bin/swiftc -O src/Launcher.swift -o build/Launcher
(cd bridge && go build -mod=vendor -trimpath -o ../build/ipp-bridge .)
