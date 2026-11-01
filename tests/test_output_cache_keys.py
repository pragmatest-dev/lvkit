"""The render/diff output cache key covers every option that changes the built
bytes, and a render is stale once a SubVI it read changed (#131)."""

from __future__ import annotations

from pathlib import Path

from lvkit import output_cache
from lvkit.build_options import BuildOptions
from lvkit.load_mode import LoadMode

V = "0.0.0"


def _vi(path: Path, content: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _project_vi(tmp_path: Path) -> Path:
    (tmp_path / "repo" / ".git").mkdir(parents=True, exist_ok=True)
    return _vi(tmp_path / "repo" / "Main.vi", b"main")


def test_every_build_option_changes_the_render_key(tmp_path: Path) -> None:
    base = BuildOptions()
    variants = [
        BuildOptions(vilib_root=tmp_path / "vi.lib"),
        BuildOptions(userlib_root=tmp_path / "user.lib"),
        BuildOptions(instrlib_root=tmp_path / "instr.lib"),
        BuildOptions(mode=LoadMode.NONE),
        BuildOptions(mode=LoadMode.FULL),
        BuildOptions(project_store=tmp_path / ".lvkit"),
    ]
    tags = {output_cache.render_options_tag("svg", "light", None, b) for b in variants}
    tags.add(output_cache.render_options_tag("svg", "light", None, base))
    assert len(tags) == len(variants) + 1
    diff_tags = {
        output_cache.diff_options_tag("text", False, None, None, b) for b in variants
    }
    diff_tags.add(output_cache.diff_options_tag("text", False, None, None, base))
    assert len(diff_tags) == len(variants) + 1


def test_a_changed_build_option_is_a_miss(tmp_path: Path) -> None:
    vi = _project_vi(tmp_path)
    plain = output_cache.render_options_tag("svg", "light", None, BuildOptions())
    no_vilib = output_cache.render_options_tag(
        "svg", "light", None, BuildOptions(vilib_root=tmp_path / "vi.lib")
    )
    output_cache.store_render(vi, "svg", plain, V, "BODY")
    assert output_cache.lookup_render(vi, "svg", plain, V) == "BODY"
    assert output_cache.lookup_render(vi, "svg", no_vilib, V) is None


def test_a_changed_subvi_is_a_miss(tmp_path: Path) -> None:
    vi = _project_vi(tmp_path)
    sub = _vi(tmp_path / "repo" / "Sub.vi", b"sub v1")
    gone = tmp_path / "repo" / "Missing.vi"
    opts = output_cache.render_options_tag("svg", "light", None)
    output_cache.store_render(vi, "svg", opts, V, "BODY", dependencies=[sub, gone])
    assert output_cache.lookup_render(vi, "svg", opts, V) == "BODY"
    sub.write_bytes(b"sub v2, different size")
    assert output_cache.lookup_render(vi, "svg", opts, V) is None


def test_an_unresolved_subvi_that_appears_is_a_miss(tmp_path: Path) -> None:
    vi = _project_vi(tmp_path)
    later = tmp_path / "repo" / "Later.vi"
    opts = output_cache.render_options_tag("svg", "light", None)
    output_cache.store_render(vi, "svg", opts, V, "BODY", dependencies=[later])
    assert output_cache.lookup_render(vi, "svg", opts, V) == "BODY"
    _vi(later, b"now present")
    assert output_cache.lookup_render(vi, "svg", opts, V) is None


def test_a_same_content_touch_of_a_subvi_stays_a_hit(tmp_path: Path) -> None:
    vi = _project_vi(tmp_path)
    sub = _vi(tmp_path / "repo" / "Sub.vi", b"sub")
    opts = output_cache.render_options_tag("svg", "light", None)
    output_cache.store_render(vi, "svg", opts, V, "BODY", dependencies=[sub])
    sub.write_bytes(b"sub")  # same bytes, new mtime
    assert output_cache.lookup_render(vi, "svg", opts, V) == "BODY"


def test_a_changed_subvi_is_a_miss_for_a_diff(tmp_path: Path) -> None:
    before = _vi(tmp_path / "before.vi", b"before")
    after = _project_vi(tmp_path)
    sub = _vi(tmp_path / "repo" / "Sub.vi", b"sub v1")
    opts = output_cache.diff_options_tag("text", False, None, None)
    output_cache.store_diff(before, after, "text", opts, V, "D", dependencies=[sub])
    assert output_cache.lookup_diff(before, after, "text", opts, V) == "D"
    sub.write_bytes(b"sub v2, different size")
    assert output_cache.lookup_diff(before, after, "text", opts, V) is None
