---
name: feedback_qualified_names
description: All user-defined types (classes, typedefs, controls) must use fully qualified names — bare filenames are ambiguous
type: feedback
---

All user-defined LabVIEW types — classes, typedefs, custom controls — are identified by their file path in the project hierarchy. The fully qualified name (with library ownership chain) IS the type identity. Two types can share the same short name but differ by namespace.

**Why:** `LibA.lvlib:Status.ctl` and `LibB.lvlib:Status.ctl` are different types. Bare filenames like `Status.ctl` are ambiguous.

**How to apply:** Every `lv_type.classname`, typedef reference, and custom control reference must carry the fully qualified name, not the bare filename. The VCTP in the XML only stores bare filenames — resolve bare → qualified at load time using the project/library structure, and store the qualified form everywhere. Generalize rules that work consistently across all type references, not special-case fixes per use site.
