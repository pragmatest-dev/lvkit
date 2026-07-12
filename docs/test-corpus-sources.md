# Test-corpus sources

lvkit ships **no** sample `.vi` files — `samples/` is `.gitignore`d and nothing is
committed or packaged. Test VIs are pulled on demand from their upstreams via
[`scripts/pull_samples.sh`](../scripts/pull_samples.sh). This file is the
canonical, vetted list of sources.

## Selection criterion

A source is eligible iff it has a **verified, OSI-approved permissive license**
(MIT, BSD-2/3-Clause, Apache-2.0, 0BSD) confirmed from the repo's **actual
LICENSE file** — *not* a badge or README prose. **Author does not matter**: NI's
own repos that NI published under MIT/BSD/Apache are fine (NI is the copyright
holder and granted the license). What is excluded is NI's *proprietary* example
VIs (bundled with LabVIEW under the EULA / all-rights-reserved) and any repo with
no license (all-rights-reserved by default).

Using a licensed VI as a **test input** does not affect lvkit's clean-room
provenance (see [`PROVENANCE.md`](../PROVENANCE.md)) — the VI is data we parse,
not a source of the format/codegen knowledge.

## Sources (all license-verified)

### Currently in use
| Source | License | Notes |
| --- | --- | --- |
| OpenG Toolkit | BSD-3-Clause | 17 individual `oglib_*.vip` library packages (each a zip) fetched anonymously from SourceForge's `opengtoolkit` project → unzipped → `scripts/reproduce_openg_corpus.py` runs `lvkit.extractor.extract_llb`/`extract_vi_xml`. **Version note**: SourceForge's public listing tops out at each library's `4.x` branch (e.g. `oglib_string-4.2.0.13.vip`) — newer `5.x`/`6.x` releases exist on vipm.io but are gated behind an account login and aren't anonymously/scriptably reproducible, so they are NOT used here. |
| JKISoftware/JKI-EasyXML | BSD-3-Clause | cases, nested dataflow, clusters, variants; flat-sequence + stacked-sequence renderer fixtures (`Fast Parser/test TCX read (installed 71).vi`) |
| JKISoftware/JKI-VI-Tester | BSD-3-Clause | LVOOP / project-plugin |
| mefistotelis/pylabview (test VIs) | MIT | the parser's own corpus |
| KL-Turner/LabVIEW-DAQ | MIT | DAQ app |
| LabVIEW-DCAF/DAQModule | Apache-2.0 | single-point DAQ |
| ni/measurement-plugin-labview | MIT | **1337 .vi / 34 .lvclass / 55 .lvlib / 423 .ctl** — broadest single source: LVOOP, libraries, typedefs, deep hierarchies |
| illuminated-g/lv-flex-channel-examples | MIT | `DAQmx AO/DAQ AO.vi` (+ .lvlib/.ctl) — the corpus's real DAQmx caller (poly-variant extraction, LIbd/BDHP vilib qualified-path parsing, frame-attributed constants, diff fixtures) |
| ismet55555/LabVIEW-OOP-Classes | MIT | 186 .vi / 11 .lvclass / 21 .ctl — compact LVOOP across DAQ/hardware/utility |

### NI, permissively licensed (MIT unless noted) — author is NI, license is real
| Source | License | Content | Good for |
| --- | --- | --- | --- |
| NI-Measurement-Plug-Ins (org, ~20 repos) | MIT | measurement plugins (adc, dac, pmic, dsa, scope/fgen, best-practices-labview, abstraction-layer-labview, …) | instrument/measurement domain, error clusters, driver calls |
| ni/actor-framework | MIT | actor framework | dynamic dispatch, events, deep OOP |
| ni/grpc-labview | MIT | gRPC stack | large real codebase |
| ni/niveristand-* (many) | MIT | VeriStand custom devices | LVOOP, FPGA, message libraries |

### Community, permissively licensed
| Source | License | Content | Good for |
| --- | --- | --- | --- |
| gitlab wovalab/…/labview-doc-generator (Antidoc) | BSD-3-Clause | 304 .vi / 16 .lvclass / 6 .lvlib | mature LVOOP + libraries |
| plasmapper/* (20 repos) | MIT | instrument drivers, 9–98 .vi each | state machines, LVOOP, error clusters |
| SilverLabUCL/SilverLab-Microscope-Software | Apache-2.0 | ~2516 .vi incl. FPGA | broadest coverage; **~242 MB** (heavy) |
| PositroniumSpectroscopy/oskar | BSD-3-Clause | sequencer/acquisition | sequences |
| LabVIEW-Open-Source/Advanced-Data-Structures | 0BSD | circular buffers | shift registers |

### DAQmx callers (for terminal-order validation) — permissive
| Source | License | Content |
| --- | --- | --- |
| illuminated-g/lv-flex-channel-examples | MIT | `DAQmx AO/DAQ AO.vi` (+ .lvlib/.ctl) — real DAQmx-calling VI; **currently in use** (see above) |
| illuminated-g/lv-example-EventBaseDaq | MIT | event-based DAQ VIs |
| timstreeter/DAQmx-Bus | BSD-3-Clause | DAQmx bus |

**Known gap**: no permissive Digital-Output DAQmx caller is currently usable.
`DAQ AO.vi` above calls Analog Output (`AO Voltage` / `Sample Clock` / `Analog
1D Wfm` write) — it never exercises the digital `do_channels`/boolean-write/
`Wait (ms)` codegen path the old (unlicensed) `DAQmx-Digital-IO/In.vi` did, and
it can't `build_module()` end-to-end (calls a project-local `Read Event.vi`
unreachable via any shippable search path, plus its AO poly variants have no
`vilib_resolver.json` mapping yet). `ismet55555/LabVIEW-OOP-Classes`' Digital
Output caller (`DAQ/Digital Output/DO_class/utils/DO_Write.vi`, MIT) IS the
right construct but can't be parsed at all — pylabview raises `AttributeError:
'TDObjectCluster' object has no attribute 'getNumRepeats'` on its VITS block
(a pylabview bug). The full-codegen DAQmx-driver tests that depended on the
digital I/O mapping were dropped (see `tests/test_e2e_codegen.py`,
`tests/test_driver_codegen.py`, `tests/test_parallel_codegen.py` — each has a
comment documenting exactly what was dropped and why). Re-evaluate if a
working Digital-Output permissive caller turns up.

## Rejected (do not use)
| Source | Reason |
| --- | --- |
| DAQmx-Digital-IO (Maxim-91/Digital-I-O-Control-based-on-NI-DAQmx-in-LabVIEW) | **no LICENSE → all-rights-reserved**; removed from the corpus and from every test (see "Known gap" above for what replaced it) |
| ni/actor-framework — *earlier* rejected as "NI-authored" | reinstated: license (MIT) is what matters, not author |
| DarioArzaba/LabVIEWInstrumentation | MIT claimed in README only, **no LICENSE file** |
| LabViewProjects/LabView | no license |
| NI proprietary example VIs (shipped with LabVIEW) | EULA / all-rights-reserved |
| vipm.io-hosted OpenG `5.x`/`6.x` package versions | real content, but downloads are gated behind an account login — not anonymously reproducible by `pull_samples.sh` |
