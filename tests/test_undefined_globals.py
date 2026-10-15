"""Every global name a function loads must exist in its module (or builtins).

A renamed helper that leaves a stale call behind ('_local_today' vs 'local_today') only fails at
run time, inside whichever try/except swallows it ("could not save the comparison to History:
NameError", empty typeahead). This scans the compiled code of the modules we hit most."""
import builtins, dis, importlib, types

MODULES = ("perception.db", "perception.analyzer", "perception.pipeline", "perception.pdf", "perception.deep_pdf",
           "perception.practice_analyzer", "perception.location_resolver", "perception.physician_resolver", "server")


def _code_objects(co):
    yield co
    for c in co.co_consts:
        if isinstance(c, types.CodeType):
            yield from _code_objects(c)


def _undefined(mod) -> set[str]:
    bad = set()
    names = set(vars(mod)) | set(dir(builtins))
    for obj in list(vars(mod).values()):
        fn = getattr(obj, "__wrapped__", obj)
        co = getattr(fn, "__code__", None)
        if co is None or getattr(fn, "__module__", None) != mod.__name__:
            continue
        for c in _code_objects(co):
            for ins in dis.get_instructions(c):
                if ins.opname in ("LOAD_GLOBAL", "LOAD_NAME") and ins.argval not in names:
                    bad.add(f"{fn.__name__}: {ins.argval}")
    return bad


def test_modules_load_only_defined_globals():
    problems = {}
    for name in MODULES:
        mod = importlib.import_module(name)
        bad = _undefined(mod)
        if bad:
            problems[name] = sorted(bad)
    assert not problems, problems
