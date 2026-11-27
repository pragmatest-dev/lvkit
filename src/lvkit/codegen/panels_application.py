"""The application the generated VIs run in, as the generated ``panels`` module holds
it: ``application``, the one object every Application reference is, and
``loaded_vis()``, Application:All VIs In Memory.

A Python program has one application and no LabVIEW projects or targets. So
``application`` is the one object that Owning Application (a VI reference's), the
default Application and App.DefaultApplication give; its ``context_name`` is ``''``
and ``all_contexts`` is ``[application]`` (NI's All VIs In Memory page: "LabVIEW
creates a new application instance each time you open a LabVIEW project or
target"); its ``name`` is Application:Name, "Filename of the application"
(application/appname.html), the program's own file name.

The VIs in memory (application/allvisinmemory.html: "Returns a list of VIs in memory
for the specified application instance") are the VI functions the program has
imported: ``loaded_vis()`` finds them in ``sys.modules`` by the ``vi_name`` each
carries and gives them sorted by that name; a VI reference is the function itself.
"""

APPLICATION_NAME = "application"
LOADED_VIS_NAME = "loaded_vis"

APPLICATION_SOURCE = '''

class Application:
    """The program the VIs run in: the one application every Application reference
    holds."""

    context_name = ""

    @property
    def name(self) -> str:
        """Application:Name: the program's own file name."""
        return os.path.basename(sys.argv[0] or sys.executable)

    @property
    def all_contexts(self) -> list[Application]:
        """App.AllContexts: a program has the one context."""
        return [self]


application = Application()


def loaded_vis() -> list[Any]:
    """Application:All VIs In Memory: the VI functions imported in this program,
    sorted by ``vi_name``, each once."""
    found: dict[int, Any] = {}
    for module in list(sys.modules.values()):
        for member in list(vars(module).values()):
            if isinstance(member, types.FunctionType) and isinstance(
                getattr(member, "vi_name", None), str
            ):
                found[id(member)] = member
    return sorted(found.values(), key=lambda function: function.vi_name)
'''
