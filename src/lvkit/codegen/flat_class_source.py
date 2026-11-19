"""The Python that flattens an object of a LabVIEW class and reads it back.

NI, "Flattened Data" (docs-be.ni.com/api/bundle/labview/page/flattened-data.html),
"Classes": a class is flattened as four fields, ``level in hierarchy`` ("4-byte
unsigned integer representing the number of levels into the class hierarchy the
class occurs ... if this value is 2, the class has one ancestor class between
itself and LabVIEW Object. Note If level in hierarchy is 0, the object is an
instance of LabVIEW Object, and LabVIEW excludes the other three fields from the
string"), ``class name`` ("A sequence of Pascal strings preceded by a single byte
that indicates the length of the qualified name of the class ... The last Pascal
string is a length byte of zero ... enough zero pad bytes to increase the class
name section to a multiple of 4 bytes"), ``version list`` ("one cluster of four
2-byte unsigned integers per level; the first is the version of class name, the
second is the version of its parent ... Note If level in hierarchy is 1 and the
version is 0, the flattened data represents the default data of the class, and
LabVIEW excludes the remaining field from the string") and ``private data`` ("A
series of flattened clusters representing the private data of each level of the
hierarchy. In reverse order of the version list, the first cluster in this series
corresponds to the oldest ancestor class. Each flattened cluster begins with a
4-byte signed integer that represents the number of bytes in the data that
follows. If this initial number is 0, the flattened cluster represents the default
data for the corresponding class"). labviewwiki.org, "LabVIEW Object", gives the
same layout with worked examples (the 2-level ``Alpha.lvclass`` string).

The bytes of the sample corpus agree (``docs/_internal/design/
labview-binary-format.md``): a cluster's stored count is exactly the cluster's
bytes, with no pad after it (a 73-byte cluster is followed directly by the next
field), though the pages say a cluster is padded; this writes none.

Each generated class names itself with ``__lv_class_name__`` (the qualified name),
and the module's table of classes (``classes``) gives, for each class a value can
be, its module and class name, its version, the attributes its private data is
stored in and the functions that flatten and read the cluster of its own level.

The errors are NI's, "LabVIEW Object-Oriented Programming Error Codes"
(docs-be.ni.com/api/bundle/labview-api-ref/page/errors/
labview-object-oriented-programming-error-codes.html): 1401 "The version of the
class currently in memory is older than the version of the data", 1403 "The data
is corrupt. LabVIEW could not interpret the data as any valid flattened LabVIEW
class", 1527 "a LabVIEW class that is not currently loaded into LabVIEW".

The version of a level is read and compared with the class's, but never changes the
value: LabVIEW mutates data of an older version, and a class of this package has
the fields it has.
"""

from __future__ import annotations

FLATTEN_CLASS = """def $NAME(value):
    cls = type(value)
    qualified = getattr(cls, '__lv_class_name__', None)
    if qualified is None:
        return struct.pack('$ORDERI', 0)
    chain = [
        c.__lv_class_name__
        for c in reversed(cls.__mro__)
        if '__lv_class_name__' in vars(c)
    ]
    section = b''.join(
        bytes([len(raw)]) + raw
        for raw in (part.encode('latin-1') for part in qualified.split(':'))
    ) + b'\\x00'
    section = bytes([len(section)]) + section
    section += b'\\x00' * (-len(section) % 4)
    versions = b''
    private = b''
    for name in reversed(chain):
        versions += struct.pack('$ORDERHHHH', *$TABLE[name][2])
    for name in chain:
        entry = $TABLE[name]
        data = entry[4](tuple(getattr(value, a) for a in entry[3]))
        private += struct.pack('$ORDERi', len(data)) + data
    return struct.pack('$ORDERI', len(chain)) + section + versions + private
"""

UNFLATTEN_CLASS = """def $NAME(data, pos):
    def fail(code, message):
        error = FlattenedClassError(message)
        error.code = code
        return error
    (levels,) = struct.unpack_from('$ORDERI', data, pos)
    pos += 4
    if levels == 0:
        return object(), pos
    try:
        length = data[pos]
        content = data[pos + 1:pos + 1 + length]
        parts = []
        cursor = 0
        while content[cursor]:
            size = content[cursor]
            parts.append(content[cursor + 1:cursor + 1 + size].decode('latin-1'))
            cursor += 1 + size
        pos += 1 + length + (-(1 + length) % 4)
        versions = [
            struct.unpack_from('$ORDERHHHH', data, pos + 8 * k) for k in range(levels)
        ]
    except (IndexError, struct.error):
        raise fail(1403, 'the flattened class has no valid name or version list')
    pos += 8 * levels
    qualified = ':'.join(parts)
    entry = $TABLE.get(qualified)
    if entry is None:
        raise fail(1527, 'the class ' + repr(qualified) + ' is not in the package')
    value = getattr(importlib.import_module(entry[0], __package__), entry[1])()
    chain = [
        c.__lv_class_name__
        for c in reversed(type(value).__mro__)
        if '__lv_class_name__' in vars(c)
    ]
    if levels != len(chain) or (
        levels > 1 and not all(any(v) for v in versions)
    ):
        raise fail(
            1403,
            'the flattened ' + qualified + ' has ' + str(levels)
            + ' levels of hierarchy and the class has ' + str(len(chain)),
        )
    if any(v > $TABLE[n][2] for v, n in zip(versions, reversed(chain))):
        raise fail(
            1401,
            'the data of ' + qualified + ' is of a newer version than the class has',
        )
    if levels == 1 and not any(versions[0]):
        return value, pos
    for name in chain:
        entry = $TABLE[name]
        try:
            (size,) = struct.unpack_from('$ORDERi', data, pos)
            pos += 4
            if size:
                values, end = entry[5](data, pos)
                if end != pos + size:
                    raise struct.error('the private data has another size')
                for attribute, field in zip(entry[3], values):
                    setattr(value, attribute, field)
                pos = end
        except (IndexError, struct.error):
            raise fail(1403, 'the private data of ' + name + ' does not read')
    return value, pos
"""
