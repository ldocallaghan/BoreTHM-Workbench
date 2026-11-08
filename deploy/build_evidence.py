"""Build the field-comparison data files from a BoreTHM checkout.

Usage:

    python deploy/build_evidence.py --borethm PATH/TO/BoreTHM [--output DIR] [--check]

PATH/TO/BoreTHM is a Git checkout of https://github.com/ldocallaghan/BoreTHM.
The script reads the tag named by TAG from that checkout with ``git show``,
so the working tree may be on any commit. It reads only:

- docs/science/validation.md, the two sections named in SECTIONS;
- the FORGE 16A comparison tables listed in FORGE_16A;
- the FORGE 16B S1 comparison tables and protocols listed in FORGE_16B.

It writes comparisons.json, tested-range.json and sources.json. sources.json
records the tag, its commit, each source path and the SHA-256 of every source
and output file. --check compares the generated files with the existing ones
and exits with status 1 on any difference. Unit conversions use the exact
factors of borethm_workbench.units.
"""

import argparse
import csv
import hashlib
import io
import json
import math
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from borethm_workbench.units import FOOT_M, PPG_KG_M3, US_GALLON_L  # noqa: E402

TAG = "v1.8.0"
DEFAULT_OUTPUT = (
    Path(__file__).resolve().parents[1] / "src" / "borethm_workbench" / "evidence"
)
VALIDATION_DOC = "docs/science/validation.md"
SECTIONS = (
    "### FORGE 16B: whole-well circulation comparison",
    "### FORGE 16A: blind circulation comparison",
)
FORGE_16A = {
    "metrics": "validation/forge16a/circulation-metrics.csv",
    "primary": "validation/forge16a/circulation-primary.csv",
    "returns": "validation/forge16a/return-metrics.csv",
    "inlet": "validation/forge16a/inlet-counterfactual.csv",
    "runs": "validation/forge16a/run_inputs.csv",
    "formation": "validation/forge16a/formation_temperature_216day.csv",
    "protocol": "validation/forge16a/comparison_protocol.md",
    "readme": "validation/forge16a/README.md",
}
FORGE_16B = {
    "runs": "validation/forge16b/s1/run-metrics.csv",
    "pooled": "validation/forge16b/s1/pooled-metrics.csv",
    "samples": "validation/forge16b/s1/sample-comparisons.csv",
    "inputs": "validation/forge16b/s1/frozen-inputs.json",
    "results": "validation/forge16b/s1/results.jsonl",
    "protocol": "validation/forge16b/comparison_protocol.md",
    "protocol_s1": "validation/forge16b/comparison_protocol_s1.md",
}
PRIMARY_16A = "film=1; k=2.497333333; formation=216-constant; pipe=4.276"
PRIMARY_16B = ("recorded_mud", "1.0", "cumulative")
BRANCHES_16A = ("bore_bit_C", "bore_40ft_C", "annulus_bit_C", "annulus_40ft_C")
GPM_M3_S = US_GALLON_L * 0.001 / 60.0


def git(checkout, *arguments):
    return subprocess.run(
        ["git", "-C", str(checkout), *arguments],
        check=True,
        capture_output=True,
    ).stdout


class Source:
    def __init__(self, checkout):
        self.checkout = checkout
        self.commit = git(checkout, "rev-parse", f"{TAG}^{{commit}}").decode().strip()
        self.files = {}

    def read(self, path):
        data = git(self.checkout, "show", f"{self.commit}:{path}")
        self.files[path] = {
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
        }
        return data

    def csv(self, path):
        return list(csv.DictReader(io.StringIO(self.read(path).decode("utf-8"))))


def number(text):
    return float(text)


def section(text, heading):
    start = text.index(heading + "\n")
    level = heading.split(" ")[0]
    end = len(text)
    for marker in ("\n" + level + " ", "\n## "):
        position = text.find(marker, start + len(heading))
        if position != -1:
            end = min(end, position)
    return text[start:end]


def table_rows(markdown, first_header):
    """Rows of the first markdown table whose header starts with first_header."""
    lines = markdown.splitlines()
    for index, line in enumerate(lines):
        if line.startswith("| " + first_header):
            rows = []
            for row in lines[index + 2 :]:
                if not row.startswith("|"):
                    break
                rows.append([cell.strip() for cell in row.strip("|").split("|")])
            return rows
    raise ValueError(f"table {first_header!r} not found")


def published_number(text):
    return float(text.replace("−", "-").replace("+", ""))


