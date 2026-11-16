"""Site definitions for the site editor.

The editor holds a site as a JSON document with the same keys and SI values
as a BoreTHM site TOML file. Parsing, validation, the evidence tier and every
plotted profile come from the installed model: ``load_site``,
``validate_site`` and the returned profile's own methods.
"""

from dataclasses import asdict, fields, is_dataclass
import math
import os
from pathlib import Path
import re
import tempfile
import tomllib

import numpy as np

BUILT_IN = ("soultz", "larderello", "united-downs", "pannonian", "newberry")

# Top-level scalar keys in the order the editor writes them.
SCALAR_ORDER = (
    "name",
    "target_depth",
    "target_T",
    "target_depth_v11",
    "Sv_grad",
    "K0_min",
    "SHmax_over_Sv",
    "rho_fluid_grad",
    "k_rock",
    "E_rock",
    "UCS",
    "T_inj",
    "stress_basis",
    "strength_cases",
    "temperature_data_to",
    "mu",
    "coupled",
)
TABLE_ORDER = ("geotherm", "geotherm_v11", "stress_rule", "programme", "operations")
STRESS_CASE_ORDER = (
    "label",
    "regime",
    "Sv_grad",
    "Sv_int",
    "Shmin_grad",
    "Shmin_int",
    "SHmax_grad",
    "SHmax_int",
    "Pp_grad",
    "Pp_datum",
    "mu",
    "source",
    "z_data",
)
BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


def bases():
    from borethm import data_bases

    return list(data_bases())


def template_text(key):
    from borethm import site_text

    return site_text(key)


def templates():
    result = []
    for key in BUILT_IN:
        document = tomllib.loads(template_text(key))
        result.append({"key": key, "name": document.get("name", key)})
    return result


def document_from_toml(text):
    """Parse TOML text into a site document. Schema checks follow in check()."""
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise ValueError(f"TOML: {error}") from None


# ---------------------------------------------------------------- TOML writer
def _key(name):
    if BARE_KEY.match(name):
        return name
    return _string(name)


def _string(value):
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    escaped = "".join(
        ch if ord(ch) >= 0x20 and ord(ch) != 0x7F else f"\\u{ord(ch):04x}"
        for ch in escaped
    )
    return f'"{escaped}"'


def _float(value):
    if math.isnan(value):
        return "nan"
    if math.isinf(value):
        return "inf" if value > 0 else "-inf"
    text = repr(float(value))
    if "e" in text or "E" in text:
        mantissa, exponent = text.split("e")
        if "." not in mantissa:
            mantissa += ".0"
        return f"{mantissa}e{exponent}"
    return text


