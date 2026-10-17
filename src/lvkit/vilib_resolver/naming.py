"""Python-name derivation helpers for vilib typedefs."""

from __future__ import annotations

from lvkit.type_names import derive_python_name


def derive_python_location(typedef_name: str) -> tuple[str, str]:
    """Derive Python package and class name from qualified name.

    The qualified name determines the package structure - types belong to
    their containing library, just like VIs do.

    Args:
        typedef_name: Qualified name like "sysdir.llb:System Directory Type.ctl"

    Returns:
        Tuple of (package_name, class_name) like ("sysdir", "SystemDirectoryType")
    """
    if ":" in typedef_name:
        container, filename = typedef_name.split(":", 1)
    else:
        container = ""
        filename = typedef_name

    # Container becomes package: "sysdir.llb" -> "sysdir"
    package = container.replace(".llb", "").replace(".lvlib", "")
    package = package.replace(".lvclass", "").lower()
    package = package.replace(" ", "_").replace("-", "_")

    # Filename becomes class name
    class_name = derive_python_name(filename)

    return (package, class_name)