def published_range(text):
    """'a–b' or a single value, with Unicode minus signs."""
    text = text.replace("−", "-")
    if " to " in text:
        low, high = text.split(" to ")
        return float(low.replace("+", "")), float(high.replace("+", ""))
    if "–" in text:
        low, high = text.split("–")
        return float(low), float(high)
    value = float(text.replace("+", ""))
    return value, value


def check_close(label, value, published, places=2):
    if round(value, places) != round(published, places) and not math.isclose(
        value, published, abs_tol=0.5 * 10**-places + 1e-9
    ):
        raise ValueError(f"{label}: table value {value} differs from published {published}")


def f_to_c(value):
    return (value - 32.0) * 5.0 / 9.0


def build_16a(source, doc_section):
    metrics = [
        r for r in source.csv(FORGE_16A["metrics"]) if r["configuration"] == PRIMARY_16A
    ]
    returns = [
        r for r in source.csv(FORGE_16A["returns"]) if r["configuration"] == PRIMARY_16A
    ]
    primary = [
        r for r in source.csv(FORGE_16A["primary"]) if r["source"] == "Sanvean on-bottom"
    ]
    inlet = {r["run"]: r for r in source.csv(FORGE_16A["inlet"])}
    run_inputs = {r["run"]: r for r in source.csv(FORGE_16A["runs"])}
    formation = source.csv(FORGE_16A["formation"])
    source.read(FORGE_16A["protocol"])
    source.read(FORGE_16A["readme"])

    published = {row[0]: row for row in table_rows(doc_section, "BHA |")}
    runs = []
    order = [r for r in dict.fromkeys(r["run"] for r in metrics) if r[0].isdigit()]
    for run in order:
        rows = [
            r for r in metrics if r["run"] == run and r["channel"] == "bit_box_C"
        ]
        model = [number(r["model_mean_C"]) for r in rows]
        error = [number(r["ME_C"]) for r in rows]
        rmse = [number(r["RMSE_C"]) for r in rows]
        entry = {
            "run": f"BHA {run}",
            "samples": int(rows[0]["n"]),
            "measured_mean_C": number(rows[0]["measured_mean_C"]),
            "model_mean_C": [min(model), max(model)],
            "bias_C": [min(error), max(error)],
            "rmse_C": [min(rmse), max(rmse)],
        }
        row = published[run]
        check_close(f"16A {run} measured", entry["measured_mean_C"], published_number(row[2]))
        for key, cell in (("model_mean_C", row[3]), ("bias_C", row[4]), ("rmse_C", row[5])):
            low, high = published_range(cell)
            check_close(f"16A {run} {key} low", entry[key][0], low)
            check_close(f"16A {run} {key} high", entry[key][1], high)
        runs.append(entry)
    pooled = [r for r in metrics if r["run"] == "ALL" and r["channel"] == "bit_box_C"]
    pooled_rmse = [min(number(r["RMSE_C"]) for r in pooled), max(number(r["RMSE_C"]) for r in pooled)]
    pooled_bias = [min(number(r["ME_C"]) for r in pooled), max(number(r["ME_C"]) for r in pooled)]
    return_all = next(r for r in returns if r["run"] == "ALL" and r["source"] == "Sanvean on-bottom")

    samples = []
    for r in primary:
        modelled = [number(r[b]) for b in BRANCHES_16A]
        samples.append(
            {
                "run": f"BHA {r['run']}",
                "depth_m": number(r["bit_ft"]) * FOOT_M,
                "measured_C": number(r["measured_bit_box_C"]),
                "modelled_C": [min(modelled), max(modelled)],
            }
        )
    compared = sorted({r["run"] for r in primary}, key=lambda x: order.index(x))

    # Formation temperature at each sample depth: the primary case uses the
    # 216-day log by MD and keeps its endpoint beyond the deepest log point.
    log_md = [number(r["md_ft"]) for r in formation]
    log_t = [number(r["temp_C"]) for r in formation]

    def formation_at(md_ft):
        if md_ft >= log_md[-1]:
            return log_t[-1]
        for i in range(1, len(log_md)):
            if md_ft <= log_md[i]:
                fraction = (md_ft - log_md[i - 1]) / (log_md[i] - log_md[i - 1])
                return log_t[i - 1] + fraction * (log_t[i] - log_t[i - 1])
        return log_t[-1]

    formation_c = [formation_at(number(r["bit_ft"])) for r in primary]
    flow = [
        (number(run_inputs[run]["drilling_flow_min_gpm"]), number(run_inputs[run]["drilling_flow_max_gpm"]))
        for run in compared
    ]
    inlet_c = [
        (number(inlet[run]["logged_inlet_min_C"]), number(inlet[run]["logged_inlet_max_C"]))
        for run in compared
    ]
    mud = [
        (number(run_inputs[run]["mud_weight_min_ppg"]), number(run_inputs[run]["mud_weight_max_ppg"]))
        for run in compared
    ]
    depths = [s["depth_m"] for s in samples]
    comparison = {
        "id": "forge16a",
        "title": "FORGE 16A: blind circulation comparison",
        "well": "Utah FORGE well 16A(78)-32",
        "model": "BoreTHM v1.5.0 at b9a8a93, nothing fitted to the 16A temperatures",
        "measurement": "Bit-box temperature from the bottom-hole sensor while drilling on bottom",
        "samples": len(samples),
        "runs": runs,
        "pooled": {"samples": int(pooled[0]["n"]), "bias_C": pooled_bias, "rmse_C": pooled_rmse},
        "return": {
            "samples": int(return_all["n"]),
            "bias_C": number(return_all["ME_C"]),
            "rmse_C": number(return_all["RMSE_C"]),
        },
        "modelled_range_note": "Each modelled value is the range over four fixed readings: bore and annulus fluid, at the bit and 40 ft above it. The sensor position and medium are unresolved. The range is not a confidence interval.",
        "covers": [
            "Circulating temperatures while drilling on bottom: 135 half-hour samples from nine bottom-hole assemblies.",
            "Water-based drilling mud in steel drill pipe.",
            f"Bit depths from {min(depths):,.0f} m to {max(depths):,.0f} m measured depth.",
            "Surface return temperature at the same sample depths.",
        ],
        "does_not_cover": [
            "Run-in, trip, connection or pumps-off temperatures.",
            "Insulated drill pipe.",
            "Recovery of the wall temperature during trips: the calculation counts accumulated circulating exposure only.",
            "Formation temperatures above the 216-day log's deepest value.",
        ],
        "range": {
            "depth_m": [min(depths), max(depths)],
            "formation_C": [min(formation_c), max(formation_c)],
            "volume_flow_m3_s": [min(x[0] for x in flow) * GPM_M3_S, max(x[1] for x in flow) * GPM_M3_S],
            "inlet_C": [min(x[0] for x in inlet_c), max(x[1] for x in inlet_c)],
            "mud_density_kg_m3": [min(x[0] for x in mud) * PPG_KG_M3, max(x[1] for x in mud) * PPG_KG_M3],
            "fluids": ["mud"],
            "pipes": ["steel"],
        },
        "range_basis": {
            "depth_m": "bit depth of the 135 compared samples",
            "formation_C": "216-day formation log by measured depth at each sample's bit depth, with the log's deepest value held below it, as in the primary case",
            "volume_flow_m3_s": "drilling flow range of the nine compared runs",
            "inlet_C": "logged inlet range of the compared samples",
            "mass_flow_kg_s": "not recorded in the 16A comparison tables",
        },
    }
    return comparison, samples


