# Scope and limits

Circulation mode calls BoreTHM's coupled bore and annulus temperature calculation. It retains the model's hydraulics, liquid-property guards and radial rock response. The exported pressure profiles distinguish absolute pressure from gauge pressure. Prescribed annulus heating is integrated across its declared depth knots. A sweep repeats a declared set of circulation inputs, with at most 25 cases; it does not optimise an operating programme or estimate a probability distribution.

Site assessment calls the model's evaluator with a frozen site definition. The evaluator selects its programme and reports stability windows and equipment qualifications. The application preserves these status decisions. It does not impose a manual circulation flow on that evaluator.

A survey defines measured depth and vertical depth, with positions where sufficient directional data exist. Temperature and hydraulic calculations support paths whose vertical depth does not decrease. Survey reconstruction can display other paths, whose calculations are unavailable. Deviated stability assessment is unavailable.

Wall histories solve independent radial columns under prescribed circulating or static boundaries. They omit axial rock conduction and feedback to a circulating well. Tool histories use a prescribed surrounding temperature column with lumped steel and fluid heat capacities. They omit coolant transport delay and feedback to that column. Scheduled wall interventions support a fixed radial domain. Checkpoints record full physical state at completed event boundaries. Display frames alone cannot restart an integration.

Each scenario selects a named set of physical assumptions. New scenarios use `physics="v1.8.0"`: the model uses its Herschel-Bulkley annulus treatment with the Erge flow-transition band, Colebrook pipe friction and zero wall roughness. Herschel-Bulkley describes fluid shear stress as a yield stress plus a power of the shear rate. Colebrook relates pipe resistance to the flow and wall roughness. `physics="v1.7.0"` restores the previous annulus treatment and Blasius pipe friction. The installed model reports the settings used; manifests and exports record them.

Schema 1.0 scenarios load as schema 1.1 with `physics="v1.7.0"`. A visible qualification explains that choice. New scenarios use schema 1.1. Existing results retain their recorded environment requirements. Archives and checkpoints made with BoreTHM 1.6.0 cannot be reproduced; their scenarios can be re-run with previous physics on the current model.

Prescribed thermal histories do not solve hydraulic equations. Their records identify the selected physics and state that its hydraulic settings are unused.

Histories and checkpoints use the observations provided by BoreTHM 1.8.0. The service reports the installed version and checks its function signatures.

Reproduction compares installed model and application versions, the model commit for a Git installation, and installed versions of dependencies named by the lock. It also compares the saved request. Property-array fingerprint differences are reported; they do not by themselves reject reproduction across platforms. The numerical checks still apply. Checkpoint continuation requires matching build fields and the saved radial grid, with the model's eight-unit limit on differences in the last representable place.

Circulation reproduction requires identical saved coordinates and component identifiers. Temperature tolerance is 0.001 degrees Celsius. Pressure tolerance is the greater of 1 Pa and one millionth of the reference magnitude. Other scalar tolerance is 0.001. Assessment comparisons use the model reference projection: 0.002 specific gravity, 0.1 degrees Celsius, 10,000 Pa, 100 m for the field named `md`, 0.1 m for `target_m`, and 1e-8 for remaining numerical fields. Status and programme identifiers must match.

History comparisons retain their declared physical units. Temperature tolerance is 0.001 degrees Celsius. Energy tolerance is the greater of 1 J per metre and one millionth of the reference magnitude. Time tolerance is the greater of 1e-8 seconds and 1e-9 times the reference magnitude. Wall-flux tolerance is the greater of 0.001 W per metre and 1e-9 times the reference magnitude. Coordinates and declared physical inputs must match exactly. Unknown numerical fields fail the comparison. A failed physical balance remains a failure even when repeated values agree. The radial grid may be reused within the model's representable-value bound. No tolerance is enlarged to accommodate a platform difference.

The CI workflow tests Windows and Linux with Python 3.11 and 3.14. Local execution evidence is reported separately from the source tree. Results have not been compared with field measurements by this application. Internal trial-state property lookups are not comprehensively instrumented. Use the model's documented assumptions and the returned qualifications when interpreting a result.

A joint declares its kind. An omitted transition lets the selected physics decide: current physics uses an 11.5-degree included bore taper for ordinary drill-pipe joints; other kinds use abrupt transitions. An explicit tapered joint requires its angle. The annulus treatment remains abrupt. Gradual transitions omit the geometric local loss.

A segment can declare one tool class and its collar size in inches. A named tool uses the model's generic allowance under current physics; previous physics applies no generic allowance. These values describe a class and size band. Enter the maker's figure when known. A supplied ToolLoss overrides the generic allowance. The declared loss basis and segment span determine whether it adds to passage loss or replaces internal loss across that span. A zero allowance with no source remains qualified.

