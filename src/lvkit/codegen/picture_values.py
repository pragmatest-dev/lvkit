"""A LabVIEW Picture as a value of Pillow (``PIL.Image``).

A Picture wire in generated Python is a ``PIL.Image.Image`` in mode ``RGBA``; the
empty picture (what every Picture input holds when unwired, and what a Picture
constant holds) is ``None``, read as an image of no pixels. A Picture VI returns a
new image, never the one it was given: LabVIEW's picture in / new picture out
wires are values. The drawing rules below are what NI's pages state, and where a
page is silent, the rule chosen and where it was searched (nothing here is read
from LabVIEW itself):

* Coordinates: "Horizontal coordinates increase to the right, and vertical
  coordinates increase to the bottom" (NI, Draw Rectangle VI). The origin is the
  top-left pixel of the image, so the image has no pixels above or to the left of
  it; drawing there is clipped.
* Size: a Picture has no stored size, so the new image is as large as the old one
  or as large as what is drawn needs (the right and bottom edge of a rectangle,
  an oval, an image data cluster or text; the pixel after a point or a line's
  end), whichever is bigger. NI says only that Picture to Pixmap's rect "default
  is to return the entire image" (NI, Picture to Pixmap VI); the forum and wiki
  searches for how the entire image is bounded found nothing, so it is the
  rectangle from (0, 0) to the new image's size.
* A rectangle or oval ``rect`` is (left, top, right, bottom) with "the bottom and
  right edges of the rectangle bounds do not contain image pixels" (NI, Draw
  Flattened Pixmap VI): the pixels drawn are left to right - 1 and top to
  bottom - 1. A rect given right-to-left or bottom-to-top is put in order.
* A color is the number a color box holds: 0x00RRGGBB, and 0x01000000 for
  Transparent (labviewwiki.org/wiki/Color: "The 'transparent' color has the value
  0x01000000"), which draws transparent pixels.
* The pen is a (width, style) cluster. Width is drawn for outlines and lines;
  a dash style is drawn solid (Pillow has no dashed lines). The pen's position
  (set by Move Pen, moved by Draw Line) is ``image.info["pen"]``, Pillow's own
  dict; a new image starts at (0, 0). NI does not state that Draw Point or the
  shapes move the pen, so they leave it where it is.
* Text uses Pillow's built-in scalable font. NI's own fonts (Application, System,
  Dialog) and the font name, strikeout, italic, underline, outline, shadow and
  bold of a user-specified font are not reproduced; a user-specified font's size
  is used as the pixel size, any other font is 13 pixels.
* The "image data" cluster is the tuple (image type, image depth, image, mask,
  colors, (left, top, right, bottom)) of NI's Picture to Pixmap page. ``image`` is
  the pixels in raster order, every row padded to a 2-byte boundary ("The Draw
  Flattened Pixmap VI expects all images to be padded with a 2-byte boundary",
  NI), a 24-bit pixel as red, green, blue bytes, an 8-, 4- or 1-bit pixel an
  index (the first pixel in the high bits) into ``colors`` (0x00RRGGBB). ``mask``
  has one bit per pixel in raster order, the first pixel in the high bit of the
  first byte; a 0 bit is transparent and a missing bit is opaque. An empty
  ``colors`` for a depth below 24 is NI's default color palette, which NI does not
  publish: that case raises ``NotImplementedError`` when it runs. The templates
  write and read the tuple; a call whose image data wire has a typedef is given
  the typedef's attribute object instead (``nodes/picture.py``).

The Python of each VI is a template of ``{terminal}`` placeholders the catalog
call machinery fills (``nodes/picture.py``); it uses only ``PIL`` and builtins.
"""

from __future__ import annotations

from collections.abc import Mapping

IMPORTS = ("from PIL import Image, ImageDraw, ImageFont",)

# A color box number, as an (r, g, b, a) tuple of Pillow. The transparent color
# (0x01000000, the bit above the 24 color bits) has alpha 0.
_COLOR = (
    "_c = {color}\n"
    "_color = (_c >> 16 & 255, _c >> 8 & 255, _c & 255, 0 if _c >> 24 else 255)\n"
)

