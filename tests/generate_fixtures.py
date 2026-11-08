"""Generate the small numerical input and result fixtures read by the tests."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def reference_case(base):
    from borethm.programme import HydraulicProgramme
    from borethm import well_geometry as w

    programme = HydraulicProgramme.reference_programme()
    geometry = programme.geometry(3000.0, w.CONVENTIONAL)
    data = json.loads(json.dumps(base))

    def layer(x):
        return dict(r_in_m=x.r_in, r_out_m=x.r_out, conductivity_W_m_K=x.k)

    string = []
    for index, s in enumerate(geometry.string):
        p, j, t, local = s.pipe, s.pipe.hydraulic_joint, s.tool_loss, s.local_loss
        assert local is None
        joint = (
            None
            if j is None
            else dict(
                outer_diameter_m=j.outer_diameter,
                bore_diameter_m=j.bore_diameter,
                restriction_length_m=j.restriction_length,
                spacing_m=j.spacing,
                transition=j.transition,
                kind=j.kind,
                taper_angle_deg=j.taper_angle_deg,
                source=j.source,
            )
        )
        tool = (
            None
            if t is None
            else dict(
                reference_pressure_Pa=t.dp_ref,
                reference_flow_m3_s=t.flow_ref,
                reference_density_kg_m3=t.density_ref,
                basis=t.basis,
                span_segments=t.span_segments,
                source=t.source,
                flow_range_m3_s=t.flow_range,
                temperature_max_C=t.temperature_max_C,
                include_boundary_changes=t.include_boundary_changes,
                unresolved=t.unresolved,
            )
        )
        string.append(
            dict(
                id=f"reference-component-{index + 1}",
                length_m=s.length,
                pipe=dict(
                    name=p.name,
                    layers=[layer(x) for x in p.layers],
                    thermal_joint_fraction=p.joint_fraction,
                    hydraulic_joint=joint,
                    source=p.source,
                ),
                local_loss=None,
                tool_loss=tool,
                tool_class=s.tools[0].tool_class if s.tools else None,
                collar_in=s.tools[0].collar_in if s.tools else None,
                tool_basis=s.tools[0].basis if s.tools else "additional",
                tool_span_segments=s.tools[0].span_segments if s.tools else 1,
            )
        )
    data["definition"]["well"].update(
        string=string,
        hole=[
            dict(
                id=f"reference-hole-{i + 1}",
                md_top_m=h.md_top,
                md_bottom_m=h.md_bottom,
                r_wall_m=h.r_wall,
                layers=[layer(x) for x in h.layers],
            )
            for i, h in enumerate(geometry.hole)
        ],
    )
    data["definition"]["hydraulics"]["surface_loss"] = dict(kind="api_case", case=5)
    data["title"] = (
        "Reference vertical assembly: joints, MWD allowance and cased intervals"
    )
    data["provenance"] = [
        dict(
            path="/definition/well",
            basis="reference_scenario",
            source="BoreTHM 1.8.0 HydraulicProgramme.reference_programme / Wu geometry; supplied catalogue allowances are not verified field hardware",
        )
    ]
    return data


def cases():
    base = json.loads(
        (ROOT / "src/borethm_workbench/presets/synthetic-water.json").read_bytes()
    )
    reference = reference_case(base)
    mud = json.loads(json.dumps(base))
    mud["title"] = "Weighted-mud vertical case, 1,200 kg/m³ at 20 °C"
    mud["definition"]["fluid"] = dict(
        kind="barite_mud",
        surface_density_kg_m3=1200.0,
        plastic_viscosity_Pa_s=0.02,
        viscosity_reference_temperature_C=20.0,
        yield_point_Pa=5.0,
        conserve_mass=True,
    )
    constant = json.loads(json.dumps(base))
    constant["title"] = "Constant-property analytic hydrostatic fixture"
    constant["definition"]["fluid"] = dict(
        kind="constant_properties",
        density_kg_m3=1000.0,
        heat_capacity_J_kg_K=4200.0,
        viscosity_Pa_s=0.001,
        conductivity_W_m_K=0.6,
        approximation_label="Analytic constant-property fixture; not a water-property calculation",
    )
    hot = json.loads(json.dumps(base))
    hot["title"] = (
        "High-temperature water case: no converged solution under current physics"
    )
    hot["definition"]["formation"]["geotherm"]["points_tvd_m_temperature_C"][-1][1] = (
        450.0
    )
    return {
        "reference-assembly": reference,
        "weighted-mud": mud,
        "constant-properties": constant,
        "high-temperature": hot,
    }


def main():
    import borethm
    from borethm_workbench.adapter import run_circulation
    from borethm_workbench.contracts import (
        Scenario,
        canonical_bytes,
        parse_scenario_json,
    )
    from borethm_workbench.assessment import load_frozen_site, finite_data
    from borethm.studies.release_v134 import reference_row

    folder = ROOT / "tests/fixtures"
    folder.mkdir(exist_ok=True)
    presets = ROOT / "src/borethm_workbench/presets"
    for path in presets.glob("*.json"):
        data = json.loads(path.read_bytes())
        data["schema_version"] = "1.1"
        data["model"].update(supported_release="1.8.0", physics="v1.8.0")
        data.get("metadata", {}).pop("schema_loading", None)
        if data["mode"] == "circulation_state":
            for segment in data["definition"]["well"]["string"]:
                joint = segment["pipe"].get("hydraulic_joint")
                if joint:
                    joint.setdefault("kind", "ordinary")
                    joint["transition"] = None
        path.write_bytes(canonical_bytes(data))
    for name, data in cases().items():
        (presets / (name + ".json")).write_bytes(
            canonical_bytes(Scenario.model_validate(data).model_dump())
        )
    records = {}
    for name, data in cases().items():
        if name == "high-temperature":
            continue
        scenario = Scenario.model_validate(data)
        result = run_circulation(scenario)
        records[name] = {
            "scenario": scenario.model_dump(),
            "physical_request_hash": result["physical_request_hash"],
            "summary": result["summary"],
            "warnings": [w["code"] for w in result["warnings"]],
        }
    (folder / "physical-cohort.json").write_bytes(
        canonical_bytes(
            {
                "schema_version": "circulation-cohort-v1",
                "core_version": borethm.__version__,
                "cases": records,
            }
        )
    )
    failure = json.loads(
        (ROOT / "src/borethm_workbench/presets/synthetic-water.json").read_bytes()
    )
    failure["title"] = "Hot formation with short exposure"
    failure["definition"]["formation"]["geotherm"]["points_tvd_m_temperature_C"][-1][
        1
    ] = 450.0
    failure["definition"]["operating"]["exposure"] = {
        "kind": "uniform_seconds",
        "seconds": 3600.0,
    }
    (folder / "hot-short-exposure.json").write_bytes(canonical_bytes(failure))
    scenario = parse_scenario_json(
        (ROOT / "src/borethm_workbench/presets/newberry-assessment.json").read_bytes()
    )
    raw = borethm.evaluate(
        load_frozen_site(scenario),
        v10=False,
        thermal=True,
        circulation=scenario.definition.options.calculation_mode,
        coupled=None,
        programme=None,
        physics=scenario.model.physics,
        surface=None,
    )
    (folder / "assessment-reference.json").write_bytes(
        canonical_bytes(reference_row(finite_data(borethm.result_dict(raw))))
    )


if __name__ == "__main__":
    main()
