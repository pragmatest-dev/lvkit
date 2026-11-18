"""Every view shows the tags a VI saves (``graph/describe.py``'s ``## Tags``, the
``tags`` of the JSON)."""

from __future__ import annotations

from tests.error_diagrams import Project, Vi
from tests.error_diagrams import project as project  # noqa: F401  (the fixture)
from tests.test_saved_tags import LOCALIZED, SOURCE_ONLY, VITS_HEX


def test_the_vits_block_beside_the_main_xml_reaches_every_view(
    project: Project,  # noqa: F811
) -> None:
    project.add(Vi("Tagged.vi"))
    slot = project.base / "_xml" / "Tagged_vi"
    (slot / "vi_VITS.bin").write_bytes(bytes.fromhex(VITS_HEX))
    views = project.views("Tagged.vi")
    assert (
        "## Tags\n"
        '  Localized: String = "Don\'t Localize"\n'
        "  NI.LV.All.SourceOnly: TF = True\n"
    ) in views.describe
    assert views.json["tags"] == [
        {
            "name": LOCALIZED,
            "type": "String",
            "value": '"Don\'t Localize"',
            "undecoded": None,
        },
        {"name": SOURCE_ONLY, "type": "TF", "value": "True", "undecoded": None},
    ]


def test_a_vi_that_saves_no_tags_shows_none(
    project: Project,  # noqa: F811
) -> None:
    project.add(Vi("Plain.vi"))
    views = project.views("Plain.vi")
    assert "## Tags" not in views.describe
    assert "tags" not in views.json