# The picture that was given, as an image, and a new one at least ``_w`` x ``_h``
# holding it. ``_w`` and ``_h`` are set by the template before this runs.
_GROW = (
    "_base = {picture} if {picture} is not None else Image.new('RGBA', (0, 0))\n"
    "{new_picture} = Image.new('RGBA', (max(_base.width, _w), max(_base.height, _h)))\n"
    "{new_picture}.paste(_base, (0, 0))\n"
    "{new_picture}.info['pen'] = _base.info.get('pen', (0, 0))\n"
    "_draw = ImageDraw.Draw({new_picture})\n"
)

_SHAPE = (
    "_l, _r = sorted(({rect}[0], {rect}[2]))\n"
    "_t, _b = sorted(({rect}[1], {rect}[3]))\n"
    "_w, _h = max(_r, 0), max(_b, 0)\n" + _COLOR + _GROW + "if _r > _l and _b > _t:\n"
    "    if {fill}:\n"
    "        _draw.SHAPE((_l, _t, _r - 1, _b - 1), fill=_color)\n"
    "    else:\n"
    "        _draw.SHAPE((_l, _t, _r - 1, _b - 1), outline=_color,"
    " width=max({pen}[0], 1))\n"
)

# --- The image data cluster ---------------------------------------------------

# Reads {image_data}: _depth, _w, _h, _colors, _mask, _rect and _rows (the pixel
# values of each row: 0xRRGGBB for 24 bits, else an index into _colors).
IMAGE_DATA_UNPACK = "_kind, _depth, _bytes, _mask, _colors, _rect = {image_data}\n"
IMAGE_DATA_PARAMS = ("image_data", "image_subset", "image_data_out")
_READ = (
    IMAGE_DATA_UNPACK + "_w = max(_rect[2] - _rect[0], 0)\n"
    "_h = max(_rect[3] - _rect[1], 0)\n"
    "if _depth not in (1, 4, 8, 24):\n"
    "    raise NotImplementedError('image depth ' + str(_depth)"
    " + ' is not one of 1, 4, 8 or 24 bits')\n"
    "_stride = (_w * _depth + 15) // 16 * 2\n"
    "_data = bytes(_bytes)\n"
    "_rows = []\n"
    "for _y in range(_h):\n"
    "    _line = _data[_y * _stride:(_y + 1) * _stride].ljust(_stride, b'\\0')\n"
    "    _row = []\n"
    "    for _x in range(_w):\n"
    "        if _depth == 24:\n"
    "            _row.append(_line[3 * _x] << 16 | _line[3 * _x + 1] << 8"
    " | _line[3 * _x + 2])\n"
    "        elif _depth == 8:\n"
    "            _row.append(_line[_x])\n"
    "        else:\n"
    "            _bit = _x * _depth\n"
    "            _row.append(_line[_bit >> 3] >> (8 - _depth - (_bit & 7))"
    " & (1 << _depth) - 1)\n"
    "    _rows.append(_row)\n"
)

# _flags: for each pixel in raster order, whether its mask bit is set (opaque).
_READ_MASK = (
    "_flags = [_i >> 3 >= len(_mask) or bool(_mask[_i >> 3] >> (7 - (_i & 7)) & 1)"
    " for _i in range(_w * _h)]\n"
)

# _out: the image data bytes of _rows at _depth; _packed_mask: the mask bytes of
# _flags (empty when every pixel is opaque).
_PACK = (
    "_stride = (_w * _depth + 15) // 16 * 2\n"
    "_out = bytearray()\n"
    "for _row in _rows:\n"
    "    _line = bytearray(_stride)\n"
    "    for _x, _v in enumerate(_row):\n"
    "        if _depth == 24:\n"
    "            _line[3 * _x:3 * _x + 3] = bytes((_v >> 16 & 255, _v >> 8 & 255,"
    " _v & 255))\n"
    "        elif _depth == 8:\n"
    "            _line[_x] = _v\n"
    "        else:\n"
    "            _bit = _x * _depth\n"
    "            _line[_bit >> 3] |= (int(_v) & (1 << _depth) - 1)"
    " << (8 - _depth - (_bit & 7))\n"
    "    _out += _line\n"
)
_PACK_MASK = (
    "_packed_mask = bytearray()\n"
    "if not all(_flags):\n"
    "    _packed_mask = bytearray((len(_flags) + 7) // 8)\n"
    "    for _i, _f in enumerate(_flags):\n"
    "        if _f:\n"
    "            _packed_mask[_i >> 3] |= 0x80 >> (_i & 7)\n"
)