def build_16b(source, doc_section):
    runs_csv = source.csv(FORGE_16B["runs"])
    pooled_csv = source.csv(FORGE_16B["pooled"])
    samples_csv = source.csv(FORGE_16B["samples"])
    inputs = json.loads(source.read(FORGE_16B["inputs"]))
    results = [
        json.loads(line)
        for line in source.read(FORGE_16B["results"]).decode("utf-8").splitlines()
        if line.strip()
    ]
    source.read(FORGE_16B["protocol"])
    source.read(FORGE_16B["protocol_s1"])

    def primary(row):
        return (row["fluid"], row["film_mult"], row["exposure"]) == PRIMARY_16B

    published = {}
    for row in table_rows(doc_section, "EOW assembly | Samples"):
        published[row[0]] = row
    revised = {}
    for row in table_rows(doc_section, "EOW assembly | S1 v1 bias"):
        revised[row[0]] = published_number(row[2])

    runs = []
    for row in [r for r in runs_csv if primary(r)]:
        entry = {
            "run": f"EOW {row['eow_bha']}",
            "position_ft": int(row["position_ft"]),
            "samples": int(row["n"]),
            "measured_mean_C": number(row["measured_mean_C"]),
            "model_mean_C": number(row["model_mean_C"]),
            "bias_C": number(row["bias_C"]),
            "rmse_C": number(row["RMSE_C"]),
            "revised_bias_C": revised.get(row["eow_bha"]),
        }
        doc = published[row["eow_bha"]]
        check_close(f"16B {row['eow_bha']} measured", entry["measured_mean_C"], published_number(doc[2]))
        low, high = published_range(doc[3])
        if not (round(low, 2) - 0.005 <= round(entry["bias_C"], 2) <= round(high, 2) + 0.005):
            raise ValueError(f"16B {row['eow_bha']} bias {entry['bias_C']} outside published {doc[3]}")
        runs.append(entry)
    pooled = [
        {
            "group": r["group"],
            "samples": int(r["n"]),
            "bias_C": number(r["bias_C"]),
            "rmse_C": number(r["RMSE_C"]),
        }
        for r in pooled_csv
        if primary(r) and r["position"] == "nominal"
    ]
    by_sample = {}
    for r in samples_csv:
        if not primary(r):
            continue
        item = by_sample.setdefault(
            r["sample_id"],
            {
                "run": f"EOW {r['eow_bha']}",
                "depth_m": number(r["depth_ft_MD"]) * FOOT_M,
                "measured_C": number(r["measured_C"]),
                "modelled": [],
                "pipe": r["pipe_type"],
            },
        )
        item["modelled"].append(number(r["bore_C"]))
    samples = []
    for key in sorted(by_sample, key=lambda k: (by_sample[k]["depth_m"], k)):
        item = by_sample[key]
        samples.append(
            {
                "run": item["run"],
                "depth_m": item["depth_m"],
                "measured_C": item["measured_C"],
                "modelled_C": [min(item["modelled"]), max(item["modelled"])],
                "pipe": "insulated" if "IDP" in item["pipe"] else "steel",
            }
        )
    frozen = inputs["samples"]
    depths = [number(s["bit_ft"]) * FOOT_M for s in frozen]
    flows = [number(s["flow_gpm"]) * GPM_M3_S for s in frozen]
    inlets = [number(s["T_in_C"]) for s in frozen]
    mass = [
        number(r["mass_rate_kg_s"])
        for r in results
        if r["success"] and r["fluid"] == "recorded_mud" and r["film_mult"] == 1.0 and r["exposure"] == "cumulative"
    ]
    mass_water = [
        number(r["mass_rate_kg_s"])
        for r in results
        if r["success"] and r["fluid"] == "water" and r["film_mult"] == 1.0 and r["exposure"] == "cumulative"
    ]
    mud = [number(m["density_ppg"]) * PPG_KG_M3 for m in inputs["mud"]]
    comparison = {
        "id": "forge16b",
        "title": "FORGE 16B: whole-well circulation comparison",
        "well": "Utah FORGE well 16B",
        "model": "BoreTHM v1.5.0 at 85f23db, nothing fitted in this comparison",
        "measurement": "MWD EYE temperature channel while circulating",
        "samples": len(frozen),
        "runs": runs,
        "pooled": pooled,
        "modelled_range_note": "Each modelled value is the bore-fluid temperature at the stated sensor position; where the position is unresolved, the range covers every requested position. The range is not a confidence interval.",
        "revision_note": "A later mud-protocol revision (version 2) changed five run means; its biases are shown beside the original version 1 values. RMSE is published for version 1 only.",
        "covers": [
            "Circulating temperatures: 109 half-hour samples from 13 bottom-hole assemblies.",
            "Recorded drilling mud. Water was run as a sensitivity case on the same samples.",
            "Regular steel drill pipe and, in the four trial assemblies, insulated drill pipe.",
            f"Bit depths from {min(depths):,.0f} m to {max(depths):,.0f} m measured depth.",
        ],
        "does_not_cover": [
            "Run-in, trip, connection or pumps-off temperatures.",
            "An independent test of insulated pipe: its conductivity was fitted on EOW 18, and EOW 17 and 20 supplied the earlier film fit.",
            "A resolved sensor position for EOW 3 and 4.",
            "Recovery of the wall temperature during trips: the primary calculation counts accumulated circulating exposure.",
        ],
        "range": {
            "depth_m": [min(depths), max(depths)],
            "volume_flow_m3_s": [min(flows), max(flows)],
            "mass_flow_kg_s": [min(mass + mass_water), max(mass + mass_water)],
            "inlet_C": [min(inlets), max(inlets)],
            "mud_density_kg_m3": [min(mud), max(mud)],
            "fluids": ["mud", "water"],
            "pipes": ["steel", "insulated"],
        },
        "range_basis": {
            "depth_m": "bit depth of the 109 samples",
            "volume_flow_m3_s": "measured flow of the 109 samples",
            "mass_flow_kg_s": "modelled mass rate of the primary mud and water cases",
            "inlet_C": "measured inlet temperature of the 109 samples",
            "formation_C": "not recorded in the S1 comparison tables",
        },
    }
    return comparison, samples


