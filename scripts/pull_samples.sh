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
# The sample corpora are "local-only-always" — never committed, always
# re-pullable — so they belong in the lvkit cache, not loose at the repo root.
DEST="$ROOT/.lvkit/cache/samples"
FORCE="${1:-}"

mkdir -p "$DEST"

# name|git-url|license|source
GIT_SETS=(
  "JKI-EasyXML|https://github.com/JKISoftware/JKI-EasyXML.git|BSD-3-Clause|https://github.com/JKISoftware/JKI-EasyXML"
  "JKI-VI-Tester|https://github.com/JKISoftware/JKI-VI-Tester.git|BSD-3-Clause|https://github.com/JKISoftware/JKI-VI-Tester"
  "LabVIEW-DAQ|https://github.com/KL-Turner/LabVIEW-DAQ.git|MIT|https://github.com/KL-Turner/LabVIEW-DAQ"
  "pylabview-full|https://github.com/mefistotelis/pylabview.git|MIT|https://github.com/mefistotelis/pylabview"
  "DCAF-DAQModule|https://github.com/LabVIEW-DCAF/DAQModule.git|Apache-2.0|https://github.com/LabVIEW-DCAF/DAQModule"
  "measurement-plugin-labview|https://github.com/ni/measurement-plugin-labview.git|MIT|https://github.com/ni/measurement-plugin-labview"
  "lv-flex-channel-examples|https://github.com/illuminated-g/lv-flex-channel-examples.git|MIT|https://github.com/illuminated-g/lv-flex-channel-examples"
  "LabVIEW-OOP-Classes|https://github.com/ismet55555/LabVIEW-OOP-Classes.git|MIT|https://github.com/ismet55555/LabVIEW-OOP-Classes"
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

# OpenG Toolkit (BSD-3-Clause, OSI-approved). Not a git repo — each library
# ships as its own VIPM ".vip" package (a ZIP) on SourceForge, whose internal
# "File Group 0/user.lib/_OpenG.lib/<lib>/<lib>.llb/" layout is exactly what
# samples/OpenG/extracted/ needs. NOTE: the combined "openg.org_lib_openg_
# toolkit" meta-package (formerly fetched here) contains no VI content at all
# — it is a VIPM dependency-resolution stub. scripts/reproduce_openg_corpus.py
# instead downloads each individual oglib_*.vip library package, unzips them
# all (they merge cleanly — no filename collisions across libraries), and
# runs lvkit.extractor.extract_llb (for the few that ship a true binary LLB
# container) + extract_vi_xml (one VI at a time, memory-flat) to produce the
# *_BDHb.xml the tests load. No LabVIEW/VIPM install and no redistribution —
# pulled straight from source.
if [[ "$FORCE" == "--force" ]]; then rm -rf "$DEST/OpenG"; fi
if [[ -d "$DEST/OpenG/extracted" ]]; then
  echo "skip   OpenG (already present) — BSD-3-Clause — https://sourceforge.net/projects/opengtoolkit/"
else
  echo "fetch  OpenG Toolkit libraries (.vip x17) — BSD-3-Clause — sourceforge.net/projects/opengtoolkit"
  mkdir -p "$DEST/OpenG/extracted"
  python3 "$ROOT/scripts/reproduce_openg_corpus.py" "$DEST/OpenG/extracted"
fi

echo
echo "Done. samples/ is .gitignore'd — these VIs are local-only and never redistributed."
