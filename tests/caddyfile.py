# User intent: one reader for the shipped Caddyfile, so every deploy test (and the smoke render) asks the same question the same
# way for each site; a second hand-rolled regex per site is how the two sites drift apart (design 028 section 2.3).
from __future__ import annotations

import shlex
from dataclasses import dataclass, field


@dataclass
class Node:
    name: str
    args: list[str]
    children: list["Node"] = field(default_factory=list)

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()


def _tokens(line: str) -> list[str]:
    return shlex.split(line, posix=False)


def parse(text: str) -> list[Node]:
    """Top-level nodes of a Caddyfile: comments and blank lines dropped, one node per line, `{` opens children."""
    root = Node("", [])
    stack = [root]
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip() if not raw.strip().startswith("#") else ""
        if not line:
            continue
        if line == "}":
            stack.pop()
            continue
        opens = line.endswith("{")
        tokens = _tokens(line[:-1].strip() if opens else line)
        node = Node(tokens[0] if tokens else "", tokens[1:])
        stack[-1].children.append(node)
        if opens:
            stack.append(node)
    return root.children


def sites(text: str) -> dict[str, list[Node]]:
    """Site blocks by address, in file order. The global options block (an anonymous `{`) is skipped."""
    return {node.name: node.children for node in parse(text) if node.name and node.children}


def _walk(body: list[Node]):
    for node in body:
        yield from node.walk()


def directive(body: list[Node], name: str) -> list[str]:
    """Every `name ...` line at any depth of this site, as its argument text."""
    return [" ".join(node.args) for node in _walk(body) if node.name == name]


def matcher(body: list[Node], name: str) -> list[str]:
    """The paths of `@name path a b c`."""
    found = [node for node in _walk(body) if node.name == f"@{name}"]
    assert len(found) == 1, (name, found)
    assert found[0].args[0] == "path", found[0]
    return found[0].args[1:]


def upstreams(body: list[Node]) -> list[str]:
    return [node.args[0] for node in _walk(body) if node.name == "reverse_proxy"]


def subdirective(body: list[Node], path: str) -> list[str]:
    """Nested read: `reverse_proxy/transport/max_conns_per_host` gives the argument text of each such line."""
    *parents, leaf = path.split("/")
    first, *rest = parents
    level = [child for node in _walk(body) if node.name == first for child in node.children]
    for name in rest:
        level = [child for node in level if node.name == name for child in node.children]
    return [" ".join(node.args) for node in level if node.name == leaf]


def block(body: list[Node], name: str, *args: str) -> Node:
    """The one node `name args...` at any depth of this site (e.g. `@static`, or `handle @static`)."""
    found = [node for node in _walk(body) if node.name == name and node.args[:len(args)] == list(args)]
    assert len(found) == 1, (name, args, found)
    return found[0]
