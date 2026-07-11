#!/usr/bin/env bash
# Pull the local-only test-sample VI corpora from their upstream repositories.
#
# lvkit does NOT redistribute sample VIs: the samples/ directory is .gitignore'd
# and no .vi is committed to this repo or shipped in the wheel. This script
# fetches the sample sets on demand, for local testing only, straight from each
# project's own permissively-licensed upstream — so provenance and licensing are
# always traceable to the source.
#
# Usage:
#   scripts/pull_samples.sh          # clone any missing sets into samples/
#   scripts/pull_samples.sh --force  # remove and re-clone every set
#
# Every source below has a provable, permissive license (verified from the
# repo's own LICENSE file). If you add a set, it MUST come from a source with a
# provable permissive license and be recorded here.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="$ROOT/samples"
FORCE="${1:-}"

mkdir -p "$DEST"

# name|git-url|license|source
GIT_SETS=(
  "JKI-EasyXML|https://github.com/JKISoftware/JKI-EasyXML.git|BSD-3-Clause|https://github.com/JKISoftware/JKI-EasyXML"
  "JKI-VI-Tester|https://github.com/JKISoftware/JKI-VI-Tester.git|BSD-3-Clause|https://github.com/JKISoftware/JKI-VI-Tester"
  "LabVIEW-DAQ|https://github.com/KL-Turner/LabVIEW-DAQ.git|MIT|https://github.com/KL-Turner/LabVIEW-DAQ"
  "pylabview-full|https://github.com/mefistotelis/pylabview.git|MIT|https://github.com/mefistotelis/pylabview"
  "DCAF-DAQModule|https://github.com/LabVIEW-DCAF/DAQModule.git|Apache-2.0|https://github.com/LabVIEW-DCAF/DAQModule"
)

for entry in "${GIT_SETS[@]}"; do
  IFS='|' read -r name url license source <<<"$entry"
  target="$DEST/$name"
  if [[ "$FORCE" == "--force" ]]; then rm -rf "$target"; fi
  if [[ -d "$target" ]]; then
    echo "skip   $name (already present) — $license — $source"
  else
    echo "clone  $name — $license — $source"
    git clone --quiet --depth 1 "$url" "$target"
  fi
done

# OpenG Toolkit (BSD-3-Clause, OSI-approved). Not a git repo — it ships as a
# VIPM ".vip" package, which is a ZIP whose internal "File Group N/" layout is
# exactly samples/OpenG/extracted/. Reproduce it fully from the public download:
#   1. download the .vip below,
#   2. unzip it into samples/OpenG/extracted/  (gives the File Group N/ tree),
#   3. let lvkit turn each VI into the *_BDHb.xml the tests load, via
#      lvkit.extractor.extract_llb (LLBs are zips) + extract_vi_xml.
# No LabVIEW/VIPM install and no redistribution — pulled straight from source.
OPENG_VIP_URL="https://sourceforge.net/projects/opengtoolkit/files/lib_openg_toolkit/4.x/openg.org_lib_openg_toolkit-4.0.1.9.vip/download"
if [[ "$FORCE" == "--force" ]]; then rm -rf "$DEST/OpenG"; fi
if [[ -d "$DEST/OpenG/extracted" ]]; then
  echo "skip   OpenG (already present) — BSD-3-Clause — https://sourceforge.net/projects/opengtoolkit/"
else
  echo "fetch  OpenG Toolkit 4.0.1.9 (.vip) — BSD-3-Clause — sourceforge.net/projects/opengtoolkit"
  mkdir -p "$DEST/OpenG/extracted"
  tmp="$(mktemp -d)"
  curl -sL "$OPENG_VIP_URL" -o "$tmp/openg.vip"
  python3 -c "import zipfile,sys; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" \
    "$tmp/openg.vip" "$DEST/OpenG/extracted"
  rm -rf "$tmp"
  echo "       unzipped; run the lvkit LLB/VI extraction to produce *_BDHb.xml (see comment above)"
fi

echo
echo "Done. samples/ is .gitignore'd — these VIs are local-only and never redistributed."