def tested_range(comparisons):
    def envelope(key):
        values = [c["range"][key] for c in comparisons if key in c["range"]]
        return [min(v[0] for v in values), max(v[1] for v in values)] if values else None

    def sources(key):
        return [c["id"] for c in comparisons if key in c["range"]]

    rows = []
    for key, quantity, label in (
        ("depth_m", "length", "Bit depth (measured depth)"),
        ("formation_C", "temperature", "Bottom-hole formation temperature"),
        ("volume_flow_m3_s", "volume_flow", "Volume flow"),
        ("mass_flow_kg_s", "mass_flow", "Mass flow"),
        ("inlet_C", "temperature", "Inlet temperature"),
    ):
        rows.append(
            {
                "input": key,
                "label": label,
                "quantity": quantity,
                "range": envelope(key),
                "sources": sources(key),
                "basis": {c["id"]: c["range_basis"].get(key) for c in comparisons},
            }
        )
    fluids = sorted({f for c in comparisons for f in c["range"]["fluids"]})
    pipes = sorted({p for c in comparisons for p in c["range"]["pipes"]})
    rows.append(
        {
            "input": "fluid",
            "label": "Fluid type",
            "values": fluids,
            "notes": {"water": "as a sensitivity case in a well drilled with mud"},
            "sources": {f: [c["id"] for c in comparisons if f in c["range"]["fluids"]] for f in fluids},
        }
    )
    rows.append(
        {
            "input": "pipe",
            "label": "Pipe type",
            "values": pipes,
            "notes": {"insulated": "in trial assemblies whose data supplied the fitted pipe conductivity"},
            "sources": {p: [c["id"] for c in comparisons if p in c["range"]["pipes"]] for p in pipes},
        }
    )
    return {
        "rows": rows,
        "conditions": "Circulating conditions only. No run-in, trip or connection temperatures were compared.",
        "pipe_rule": "A string counts as insulated when any of its pipe layers has a thermal conductivity below 10 W/(m K); otherwise it counts as steel.",
    }


