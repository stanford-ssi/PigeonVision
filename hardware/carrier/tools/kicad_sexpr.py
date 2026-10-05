"""Small S-expression reader for inspections and new KiCad objects.

Use edit_camera_buses.patch to preserve original bytes of existing objects.
"""
import json
import re


class Atom(str):
    pass


def parse(source):
    tokens = re.findall(r'"(?:\\.|[^"\\])*"|[()]|[^\s()]+', source)
    stack, root = [], None
    for token in tokens:
        if token == '(':
            node = []
            if stack:
                stack[-1].append(node)
            else:
                root = node
            stack.append(node)
        elif token == ')':
            stack.pop()
        else:
            stack[-1].append(json.loads(token) if token.startswith('"') else Atom(token))
    assert not stack
    return root


def nodes(node, name):
    return [x for x in node if isinstance(x, list) and x and x[0] == name]


def one(node, name):
    return nodes(node, name)[0]


def walk(node):
    if isinstance(node, list):
        yield node
        for child in node:
            yield from walk(child)


def dump(node):
    if isinstance(node, Atom):
        return str(node)
    if isinstance(node, str):
        return json.dumps(node, ensure_ascii=False)
    return '(' + ' '.join(dump(x) for x in node) + ')'