# _img: an RGBA image of _rows through _colors and _flags.
_TO_IMAGE = (
    "if _depth != 24 and not _colors:\n"
    "    raise NotImplementedError('the default LabVIEW color palette of a '"
    " + str(_depth) + '-bit image is not published by NI')\n"
    "_img = Image.new('RGBA', (_w, _h))\n"
    "_img.putdata([\n"
    "    ((_v if _depth == 24 else _colors[_v]) >> 16 & 255,\n"
    "     (_v if _depth == 24 else _colors[_v]) >> 8 & 255,\n"
    "     (_v if _depth == 24 else _colors[_v]) & 255,\n"
    "     255 if _flags[_y * _w + _x] else 0)\n"
    "    for _y, _row in enumerate(_rows) for _x, _v in enumerate(_row)])\n"
)

# _rows, _depth, _colors, _flags, _w, _h of the RGBA image _src (a transparent
# pixel is the background color _bg; depths below 24 quantize to a palette).
_FROM_IMAGE = (
    "_back = Image.new('RGBA', _src.size, (_bg >> 16 & 255, _bg >> 8 & 255,"
    " _bg & 255, 255))\n"
    "_back.alpha_composite(_src)\n"
    "_w, _h = _back.size\n"
    "_flags = []\n"
    "_colors = []\n"
    "if _depth == 24:\n"
    "    _raw = _back.tobytes()\n"
    "    _pixels = [_raw[_i] << 16 | _raw[_i + 1] << 8 | _raw[_i + 2]\n"
    "               for _i in range(0, len(_raw), 4)]\n"
    "else:\n"
    "    _small = _back.convert('RGB').quantize(colors=1 << _depth)\n"
    "    _palette = _small.getpalette() or []\n"
    "    _colors = [_palette[_i] << 16 | _palette[_i + 1] << 8 | _palette[_i + 2]\n"
    "               for _i in range(0, min(len(_palette), 3 << _depth), 3)]\n"
    "    _pixels = list(_small.tobytes())\n"
    "_rows = [_pixels[_y * _w:(_y + 1) * _w] for _y in range(_h)]\n"
)

# --- Text ------------------------------------------------------------------

# _tw, _th, _tmp: the text of {text} drawn in _tcolor on _tbg, turned by
# {text_orientation}; _left and _top: where its top-left corner goes for {origin}
# and {alignment}. NI orientations: 0 none, 1 stacked, 2 clockwise, 3 counter-
# clockwise.
_TEXT_BOX = (
    "_size = {user_specified_font}[1] if {desired_font} == 0 and"
    " {user_specified_font} and {user_specified_font}[1] > 0 else 13\n"
    "_font = ImageFont.load_default(_size)\n"
    "_text = '\\n'.join({text}) if {text_orientation} == 1 else {text}\n"
    "_box = ImageDraw.Draw(Image.new('RGBA', (1, 1))).multiline_textbbox("
    "(0, 0), _text, font=_font)\n"
    "_tw, _th = _box[2], _box[3]\n"
    "if {text_orientation} in (2, 3):\n"
    "    _tw, _th = _th, _tw\n"
    "_left = {origin}[0] - (0, _tw // 2, _tw)[{alignment}[0]]\n"
    "_top = {origin}[1] - (0, _th // 2, _th)[{alignment}[1]]\n"
)

