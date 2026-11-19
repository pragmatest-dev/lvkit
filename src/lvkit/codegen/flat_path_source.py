"""The Python that flattens a path to LabVIEW's ``PTH0`` form and reads it back.

NI's "Flattened Data" page: "paths ... a flattened 32-bit integer that records the
length in bytes precedes the flattened form ... This length is preceded by four
characters: PTH0. For example, a path with value C:\\File is flattened to 5054 4830
0000 000B 0000 0002 0143 0466 696C 65. 5054 4830 indicates PTH0. 0000 000B
indicates 11 bytes total. 0000 is the type. 0002 is the number of components. 0143
indicates the letter C as a Pascal string. 0466 696C 65 indicates the word File as a
Pascal string." The 11 bytes are the type, the count and the components. The type is
NI's "How LabVIEW Stores Data in Memory": "0 for an absolute path, 1 for a relative
path, and 3 for a Universal Naming Convention (UNC) path", and 2 for Not A Path (NI's
Path Type function has the three kinds absolute, relative and Not A Path, 0, 1 and 2;
every saved Not A Path in the sample corpus is type 2 with no components).

A path value is Python's ``Path`` the way ``lvkit.path_value.python_path`` writes a
saved path: Not A Path is ``None``; an absolute path's first component is its volume
(``C`` is ``C:/`` on Windows and ``/C`` elsewhere); an empty component goes up a level
(``..``; the sample corpus has a relative path of one empty component, the ``..`` that
JKI's Get Method Path builds a path from); a path with no component is ``Path('')``;
and a path that starts at a symbolic LabVIEW folder (``<vilib>``) is a plain path whose
first component is that folder. A UNC path (type 3) has no Python value and raises
where it runs, naming the path.
"""

from __future__ import annotations

FLATTEN_PATH = """def {name}(value):
    if value is None:
        return b'PTH0' + struct.pack('{order}IHH', 4, 2, 0)
    value = Path(value)
    parts = list(value.parts)
    if not parts:
        kind, components = 0, []
    elif value.anchor:
        if os.name == 'nt':
            if len(value.drive) != 2 or not value.drive.endswith(':'):
                raise NotImplementedError(
                    'the path ' + str(value) + ' has no drive letter, so it has no '
                    'flattened form here (a UNC path is flattened as type 3)'
                )
            kind, components = 0, [value.drive[0]] + parts[1:]
        else:
            kind, components = 0, parts[1:]
    else:
        kind, components = 1, parts
        if parts and re.fullmatch(r'<[^<>]+>', parts[0]):
            kind = 0
    body = b''.join(
        bytes([len(name)]) + name
        for name in (os.fsencode('' if part == '..' else part) for part in components)
    )
    header = struct.pack('{order}IHH', 4 + len(body), kind, len(components))
    return b'PTH0' + header + body
"""

UNFLATTEN_PATH = """def {name}(data, pos):
    if data[pos:pos + 4] != b'PTH0':
        raise struct.error('a path does not start with PTH0')
    size, kind, count = struct.unpack_from('{order}IHH', data, pos + 4)
    end = pos + 8 + size
    if end > len(data):
        raise struct.error('a path runs past the end of the data')
    if kind == 2:
        return None, end
    cursor = pos + 12
    parts = []
    for _ in range(count):
        length = data[cursor]
        parts.append(os.fsdecode(data[cursor + 1:cursor + 1 + length]))
        cursor += 1 + length
    if kind == 3:
        raise NotImplementedError(
            'a flattened UNC path has no Python value: ' + repr(parts)
        )
    if kind not in (0, 1):
        raise ValueError('a flattened path has the invalid type ' + str(kind))
    names = ['..' if part == '' else part for part in parts]
    if not names:
        return Path(''), end
    if kind == 1 or re.fullmatch(r'<[^<>]+>', names[0]):
        return Path(*names), end
    if parts[0] == '':
        raise ValueError('a flattened absolute path has no volume name')
    volume = parts[0] + ':/' if os.name == 'nt' else '/' + parts[0]
    return Path(volume, *names[1:]), end
"""
