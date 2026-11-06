"""pylabview reads a VI's PNG image block without a call Pillow deprecates.

pylabview's ``ImageBlock.parseRSRCSectionData`` (used by ``PNGI`` blocks) called
``Image.getdata()``, which Pillow deprecates (removed in Pillow 14). lvkit's
patch 11 (``_pylabview_patches.py``) reads the image with ``Image.load()``. The
test records every warning while a VI with a ``PNGI`` block (icon-editor
``lv_icon.vi``) is extracted: pylabview catches an error raised inside a block,
so a warnings-as-errors filter would not show the call.
"""

from pathlib import Path

import pytest

from lvkit._pylabview_patches import install_pylabview_patches
from lvkit.extractor import _extract_in_process

_SAMPLES = Path(__file__).resolve().parent.parent / ".lvkit" / "cache" / "samples"
_VI = _SAMPLES / "ni-labview-icon-editor" / "resource" / "plugins" / "lv_icon.vi"

pytestmark = pytest.mark.needs_samples


def test_a_png_image_block_is_read_without_the_deprecated_getdata(
    tmp_path: Path, recwarn: pytest.WarningsRecorder
) -> None:
    install_pylabview_patches()
    _extract_in_process(_VI, tmp_path, _VI.stem)
    deprecated = [w for w in recwarn if "getdata is deprecated" in str(w.message)]
    assert deprecated == []
    assert list(tmp_path.glob("*.png"))
