"""Read-only semantic views of script wrappers and nested tool responses."""
from __future__ import annotations

import ast
import json
import re
import shlex

from .details import Span, section, subsection
from .paging import content_spans, json_spans


# Tokenize strings/comments before looking for calls, so examples in strings
# cannot be mistaken for tool invocations. This is not a JavaScript evaluator.
TOKENS = re.compile(r'''\s+|//[^\n]*|/\*[\s\S]*?\*/|"(?:\\[\s\S]|[^"\\])*"|'(?:\\[\s\S]|[^'\\])*'|`(?:\\[\s\S]|[^`\\])*`|[A-Za-z_$][\w$]*|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|[^\s]''')


def literal_calls(script):
    """Extract only complete literal argument objects; never guess expressions.

    JSON and simple JS object literals (unquoted keys / single-quoted strings)
    are supported. Variables, spreads, templates and expressions are left in
    the original script. Multiple occurrences retain their source order.
    """
    tokens = [m.group() for m in TOKENS.finditer(script)
              if not m.group().isspace() and not m.group().startswith(("//", "/*"))]

    def literal(pos, depth=0):
        if depth > 40 or pos >= len(tokens):
            raise ValueError("not a bounded literal")
        token = tokens[pos]
        if token in {"{", "["}:
            obj = {} if token == "{" else []
            close = "}" if token == "{" else "]"
            pos += 1
            while tokens[pos] != close:
                if isinstance(obj, dict):
                    key = tokens[pos]
                    if key.startswith(('"', "'")):
                        key, pos = literal(pos, depth + 1)
                    elif re.fullmatch(r"[A-Za-z_$][\w$]*", key):
                        pos += 1
                    else:
                        raise ValueError("computed key")
                    if tokens[pos] != ":" or key in obj:
                        raise ValueError("not a plain object")
                    value, pos = literal(pos + 1, depth + 1)
                    obj[key] = value
                else:
                    value, pos = literal(pos, depth + 1)
                    obj.append(value)
                if tokens[pos] == close:
                    break
                if tokens[pos] != ",":
                    raise ValueError("expression")
                pos += 1
            return obj, pos + 1
        if token.startswith('"'):
            return json.loads(token), pos + 1
        if token.startswith("'"):
            # Only a single quoted token reaches literal_eval, never script.
            # Reject JS-specific or ambiguous escapes instead of changing them.
            if re.search(r"\\(?![\\'\"nrtbf/]|u[0-9a-fA-F]{4})", token):
                raise ValueError("unsupported string escape")
            return ast.literal_eval(token.replace(r"\/", "/")), pos + 1
        if token in {"true", "false", "null"} or re.fullmatch(r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?", token):
            return json.loads(token), pos + 1
        raise ValueError("dynamic argument")

    for n in range(len(tokens) - 4):
        if tokens[n:n + 2] != ["tools", "."] or tokens[n + 3:n + 5] != ["(", "{"]:
            continue
        if n and tokens[n - 1] in {".", "?."}:
            continue
        try:
            arguments, end = literal(n + 4)
            if tokens[end] == ")":
                yield tokens[n + 2], arguments
        except (ValueError, SyntaxError, IndexError, RecursionError):
            continue


def wrapper_script(item, data):
    name = str(item.metadata.get("tool_name") or data.get("name") or "").rsplit(".", 1)[-1]
    if name == "exec" or item.action == "tool_script" or item.metadata.get("protocol_wrapper"):
        value = data.get("input", item.detail)
        if isinstance(value, str):
            return value
    return None


def result_rows(value, depth=0, color=0):
    """Unpack recorded JSON envelopes, never unescape arbitrary plain text."""
    if depth < 32 and isinstance(value, str) and value.lstrip().startswith(("{", "[")):
        try:
            parsed = json.loads(value)
        except (ValueError, RecursionError):
            pass
        else:
            yield from result_rows(parsed, depth + 1, color)
            return
    if depth >= 32:
        yield json_spans(value)  # Full fallback, not truncation.
    elif isinstance(value, list):
        if not value:
            yield json_spans(value)
        for n, child in enumerate(value, 1):
            yield subsection(f"BLOK {n}", 6)
            yield from result_rows(child, depth + 1, color)
            yield []
    elif isinstance(value, dict):
        if not value:
            yield json_spans(value)
        text_block = value.get("type") in {"input_text", "output_text", "text"} and "text" in value
        if text_block:
            yield subsection(f"TEKST · {value['type']}")
            yield from result_rows(value["text"], depth + 1, color)
        # Metadata first, then streams. Never infer a missing exit code or
        # turn transport success into success of a nested command.
        streams = {"output", "stdout", "stderr", "aggregated_output", "formatted_output", "error", "content", "result"}
        for key, child in value.items():
            if key in streams or (text_block and key in {"text", "type"}):
                continue
            tint = (3 if child == 0 else 7) if key == "exit_code" else 5
            if isinstance(child, (dict, list)):
                yield subsection(key)
                yield json_spans(child)
            else:
                yield [Span(key + ": ", 2), Span(json.dumps(child, ensure_ascii=False), tint)]
        for key, child in value.items():
            if key in streams:
                yield []
                yield subsection(key.upper(), 7 if key in {"stderr", "error"} else 2)
                yield from result_rows(child, depth + 1, 7 if key in {"stderr", "error"} else color)
    else:
        if value == "":
            yield [Span('"" (pusty tekst)', 6)]
        else:
            yield content_spans(value, "plain", color)


def wrapper_rows(item, script):
    yield section("WRAPPER · " + str(item.metadata.get("tool_name") or "exec"))
    yield [Span("Podgląd zapisanych argumentów, nie dowód wykonania wywołań.", 6)]
    found = False
    for n, (name, arguments) in enumerate(literal_calls(script), 1):
        found = True
        yield []
        yield section(f"WEJŚCIE {n} · tools.{name}")
        command = arguments.get("cmd", arguments.get("command"))
        if name in {"exec_command", "shell", "shell_command", "bash"} and isinstance(command, (str, list)):
            yield subsection("POLECENIE")
            yield content_spans(shlex.join(map(str, command)) if isinstance(command, list) else command, "code", 1)
            rest = {key: value for key, value in arguments.items() if key not in {"cmd", "command"}}
            if rest:
                yield subsection("PARAMETRY WYKONANIA")
                yield json_spans(rest)
        else:
            yield subsection("ARGUMENTY")
            yield json_spans(arguments)
    if not found:
        yield [Span("Argumenty dynamiczne lub nierozpoznane — bez zgadywania; pełny skrypt niżej.", 4)]
    yield []
    yield section("WYNIK WRAPPERA / ZAPISANE ODPOWIEDZI")
    if item.result:
        yield from result_rows(item.result)
    else:
        yield [Span("Brak zapisanego wyniku.", 6)]
    yield []
    yield section("SKRYPT WRAPPERA — PEŁNY ORYGINAŁ", 5)
    yield [Span("Literały zachowują zapis źródłowy. Polecenia powyżej są zdekodowanym podglądem.", 6)]
    yield content_spans(script, "code", 1)
