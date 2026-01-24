# CodeFragment Pattern

Node generators return a `CodeFragment` dataclass, never modify context directly.

```python
@dataclass
class CodeFragment:
    statements: list[ast.stmt]  # AST nodes to emit
    bindings: dict[str, str]    # terminal_id -> variable_name
    imports: set[str]           # import statements needed
```

## Usage

```python
class MyCodeGen(NodeCodeGen):
    def generate(self, node: Operation, ctx: CodeGenContext) -> CodeFragment:
        # Read from ctx.resolve() - OK
        # ctx.bind() - DON'T DO THIS

        return CodeFragment(
            statements=[stmt],
            bindings={term_id: var_name},
            imports={"from x import y"},
        )
```

The builder merges fragments after generation:
```python
fragment = codegen.generate(node, ctx)
statements.extend(fragment.statements)
ctx.merge(fragment.bindings)  # Builder controls when bindings apply
ctx.imports.update(fragment.imports)
```

## Factory Methods
- `CodeFragment.empty()` - No-op node
- `CodeFragment.from_statement(stmt, bindings, imports)` - Single statement
