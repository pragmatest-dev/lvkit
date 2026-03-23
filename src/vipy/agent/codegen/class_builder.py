"""Build Python class AST from LabVIEW class (lvclass)."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from vipy.type_defaults import _is_class_refnum, _is_error_cluster

from .ast_utils import parse_expr, to_function_name, to_var_name
from .builder import build_args, generate_body, CodeGenContext

if TYPE_CHECKING:
    from vipy.memory_graph import InMemoryVIGraph
    from ...structure import LVClass, LVMethod


@dataclass
class ClassConfig:
    """Configuration for class generation."""

    include_docstrings: bool = True
    use_dataclass: bool = False  # Use @dataclass for private data
    private_prefix: str = "__"  # Name-mangled prefix for LabVIEW private
    protected_prefix: str = "_"  # Single underscore for LabVIEW protected


class ClassBuilder:
    """Build Python class AST from LabVIEW class.

    Handles:
    - Class definition with inheritance
    - __init__ from private data
    - Instance methods (with self)
    - Static methods (@staticmethod)
    - Properties from getter/setter pairs
    - Visibility mapping (public, protected, private)
    """

    def __init__(
        self,
        config: ClassConfig | None = None,
    ) -> None:
        self.config = config or ClassConfig()
        self._method_contexts: dict[str, dict[str, Any]] = {}
        self._import_resolver: Any = None

    def build_class_module(
        self,
        lvclass: LVClass,
        method_contexts: dict[str, dict[str, Any]] | None = None,
        parent_class_name: str | None = None,
        vi_context_lookup: Any = None,
        import_resolver: Any = None,
        graph: InMemoryVIGraph | None = None,
    ) -> ast.Module:
        """Build complete module with class definition.

        Args:
            lvclass: Parsed LVClass object
            method_contexts: Dict mapping method name to VI context
            parent_class_name: Parent class name (overrides lvclass.parent_class)
            vi_context_lookup: Deprecated, ignored. Kept for API compatibility.
                              Terminal names are populated on Terminal objects
                              via resolve_name().
            import_resolver: Callable to resolve import paths for SubVIs

        Returns:
            AST Module with imports and class definition
        """
        self._method_contexts = method_contexts or {}
        self._import_resolver = import_resolver
        self._graph = graph
        self._collected_imports: set[str] = set()
        parent = parent_class_name or lvclass.parent_class

        module_body: list[ast.stmt] = []

        # Build class definition (collects imports from method bodies)
        class_def = self._build_class_def(lvclass, parent)

        # Build imports (static + collected from methods)
        module_body.extend(self._build_imports(lvclass, parent))
        module_body.append(class_def)

        module = ast.Module(body=module_body, type_ignores=[])

        # Run optimizer (dead code, unreachable, duplicate imports)
        from .ast_optimizer import optimize_module
        module = optimize_module(module)

        return module

    def _build_imports(
        self,
        lvclass: LVClass,
        parent_class_name: str | None,
    ) -> list[ast.stmt]:
        """Build import statements for the module."""
        imports: list[ast.stmt] = []

        # Future annotations
        imports.append(
            ast.ImportFrom(
                module="__future__",
                names=[ast.alias(name="annotations", asname=None)],
                level=0,
            )
        )

        # Common imports
        common = [
            "from pathlib import Path",
            "from typing import Any, ClassVar",
        ]
        for imp in common:
            try:
                tree = ast.parse(imp)
                imports.extend(tree.body)
            except SyntaxError:
                pass

        # Parent class import
        if parent_class_name:
            parent_module = to_var_name(parent_class_name.replace(".lvclass", ""))
            parent_class = self._to_class_name(parent_class_name)
            imports.append(
                ast.ImportFrom(
                    module=f".{parent_module}",
                    names=[ast.alias(name=parent_class, asname=None)],
                    level=0,
                )
            )

        # Add imports collected from method body generation
        for imp_str in sorted(self._collected_imports):
            try:
                tree = ast.parse(imp_str)
                imports.extend(tree.body)
            except SyntaxError:
                pass

        return imports

    def _build_class_def(
        self,
        lvclass: LVClass,
        parent_class_name: str | None,
    ) -> ast.ClassDef:
        """Build the class definition AST node."""
        class_name = self._to_class_name(lvclass.name)

        # Base classes
        bases: list[ast.expr] = []
        if parent_class_name:
            parent_class = self._to_class_name(parent_class_name)
            bases.append(ast.Name(id=parent_class, ctx=ast.Load()))

        # Class body
        body: list[ast.stmt] = []

        # Docstring
        if self.config.include_docstrings:
            body.append(
                ast.Expr(
                    value=ast.Constant(
                        value=f"Converted from LabVIEW class: {lvclass.name}."
                    )
                )
            )

        # __init__ method
        init_method = self._build_init(lvclass, parent_class_name)
        body.append(init_method)

        # Group methods by type for ordering
        static_methods: list[LVMethod] = []
        accessors: list[LVMethod] = []
        public_methods: list[LVMethod] = []
        protected_methods: list[LVMethod] = []
        private_methods: list[LVMethod] = []

        for method in lvclass.methods:
            if method.is_accessor:
                accessors.append(method)
            elif method.scope == "public":
                public_methods.append(method)
            elif method.scope == "protected":
                protected_methods.append(method)
            else:  # private
                private_methods.append(method)

        # Build properties from accessor pairs
        property_defs = self._build_properties(accessors)
        body.extend(property_defs)

        # Separate truly static methods from instance methods
        # A method is an instance method if it has a class-typed input wire
        actual_static: list[LVMethod] = []
        actual_instance: list[LVMethod] = []
        for method in public_methods + protected_methods + private_methods:
            vi_ctx = self._method_contexts.get(method.name, {})
            has_class_wire = any(
                self._is_self_input(inp, lvclass.name)
                for inp in vi_ctx.get("inputs", [])
            )
            if has_class_wire:
                actual_instance.append(method)
            else:
                actual_static.append(method)

        # Build static methods (no class wire input)
        for method in actual_static:
            method_def = self._build_static_method(method, lvclass.name)
            body.append(method_def)

        # Build instance methods (have class wire input)
        for method in actual_instance:
            # Skip constructor-like methods (handled in __init__)
            if self._is_constructor(method.name):
                continue
            # Add scope prefix for non-public methods
            prefix = ""
            if method.scope == "protected":
                prefix = self.config.protected_prefix
            elif method.scope == "private":
                prefix = self.config.private_prefix
            try:
                method_def = self._build_instance_method(
                    method, lvclass.name, prefix=prefix,
                )
                body.append(method_def)
            except Exception:
                # Method codegen failed (e.g. unresolved terminals) — skip
                pass

        # Ensure body is not empty
        if not body:
            body.append(ast.Pass())

        return ast.ClassDef(
            name=class_name,
            bases=bases,
            keywords=[],
            body=body,
            decorator_list=[],
        )

    def _build_init(
        self,
        lvclass: LVClass,
        parent_class_name: str | None,
    ) -> ast.FunctionDef:
        """Build __init__ method from private data fields."""
        body: list[ast.stmt] = []

        # Call parent __init__ if there's a parent class
        if parent_class_name:
            body.append(
                ast.Expr(
                    value=ast.Call(
                        func=ast.Attribute(
                            value=ast.Call(
                                func=ast.Name(id="super", ctx=ast.Load()),
                                args=[],
                                keywords=[],
                            ),
                            attr="__init__",
                            ctx=ast.Load(),
                        ),
                        args=[],
                        keywords=[],
                    )
                )
            )

        # Initialize private data fields as instance attributes
        # Accessor scope determines visibility:
        #   public accessor or no accessor → self.field
        #   protected accessor → self._field
        #   private accessor → self.__field
        accessor_scopes: dict[str, str] = {}
        for m in lvclass.methods:
            if m.is_accessor and m.accessor_field:
                key = to_var_name(m.accessor_field)
                # Use most restrictive scope if multiple accessors
                if key not in accessor_scopes or m.scope != "public":
                    accessor_scopes[key] = m.scope

        for field in lvclass.private_data_fields:
            # Skip placeholder/invalid field names
            if not field.name or field.name.lower() == "none":
                continue

            var_name = to_var_name(field.name)
            scope = accessor_scopes.get(var_name, "public")

            if scope == "private":
                attr_name = "__" + var_name
            elif scope == "protected":
                attr_name = "_" + var_name
            else:
                attr_name = var_name

            body.append(
                ast.Assign(
                    targets=[
                        ast.Attribute(
                            value=ast.Name(id="self", ctx=ast.Load()),
                            attr=attr_name,
                            ctx=ast.Store(),
                        )
                    ],
                    value=self._get_default_for_type(field.python_type),
                )
            )

        # Ensure body is not empty
        if not body:
            body.append(ast.Pass())

        return ast.FunctionDef(
            name="__init__",
            args=ast.arguments(
                posonlyargs=[],
                args=[ast.arg(arg="self", annotation=None)],
                vararg=None,
                kwonlyargs=[],
                kw_defaults=[],
                kwarg=None,
                defaults=[],
            ),
            body=body,
            decorator_list=[],
            returns=ast.Constant(value=None),
        )

    def _get_default_for_type(self, python_type: str) -> ast.expr:
        """Get default value AST for a Python type."""
        type_defaults = {
            "str": ast.Constant(value=""),
            "int": ast.Constant(value=0),
            "float": ast.Constant(value=0.0),
            "bool": ast.Constant(value=False),
            "list": ast.List(elts=[], ctx=ast.Load()),
            "dict": ast.Dict(keys=[], values=[]),
            "Path": ast.Call(
                func=ast.Name(id="Path", ctx=ast.Load()),
                args=[],
                keywords=[],
            ),
        }
        return type_defaults.get(python_type, ast.Constant(value=None))

    def _is_simple_accessor(
        self,
        method: LVMethod,
    ) -> bool:
        """Check if accessor VI is simple (just unbundle/bundle + error handling).

        Simple accessors only contain case structures and unbundle/bundle operations.
        They translate to direct attribute access, not @property.

        Complex accessors contain additional operations (math, SubVI calls, validation).
        They need @property with backing field.

        Without VI context, we assume all accessors are simple. This can be
        refined when method_contexts is provided.
        """
        vi_context = self._method_contexts.get(method.name, {})
        if not vi_context:
            # No VI context - assume simple accessor
            return True

        operations = vi_context.get("operations", [])
        if not operations:
            # No operations - simple accessor
            return True

        # Check if all operations are just unbundle/bundle/case/error handling
        simple_node_types = {"select", "case", "unbundle", "bundle", "nMux", "nDmux"}
        simple_prim_ids = {
            1340,  # Unbundle
            1302,  # Bundle
            2075,  # Merge Errors
            2076,  # Clear Error
        }

        for op in operations:
            node_type = getattr(op, "node_type", "") or ""
            prim_id = getattr(op, "primResID", 0) or 0

            if node_type in simple_node_types:
                continue
            if prim_id in simple_prim_ids:
                continue

            # Found a non-simple operation
            return False

        return True

    def _build_properties(
        self,
        accessors: list[LVMethod],
    ) -> list[ast.stmt]:
        """Build @property and @setter from getter/setter pairs.

        For SIMPLE accessors (just unbundle/bundle + error handling):
        - Do NOT generate @property
        - The field is already created in __init__ with correct visibility
        - The accessor VI logic is just LabVIEW error handling idiom

        For COMPLEX accessors (have real logic):
        - Generate @property with private backing field (_field)
        - The property can contain computed logic
        """
        property_stmts: list[ast.stmt] = []

        # Group by field name
        by_field: dict[str, dict[str, LVMethod]] = {}
        for acc in accessors:
            if acc.accessor_field:
                field = acc.accessor_field
                if field not in by_field:
                    by_field[field] = {}
                if acc.accessor_type:
                    by_field[field][acc.accessor_type] = acc

        # Build property definitions only for complex accessors
        for field, acc_dict in by_field.items():
            getter = acc_dict.get("getter")
            setter = acc_dict.get("setter")

            # Check if any accessor is complex
            is_complex = False
            if getter and not self._is_simple_accessor(getter):
                is_complex = True
            if setter and not self._is_simple_accessor(setter):
                is_complex = True

            if not is_complex:
                # Simple accessors - skip property generation
                # Field visibility is already set correctly in __init__
                continue

            prop_name = to_var_name(field)
            # Complex accessor - always use private backing field
            backing_field = "_" + prop_name

            # Build getter
            if getter:
                getter_body: list[ast.stmt] = [
                    ast.Return(
                        value=ast.Attribute(
                            value=ast.Name(id="self", ctx=ast.Load()),
                            attr=backing_field,
                            ctx=ast.Load(),
                        )
                    )
                ]

                getter_def = ast.FunctionDef(
                    name=prop_name,
                    args=ast.arguments(
                        posonlyargs=[],
                        args=[ast.arg(arg="self", annotation=None)],
                        vararg=None,
                        kwonlyargs=[],
                        kw_defaults=[],
                        kwarg=None,
                        defaults=[],
                    ),
                    body=getter_body,
                    decorator_list=[ast.Name(id="property", ctx=ast.Load())],
                    returns=ast.Name(id="Any", ctx=ast.Load()),
                )
                property_stmts.append(getter_def)

            # Build setter
            if setter:
                setter_body: list[ast.stmt] = [
                    ast.Assign(
                        targets=[
                            ast.Attribute(
                                value=ast.Name(id="self", ctx=ast.Load()),
                                attr=backing_field,
                                ctx=ast.Store(),
                            )
                        ],
                        value=ast.Name(id="value", ctx=ast.Load()),
                    )
                ]

                setter_def = ast.FunctionDef(
                    name=prop_name,
                    args=ast.arguments(
                        posonlyargs=[],
                        args=[
                            ast.arg(arg="self", annotation=None),
                            ast.arg(
                                arg="value",
                                annotation=ast.Name(id="Any", ctx=ast.Load()),
                            ),
                        ],
                        vararg=None,
                        kwonlyargs=[],
                        kw_defaults=[],
                        kwarg=None,
                        defaults=[],
                    ),
                    body=setter_body,
                    decorator_list=[
                        ast.Attribute(
                            value=ast.Name(id=prop_name, ctx=ast.Load()),
                            attr="setter",
                            ctx=ast.Load(),
                        )
                    ],
                    returns=ast.Constant(value=None),
                )
                property_stmts.append(setter_def)

        return property_stmts

    def _build_static_method(
        self,
        method: LVMethod,
        class_name: str,
    ) -> ast.FunctionDef:
        """Build a static method."""
        func_name = to_function_name(method.name)
        vi_context = self._method_contexts.get(method.name, {})

        # Extract inputs/outputs from VI context
        inputs = vi_context.get("inputs", [])
        outputs = vi_context.get("outputs", [])

        # Filter out class instance input (even for "static" methods in LabVIEW)
        filtered_inputs = [
            inp for inp in inputs if not self._is_self_input(inp, class_name)
        ]

        # Use existing build_args() - handles types and error filtering
        args_obj = build_args(filtered_inputs)

        # Generate method body from operations
        operations = vi_context.get("operations", [])
        ctx = CodeGenContext.from_vi_context(vi_context, graph=self._graph)
        ctx.import_resolver = self._import_resolver
        body = generate_body(operations, ctx)
        self._collected_imports.update(ctx.imports)

        # Ensure non-empty body
        if not body:
            body = [ast.Pass()]

        # Build return annotation - filter error clusters and class output
        filtered_outputs = [
            out for out in outputs
            if not self._is_error_output(out) and not self._is_self_output(out, class_name)
        ]

        returns = self._build_return_annotation(filtered_outputs)

        return ast.FunctionDef(
            name=func_name,
            args=args_obj,
            body=body,
            decorator_list=[ast.Name(id="staticmethod", ctx=ast.Load())],
            returns=returns,
        )

    def _build_instance_method(
        self,
        method: LVMethod,
        class_name: str,
        prefix: str = "",
    ) -> ast.FunctionDef:
        """Build an instance method.

        Args:
            method: The LVMethod to convert
            class_name: Name of the containing class (for self parameter detection)
            prefix: Prefix for method name (e.g., "_" for protected, "__" for private)
        """
        func_name = prefix + to_function_name(method.name)
        vi_context = self._method_contexts.get(method.name, {})

        # Extract inputs/outputs from VI context
        inputs = vi_context.get("inputs", [])
        outputs = vi_context.get("outputs", [])

        # Find the class instance input by TYPE (becomes self)
        instance_input = None
        for inp in inputs:
            if self._is_self_input(inp, class_name):
                instance_input = inp
                break

        # Filter out the class-typed input (becomes self)
        filtered_inputs = [
            inp for inp in inputs if not self._is_self_input(inp, class_name)
        ]

        # Use existing build_args() for proper types and error filtering
        args_obj = build_args(filtered_inputs)

        # Prepend self
        args_obj.args.insert(0, ast.arg(arg="self", annotation=None))

        # Generate method body from operations
        operations = vi_context.get("operations", [])
        ctx = CodeGenContext.from_vi_context(vi_context, graph=self._graph)
        ctx.import_resolver = self._import_resolver
        body = generate_body(operations, ctx)
        self._collected_imports.update(ctx.imports)

        # Get instance variable name from context bindings (not from input name!)
        instance_var_name = None
        if instance_input and instance_input.id:
            instance_var_name = ctx.resolve(instance_input.id)

        # Transform instance variable references to self
        if instance_var_name:
            body = self._transform_instance_to_self(body, instance_var_name)

        # Ensure non-empty body
        if not body:
            body = [ast.Pass()]

        # Build return annotation - filter error clusters and class output
        filtered_outputs = [
            out for out in outputs
            if not self._is_error_output(out) and not self._is_self_output(out, class_name)
        ]

        returns = self._build_return_annotation(filtered_outputs)

        return ast.FunctionDef(
            name=func_name,
            args=args_obj,
            body=body,
            decorator_list=[],
            returns=returns,
        )

    def _transform_instance_to_self(
        self, body: list[ast.stmt], instance_var: str
    ) -> list[ast.stmt]:
        """Transform references to instance variable into self.

        Walks the AST and replaces Name nodes matching instance_var with 'self'.
        """
        class InstanceToSelfTransformer(ast.NodeTransformer):
            def visit_Name(self, node: ast.Name) -> ast.AST:
                if node.id == instance_var:
                    return ast.Name(id="self", ctx=node.ctx)
                return node

        transformer = InstanceToSelfTransformer()
        return [transformer.visit(stmt) for stmt in body]

    def _is_self_input(self, inp: Any, class_name: str) -> bool:
        """Check if input is the class instance (becomes self).

        Uses lv_type to detect class refnums by type, not name.
        """
        lv_type = getattr(inp, "lv_type", None)
        if lv_type and _is_class_refnum(lv_type, class_name):
            return True
        return False

    def _is_self_output(self, out: Any, class_name: str) -> bool:
        """Check if output is the class instance (filtered from return).

        Uses lv_type to detect class refnums by type, not name.
        """
        lv_type = getattr(out, "lv_type", None)
        if lv_type and _is_class_refnum(lv_type, class_name):
            return True
        return False

    def _is_error_output(self, out: Any) -> bool:
        """Check if output is an error cluster (should not be in return).

        Python uses exceptions instead of error clusters.
        """
        # Check by lv_type if available
        lv_type = getattr(out, "lv_type", None)
        if lv_type and _is_error_cluster(lv_type):
            return True

        # Fallback: check name pattern
        out_name = (out.name if hasattr(out, "name") else str(out)).lower()
        if "error" in out_name and ("in" in out_name or "out" in out_name):
            return True

        return False

    def _build_return_annotation(self, outputs: list[Any]) -> ast.expr:
        """Build return type annotation from outputs using lv_type."""
        if not outputs:
            return ast.Constant(value=None)

        if len(outputs) == 1:
            out = outputs[0]
            lv_type = getattr(out, "lv_type", None)
            if lv_type:
                return parse_expr(lv_type.to_python())
            return ast.Name(id="Any", ctx=ast.Load())

        # Multiple outputs - tuple
        elts = []
        for out in outputs:
            lv_type = getattr(out, "lv_type", None)
            if lv_type:
                elts.append(parse_expr(lv_type.to_python()))
            else:
                elts.append(ast.Name(id="Any", ctx=ast.Load()))

        return ast.Subscript(
            value=ast.Name(id="tuple", ctx=ast.Load()),
            slice=ast.Tuple(elts=elts, ctx=ast.Load()),
            ctx=ast.Load(),
        )

    def _is_constructor(self, method_name: str) -> bool:
        """Check if a method is a constructor-like method."""
        constructor_patterns = [
            "init",
            "new",
            "create",
            "construct",
        ]
        name_lower = method_name.lower()
        return any(p in name_lower for p in constructor_patterns)

    def _to_class_name(self, name: str) -> str:
        """Convert name to PascalCase class name.

        Preserves existing capitalization patterns (e.g., TestCase -> TestCase).
        """
        name = name.replace(".lvclass", "").replace(".LVCLASS", "")

        # If already looks like PascalCase (has uppercase letters), preserve it
        if any(c.isupper() for c in name) and not name.isupper():
            # Just remove spaces/dashes/underscores
            return name.replace("-", "").replace("_", "").replace(" ", "")

        # Convert from snake_case or kebab-case to PascalCase
        words = name.replace("-", " ").replace("_", " ").split()
        return "".join(word.capitalize() for word in words) or "LVClass"


def build_class(
    lvclass: LVClass,
    method_contexts: dict[str, dict[str, Any]] | None = None,
    parent_class_name: str | None = None,
    config: ClassConfig | None = None,
) -> str:
    """Build Python code from a LabVIEW class.

    Convenience function that creates a ClassBuilder and builds the module.

    Args:
        lvclass: Parsed LVClass object
        method_contexts: Dict mapping method name to VI context
        parent_class_name: Parent class name (overrides lvclass.parent_class)
        config: Optional ClassConfig

    Returns:
        Python source code as string
    """
    builder = ClassBuilder(config=config)
    module = builder.build_class_module(
        lvclass,
        method_contexts=method_contexts,
        parent_class_name=parent_class_name,
    )
    ast.fix_missing_locations(module)
    return ast.unparse(module)