def dump(value):
    return (json.dumps(value, indent=1, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def build(checkout):
    source = Source(Path(checkout))
    text = source.read(VALIDATION_DOC).decode("utf-8")
    sections = {heading: section(text, heading) for heading in SECTIONS}
    c16b, s16b = build_16b(source, sections[SECTIONS[0]])
    c16a, s16a = build_16a(source, sections[SECTIONS[1]])
    c16a["sample_values"] = s16a
    c16b["sample_values"] = s16b
    comparisons = [c16a, c16b]
    outputs = {
        "comparisons.json": dump(
            {
                "source": f"BoreTHM {TAG}, {VALIDATION_DOC}",
                "sections": [h.lstrip("# ") for h in SECTIONS],
                "comparisons": comparisons,
            }
        ),
        "tested-range.json": dump(tested_range(comparisons)),
    }
    record = {
        "repository": "https://github.com/ldocallaghan/BoreTHM",
        "tag": TAG,
        "commit": source.commit,
        "script": "deploy/build_evidence.py",
        "sources": dict(sorted(source.files.items())),
        "outputs": {name: hashlib.sha256(data).hexdigest() for name, data in sorted(outputs.items())},
    }
    outputs["sources.json"] = dump(record)
    return outputs


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--borethm", required=True, help="BoreTHM Git checkout")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--check", action="store_true", help="compare with existing files")
    arguments = parser.parse_args(argv)
    try:
        outputs = build(arguments.borethm)
    except subprocess.CalledProcessError as error:
        raise SystemExit(
            f"git could not read {TAG} from the checkout: {error.stderr.decode().strip()}"
        ) from None
    directory = Path(arguments.output)
    if arguments.check:
        different = [
            name
            for name, data in outputs.items()
            if not (directory / name).is_file() or (directory / name).read_bytes() != data
        ]
        if different:
            print("Differs: " + ", ".join(different))
            raise SystemExit(1)
        print("Field-comparison data files match the BoreTHM sources.")
        return
    directory.mkdir(parents=True, exist_ok=True)
    for name, data in outputs.items():
        (directory / name).write_bytes(data)
    print(f"Wrote {', '.join(outputs)} from BoreTHM {TAG}.")


if __name__ == "__main__":
    main()