_DRAW_TEXT = (
    _TEXT_BOX + "_c = {text_color}\n"
    "_tcolor = (_c >> 16 & 255, _c >> 8 & 255, _c & 255, 0 if _c >> 24 else 255)\n"
    "_c = {bg_color}\n"
    "_tbg = (_c >> 16 & 255, _c >> 8 & 255, _c & 255, 0 if _c >> 24 else 255)\n"
    "_tmp = Image.new('RGBA', (_box[2], _box[3]), _tbg)\n"
    "ImageDraw.Draw(_tmp).multiline_text((0, 0), _text, font=_font, fill=_tcolor)\n"
    "if {text_orientation} == 2:\n"
    "    _tmp = _tmp.rotate(-90, expand=True)\n"
    "elif {text_orientation} == 3:\n"
    "    _tmp = _tmp.rotate(90, expand=True)\n"
    "_w, _h = max(_left + _tw, 0), max(_top + _th, 0)\n"
    + _GROW
    + "{new_picture}.paste(_tmp, (_left, _top), _tmp)\n"
    "{text_rect} = (_left, _top, _left + _tw, _top + _th)\n"
)

_GET_TEXT_RECT = _TEXT_BOX + "{rect} = (_left, _top, _left + _tw, _top + _th)\n"

# --- The VIs ------------------------------------------------------------------

_FILE_READ = (
    "_img = Image.open({path_in}).convert('RGBA')\n"
    "_w, _h = _img.size\n"
    "_depth = 24\n"
    "_colors = []\n"
    "_raw = _img.tobytes()\n"
    "_pixels = [_raw[_i] << 16 | _raw[_i + 1] << 8 | _raw[_i + 2]\n"
    "           for _i in range(0, len(_raw), 4)]\n"
    "_rows = [_pixels[_y * _w:(_y + 1) * _w] for _y in range(_h)]\n"
)
_FILE_READ_OPAQUE_ALL = "_flags = [True] * (_w * _h)\n"
_FILE_READ_THRESHOLD = (
    "_flags = [_raw[_i + 3] >= {transparency_thresh}"
    " for _i in range(0, len(_raw), 4)]\n"
)
_FILE_READ_END = (
    _PACK
    + _PACK_MASK
    + "{image_data} = (0, _depth, list(_out), list(_packed_mask), _colors,"
    " (0, 0, _w, _h))\n"
    "{path} = {path_in}\n"
)

_FILE_WRITE_PREFIX = _READ + _READ_MASK + _TO_IMAGE + "{path} = {path_in}\n"


def _draw_pixmap(depth: str, colors: str) -> str:
    """Draw 2D array ``{data}`` of ``depth`` bits at ``{top_left}``; ``colors`` is
    the expression of its color table."""
    return (
        "_rows = [[int(_v) for _v in _r] for _r in {data}]\n"
        "_h = len(_rows)\n"
        "_w = len(_rows[0]) if _rows else 0\n"
        "_mask = list({mask})\n"
        "_flags = [_i >> 3 >= len(_mask) or bool(_mask[_i >> 3] >> (7 - (_i & 7)) & 1)"
        " for _i in range(_w * _h)]\n"
        f"_depth = {depth}\n"
        f"_colors = {colors}\n"
        + _TO_IMAGE
        + "_w, _h = max({top_left}[0] + _w, 0), max({top_left}[1] + _h, 0)\n"
        + _GROW
        + "{new_picture}.paste(_img, ({top_left}[0], {top_left}[1]), _img)\n"
    )


def _write(save: str) -> str:
    return _FILE_WRITE_PREFIX + save


