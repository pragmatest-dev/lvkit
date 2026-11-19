"""The Python that flattens a Picture and reads it back.

The flattened form of a Picture is its stored form: a 32-bit length and then that
many bytes of records, each a big-endian ``u16`` operation number, a ``u32``
payload length and the payload (``lvkit.picture_records``, deduced from the
corpus; the byte order option sets the length prefix, the records are read as
stored). The value in Python is a ``PIL.Image.Image`` in mode ``RGBA``, ``None``
for the empty picture (``picture_values``).

* Empty (``None``, or an image with no pixels) is the length 0.
* A Picture of one copy pixmap record (operation 29: a 24 byte header and the
  24-bit pixels of a square image) reads back as its image; any other drawing
  operation raises where it runs, naming the operations.
* A drawn image is not written: operation 29 has 10 header bytes that are not
  decoded, so a record that LabVIEW would read back cannot be made. It raises
  where it runs, naming the image's size.

Each template is the source of one module-level function; ``{name}`` is its
generated name and ``{order}`` the ``struct`` byte-order prefix.
"""

from __future__ import annotations

PICTURE_IMPORTS = ("from PIL import Image",)

FLATTEN_PICTURE = """def {name}(value):
    if value is None or value.width * value.height == 0:
        return struct.pack({order!r} + 'i', 0)
    raise NotImplementedError(
        "writing a drawn Picture in LabVIEW's flattened form is not supported"
        " (an image of " + str(value.width) + " x " + str(value.height)
        + " pixels)"
    )
"""

UNFLATTEN_PICTURE = """def {name}(data, pos):
    size = struct.unpack_from({order!r} + 'i', data, pos)[0]
    pos += 4
    if size < 0 or pos + size > len(data):
        raise struct.error('a Picture runs past the end of the data')
    stream = data[pos:pos + size]
    records = []
    offset = 0
    while offset < size:
        if offset + 6 > size:
            raise struct.error('a Picture record header runs past the Picture')
        opcode, length = struct.unpack_from('>HI', stream, offset)
        offset += 6
        if offset + length > size:
            raise struct.error('a Picture record runs past the Picture')
        records.append((opcode, stream[offset:offset + length]))
        offset += length
    if not records:
        return None, pos + size
    others = [str(opcode) for opcode, _ in records if opcode != 29]
    if others:
        raise NotImplementedError(
            'the drawing operation ' + ', '.join(others)
            + ' of a Picture is not decoded'
        )
    if len(records) != 1:
        raise NotImplementedError(
            'a Picture of ' + str(len(records)) + ' copy pixmap records: where'
            ' each is placed is not confirmed'
        )
    payload = records[0][1]
    if len(payload) < 24:
        raise NotImplementedError(
            'a copy pixmap record of ' + str(len(payload))
            + ' bytes is short of its 24 byte header'
        )
    count, _, width, height, depth = struct.unpack_from('>IIHHH', payload, 0)
    if (
        depth != 24
        or width != height
        or count != width * height * 3
        or len(payload) - 24 != count
    ):
        raise NotImplementedError(
            'a copy pixmap record of ' + str(width) + ' x ' + str(height)
            + ' pixels at ' + str(depth) + ' bits holding '
            + str(len(payload) - 24) + ' bytes is not read'
        )
    image = Image.frombytes('RGB', (width, height), payload[24:])
    return image.convert('RGBA'), pos + size
"""