def _value(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return _float(value)
    if isinstance(value, str):
        return _string(value)
    if isinstance(value, (list, tuple)):
        items = [_value(item) for item in value]
        if any(isinstance(item, (list, tuple)) for item in value) and len(value) > 4:
            return "[\n" + "".join(f"    {item},\n" for item in items) + "]"
        return "[" + ", ".join(items) + "]"
    if isinstance(value, dict):
        return (
            "{"
            + ", ".join(f"{_key(k)} = {_value(v)}" for k, v in value.items())
            + "}"
        )
    raise ValueError(f"unsupported TOML value {type(value).__name__}")


def _is_table(value):
    return isinstance(value, dict)


def _is_table_array(value):
    return isinstance(value, list) and value and all(isinstance(x, dict) for x in value)


def _emit_table(lines, header, table, order=()):
    """Write one table. Values nested inside it are written inline."""
    keys = [k for k in order if k in table] + [k for k in table if k not in order]
    if header is not None:
        lines.append("")
        lines.append(header)
    for k in keys:
        lines.append(f"{_key(k)} = {_value(table[k])}")


def document_to_toml(document):
    """Write a site document as TOML. Values are written exactly as held."""
    lines = [
        "# BoreTHM site inputs written by BoreTHM Workbench.",
        "# Units: depths m; temperatures C; gradients Pa/m or C/m as named;",
        "# stresses and strengths Pa; conductivity W/(m K); ratios dimensionless.",
        "# Stress and pore pressure are gauge values relative to 0.1 MPa absolute.",
        "",
    ]
    order = [k for k in SCALAR_ORDER if k in document] + [
        k
        for k in document
        if k not in SCALAR_ORDER
        and k not in TABLE_ORDER
        and k not in ("stress_cases", "data_basis")
    ]
    for k in order:
        value = document[k]
        if _is_table(value) or _is_table_array(value):
            continue
        lines.append(f"{_key(k)} = {_value(value)}")
    for k in TABLE_ORDER:
        if k in document:
            _emit_table(lines, f"[{k}]", document[k])
    for k in order:
        if _is_table(document[k]):
            _emit_table(lines, f"[{_key(k)}]", document[k])
    for case in document.get("stress_cases", []) or []:
        lines.append("")
        lines.append("[[stress_cases]]")
        inner = []
        _emit_table(inner, None, case, STRESS_CASE_ORDER)
        lines.extend(inner)
    if "data_basis" in document:
        lines.append("")
        lines.append("[data_basis]")
        for k, pair in document["data_basis"].items():
            lines.append(f"{_string(k)} = {_value(list(pair))}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- loading
def _scratch():
    root = (
        Path(os.environ.get("BORETHM_WORKBENCH_DATA", ".workbench-data")).resolve()
        / "site-validation"
    )
    root.mkdir(parents=True, exist_ok=True)
    return root


def load_text(text):
    """Load TOML text with the model's load_site, naming the file site.toml."""
    from borethm import load_site

    with tempfile.TemporaryDirectory(prefix="site-", dir=_scratch()) as directory:
        path = Path(directory) / "site.toml"
        path.write_text(text, encoding="utf-8", newline="\n")
        try:
            return load_site(path)
        except ValueError as error:
            raise ValueError(str(error).replace(str(path), "site.toml")) from None


# ---------------------------------------------------------------- messages
def _schema_field(message):
    # load_site messages read "site.toml: key: message".
    parts = message.split(": ", 2)
    if len(parts) == 3 and parts[0] == "site.toml":
        key = parts[1]
        if key == "operations":
            # The model's parser names the key first; an unknown key belongs
            # to the table as a whole.
            from .programme import FIELDS

            name = parts[2].split(":", 1)[0].split("[", 1)[0].split(".", 1)[0]
            known = {field for field, _, _ in FIELDS} | {"sources"}
            return (f"operations.{name}" if name in known else "operations"), parts[2]
        return ("" if key in ("TOML", "file", "site") else key), parts[2]
    return "", message


def _stress_index(document, label):
    for index, case in enumerate(document.get("stress_cases", []) or []):
        if isinstance(case, dict) and case.get("label") == label:
            return f"stress_cases[{index}]"
    return "stress_rule" if "stress_rule" in document else "stress_cases"


def _validation_field(document, message):
    match = re.match(r"^(\w+) = ", message)
    if match:
        name = match.group(1)
        if name == "target_depth" and "target_depth_v11" in document:
            return "target_depth_v11"
        return name
    match = re.match(r"^(?:stress case) '(.*?)'", message) or re.match(
        r'^(?:stress case) "(.*?)"', message
    )
    if match:
        return _stress_index(document, match.group(1))
    match = re.match(r"^strength case ", message)
    if match:
        return "strength_cases"
    match = re.match(r"^data basis ['\"](.*?)['\"]", message)
    if match:
        return f"data_basis.{match.group(1)}"
    match = re.match(r"^no data basis for (.*?):", message)
    if match:
        return f"data_basis.{match.group(1)}"
    match = re.match(r"^no source stated for the rock's (\w+)", message)
    if match:
        return f"data_basis.{match.group(1)}"
    geotherm = "geotherm_v11" if "geotherm_v11" in document else "geotherm"
    if message.startswith("the geotherm gives"):
        return "target_T"
    if "temperature" in message:
        return geotherm
    return ""


def _split_validation(site_name, message):
    prefix = f"{site_name}: "
    if message.startswith(prefix):
        message = message[len(prefix) :]
    return [part for part in message.split("; ") if part]


# ---------------------------------------------------------------- profiles
def _profiles(site):
    """Sample the loaded site's own geotherm and stress methods for plotting."""
    target = float(site.target())
    deepest = max(target, float(site.temperature_data_to or 0.0))
    step = 25.0 if deepest <= 5000 else 50.0
    depth = np.unique(np.append(np.arange(0.0, deepest, step), [deepest]))
    geotherm = np.asarray(site.temperature()(depth), dtype=float)
    stress = []
    for case in site.stress_cases:
        stress.append(
            {
                "label": case.label,
                "Sv_Pa": [float(case.Sv(z)) for z in depth],
                "Shmin_Pa": [float(case.Shmin(z)) for z in depth],
                "SHmax_Pa": [float(case.SHmax(z)) for z in depth],
                "Pp_Pa": [float(x) for x in np.asarray(case.Pp(depth), dtype=float)],
                "z_data_m": list(case.z_data) if case.z_data is not None else None,
            }
        )
    return {
        "depth_m": depth.tolist(),
        "temperature_C": [
            float(x) if math.isfinite(x) else None for x in geotherm.tolist()
        ],
        "stress": stress,
        "target_depth_m": target,
        "target_T_C": float(site.target_T),
    }


def _missing_values(value, path=""):
    """Paths of empty entries: None, or an empty string where a number belongs."""
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _missing_values(item, f"{path}.{key}" if path else key)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _missing_values(item, f"{path}[{index}]")
    elif value is None:
        yield path


def check(document):
    """Write, load and validate a document with the model.

    Returns the TOML text, field-attached errors and warnings, the evidence
    tier and profiles sampled from the loaded site. Errors never raise.
    """
    from borethm import validate_site

    result = {
        "toml": None,
        "valid": False,
        "errors": [],
        "warnings": [],
        "tier": None,
        "missing": [],
        "profiles": None,
    }
    missing = list(_missing_values(document))
    if missing:
        result["errors"] = [
            {"field": path, "message": "Enter a value."} for path in missing
        ]
        return result
    try:
        text = document_to_toml(document)
    except (ValueError, TypeError) as error:
        result["errors"].append({"field": "", "message": str(error)})
        return result
    result["toml"] = text
    try:
        site = load_text(text)
    except ValueError as error:
        field, message = _schema_field(str(error))
        result["errors"].append({"field": field, "message": message})
        return result
    except (TypeError, KeyError) as error:
        result["errors"].append({"field": "", "message": str(error)})
        return result
    result["tier"] = site.tier
    result["missing"] = [
        {"input": name, "basis": basis} for name, basis in site.missing()
    ]
    # validate_site reports operations messages as "operations.<field>: ...";
    # those are taken from the model's records below, with their field names.
    own = lambda message: not str(message).startswith("operations.")
    try:
        warnings = [w for w in validate_site(site) if own(w)]
    except ValueError as error:
        for part in _split_validation(site.name, str(error)):
            if own(part):
                result["errors"].append(
                    {"field": _validation_field(document, part), "message": part}
                )
        warnings = []
    except (TypeError, IndexError, KeyError) as error:
        result["errors"].append({"field": "", "message": str(error)})
        warnings = []
    operation_warnings = []
    if getattr(site, "operations", None) is not None:
        from borethm import validate_operations

        for record in validate_operations(site.operations):
            entry = {"field": f"operations.{record['field']}", "message": record["message"]}
            if record["level"] == "error":
                result["errors"].append(entry)
            else:
                operation_warnings.append(entry)
    result["warnings"] = [
        {"field": _validation_field(document, str(w)), "message": str(w)}
        for w in warnings
    ] + operation_warnings
    result["valid"] = not result["errors"]
    try:
        result["profiles"] = _profiles(site)
    except (ValueError, TypeError, IndexError, ArithmeticError):
        result["profiles"] = None
    return result


# ---------------------------------------------------------------- comparison
def _sample_depths(a, b):
    deepest = max(float(a.target()), float(b.target()), 1000.0) + 1000.0
    return np.arange(0.0, deepest, 5.0)


def _same(x, y, rel):
    if isinstance(x, float) or isinstance(y, float):
        if x is None or y is None:
            return x is y
        return math.isclose(float(x), float(y), rel_tol=rel, abs_tol=0.0) or (
            x == y
        )
    if is_dataclass(x) and is_dataclass(y):
        return _same(asdict(x), asdict(y), rel)
    if isinstance(x, dict) and isinstance(y, dict):
        return x.keys() == y.keys() and all(_same(x[k], y[k], rel) for k in x)
    if isinstance(x, (list, tuple)) and isinstance(y, (list, tuple)):
        return len(x) == len(y) and all(_same(i, j, rel) for i, j in zip(x, y))
    return x == y


def site_differences(a, b, rel=0.0):
    """Names of SiteProfile fields that differ. Geotherms compare by sampling.

    rel=0 requires identical values. A positive rel accepts relative
    differences up to that size.
    """
    differences = []
    for field in fields(a):
        x, y = getattr(a, field.name), getattr(b, field.name)
        if callable(x) or callable(y):
            if x is None or y is None:
                if x is not y:
                    differences.append(field.name)
                continue
            depth = _sample_depths(a, b)
            tx = np.asarray(x(depth), dtype=float)
            ty = np.asarray(y(depth), dtype=float)
            same = (
                np.array_equal(tx, ty)
                if rel == 0
                else np.allclose(tx, ty, rtol=rel, atol=0.0)
            )
            if not same:
                differences.append(field.name)
        elif not _same(x, y, rel):
            differences.append(field.name)
    return differences
