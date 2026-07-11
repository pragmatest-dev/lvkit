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

# OpenG Toolkit (BSD-3-Clause, OSI-approved) is not distributed as a plain git
# repo — it ships via VIPM (JKI VI Package Manager) and SourceForge. Install it
# through LabVIEW/VIPM, then place the extracted VIs under samples/OpenG.
if [[ ! -d "$DEST/OpenG" ]]; then
  echo "MANUAL OpenG — BSD-3-Clause — obtain via VIPM (https://www.vipm.io/package/openg.org_lib_openg_toolkit/)"
  echo "       or SourceForge (https://sourceforge.net/projects/opengtoolkit/); extract into samples/OpenG"
else
  echo "skip   OpenG (already present) — BSD-3-Clause — https://sourceforge.net/projects/opengtoolkit/"
fi

echo
echo "Done. samples/ is .gitignore'd — these VIs are local-only and never redistributed."