TEMPLATES: Mapping[str, str] = {
    "PICTURE_EMPTY": "{empty_picture} = Image.new('RGBA', (0, 0))\n",
    "PICTURE_DRAW_RECT": _SHAPE.replace("SHAPE", "rectangle"),
    "PICTURE_DRAW_OVAL": _SHAPE.replace("SHAPE", "ellipse"),
    "PICTURE_DRAW_POINT": (
        "_x, _y = {point}\n"
        "_w, _h = max(_x + 1, 0), max(_y + 1, 0)\n"
        + _COLOR
        + _GROW
        + "if _x >= 0 and _y >= 0:\n"
        "    _draw.point((_x, _y), fill=_color)\n"
    ),
    "PICTURE_DRAW_LINE": (
        "_old = {picture} if {picture} is not None else Image.new('RGBA', (0, 0))\n"
        "_pen = _old.info.get('pen', (0, 0))\n"
        "_end = ({end_point}[0], {end_point}[1]) if {absolute_coordinates}"
        " else (_pen[0] + {end_point}[0], _pen[1] + {end_point}[1])\n"
        "_w = max(_pen[0], _end[0]) + 1\n"
        "_h = max(_pen[1], _end[1]) + 1\n"
        + _COLOR
        + _GROW
        + "_draw.line([_pen, _end], fill=_color, width=max({pen}[0], 1))\n"
        "{new_picture}.info['pen'] = _end\n"
    ),
    "PICTURE_MOVE_PEN": (
        "_base = {picture} if {picture} is not None else Image.new('RGBA', (0, 0))\n"
        "_pen = _base.info.get('pen', (0, 0))\n"
        "{new_picture} = _base.copy()\n"
        "{new_picture}.info['pen'] = ({new_position}[0], {new_position}[1])"
        " if {absolute_coordinates} else (_pen[0] + {new_position}[0],"
        " _pen[1] + {new_position}[1])\n"
    ),
    "PICTURE_DRAW_TEXT": _DRAW_TEXT,
    "PICTURE_GET_TEXT_RECT": _GET_TEXT_RECT,
    "PICTURE_TO_PIXMAP": (
        "_src = {picture} if {picture} is not None else Image.new('RGBA', (0, 0))\n"
        "_rect = {rect} if {rect} is not None else (0, 0, _src.width, _src.height)\n"
        "_src = _src.crop(tuple(_rect))\n"
        "_depth = {depth}\n"
        "if _depth not in (1, 4, 8, 24):\n"
        "    raise NotImplementedError('image depth ' + str(_depth)"
        " + ' is not one of 1, 4, 8 or 24 bits')\n"
        "_bg = {background_color}\n"
        + _FROM_IMAGE
        + _PACK
        + _PACK_MASK
        + "{image_data} = (0, _depth, list(_out), [], _colors,"
        " (_rect[0], _rect[1], _rect[0] + _w, _rect[1] + _h))\n"
        "{new_picture} = {picture}\n"
    ),
    "PICTURE_UNFLATTEN": (
        _READ + "{pixmap_1bit} = [[bool(_v) for _v in _r] for _r in _rows]"
        " if _depth == 1 else []\n"
        "{pixmap_4bit} = _rows if _depth == 4 else []\n"
        "{pixmap_8bit} = _rows if _depth == 8 else []\n"
        "{pixmap_24bit} = _rows if _depth == 24 else []\n"
        "{colors} = list(_colors)\n"
        "{top_left} = (_rect[0], _rect[1])\n"
        "{mask} = list(_mask)\n"
    ),
    "PICTURE_FLATTEN": (
        "_rows = {pixmap_24bit}\n"
        "_depth = 24\n"
        "for _candidate, _d in (({pixmap_8bit}, 8), ({pixmap_4bit}, 4),"
        " ({pixmap_1bit}, 1)):\n"
        "    if not _rows:\n"
        "        _rows, _depth = _candidate, _d\n"
        "_rows = [[int(_v) for _v in _r] for _r in _rows]\n"
        "_h = len(_rows)\n"
        "_w = len(_rows[0]) if _rows else 0\n"
        + _PACK
        + "{image_data} = (0, _depth, list(_out), list({mask}),"
        " list({colors}), ({top_left}[0], {top_left}[1],"
        " {top_left}[0] + _w, {top_left}[1] + _h))\n"
    ),
    "PICTURE_DRAW_FLATTENED": (
        _READ
        + _READ_MASK
        + _TO_IMAGE
        + "_w, _h = max(_rect[2], 0), max(_rect[3], 0)\n"
        + _GROW
        + "{new_picture}.paste(_img, (_rect[0], _rect[1]), _img)\n"
    ),
    "PICTURE_DRAW_TRUE_COLOR": _draw_pixmap("24", "[]"),
    "PICTURE_DRAW_8_BIT": _draw_pixmap("8", "list({color_table})"),
    "PICTURE_GET_IMAGE_SUBSET": (
        _READ
        + _READ_MASK
        + "_l = max(min({subset_rect}[0], {subset_rect}[2]), _rect[0])\n"
        "_t = max(min({subset_rect}[1], {subset_rect}[3]), _rect[1])\n"
        "_r = min(max({subset_rect}[0], {subset_rect}[2]), _rect[2])\n"
        "_b = min(max({subset_rect}[1], {subset_rect}[3]), _rect[3])\n"
        "_r, _b = max(_r, _l), max(_b, _t)\n"
        "_rows = [_row[_l - _rect[0]:_r - _rect[0]]"
        " for _row in _rows[_t - _rect[1]:_b - _rect[1]]]\n"
        "_flags = [_flags[(_y - _rect[1]) * _w + _x - _rect[0]]"
        " for _y in range(_t, _b) for _x in range(_l, _r)]\n"
        "_w, _h = _r - _l, _b - _t\n"
        + _PACK
        + _PACK_MASK
        + "{image_subset} = (_kind, _depth, list(_out), list(_packed_mask),"
        " list(_colors), (_l, _t, _r, _b))\n"
        "{true_subset_rect} = (_l, _t, _r, _b)\n"
    ),
    "PICTURE_CREATE_MASK": (
        _READ + _READ_MASK + "_hide = 1 if {mask_value_1bit} else 0\n"
        "_pixels = [_v for _r in _rows for _v in _r]\n"
        "if _depth == 1:\n"
        "    _hit = [_v == _hide for _v in _pixels]\n"
        "elif _depth == 24:\n"
        "    _hit = [_v == {mask_color} & 0xFFFFFF for _v in _pixels]\n"
        "else:\n"
        "    _hit = [_v < len(_colors) and _colors[_v] == {mask_color} & 0xFFFFFF"
        " for _v in _pixels]\n"
        "_kept = _flags if {combine_masks} else [True] * (_w * _h)\n"
        "_flags = [_k and not _x for _k, _x in zip(_kept, _hit)]\n"
        + _PACK_MASK
        + "{image_data_out} = (_kind, _depth, list(_bytes), list(_packed_mask),"
        " list(_colors), _rect)\n"
    ),
    "PICTURE_BITARRAY_TO_BYTEARRAY": (
        "_bits = [[bool(_b) for _b in _r] for _r in {bit_array}]\n"
        "{byte_array} = [sum((_r[_i + _k] if _i + _k < len(_r) else False) << (7 - _k)"
        " for _k in range(8)) for _r in _bits for _i in range(0, len(_r), 8)]\n"
    ),
    "PICTURE_READ_PNG": (_FILE_READ + _FILE_READ_THRESHOLD + _FILE_READ_END),
    "PICTURE_READ_BMP": (_FILE_READ + _FILE_READ_OPAQUE_ALL + _FILE_READ_END),
    "PICTURE_READ_JPEG": (_FILE_READ + _FILE_READ_OPAQUE_ALL + _FILE_READ_END),
    "PICTURE_WRITE_PNG": _write(
        "_img.save({path_in}, 'PNG', compress_level=6 if {compression} < 0"
        " else {compression})\n"
    ),
    "PICTURE_WRITE_BMP": _write("_img.convert('RGB').save({path_in}, 'BMP')\n"),
    "PICTURE_WRITE_JPEG": _write(
        "_img.convert('RGB').save({path_in}, 'JPEG', quality={quality})\n"
    ),
}

# The ops of the file VIs: the terminal that names the file (a placeholder of the
# template the call fills with the wired path).
FILE_PATH_PARAMS: Mapping[str, str] = {
    "PICTURE_READ_PNG": "path_to_png_file",
    "PICTURE_READ_BMP": "path_to_bmp_file",
    "PICTURE_READ_JPEG": "path_to_jpeg_file",
    "PICTURE_WRITE_PNG": "path_to_png_file",
    "PICTURE_WRITE_BMP": "path_to_bmp_file",
    "PICTURE_WRITE_JPEG": "path_to_jpeg_file",
}
