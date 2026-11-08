# Changelog

## 0.3.0.dev0

Entry and display use SI or oilfield units throughout: editors, results, plots, tables, CSV downloads and reports. The choice is an application setting. Scenarios, archives, contracts and the model hold SI values, and a scenario's hash does not change with the unit choice. One module holds the unit table and its exact factors; `borethm-workbench contracts` writes it to `contracts/units.json` for the frontend.

A site editor creates BoreTHM site files from a form: target, temperature profile, stress cases or the transition rule, strength cases, rock properties and provenance. It starts from a blank form or a built-in site, checks every change with the model's `validate_site`, shows each warning beside its field, reports the evidence tier, and imports and exports TOML. Circulation scenarios edit the formation temperature profile in a table with a plot.

An assessment opens on a results page built from the evaluator result: the verdict and controlling constraint, the mud-weight window with the binding depth of each bound, each tool and coating peak temperature against its rating, the stability verdicts at the target and the standpipe pressure. The verdict text is labelled as the model's own wording, in the units the model states. Qualifications appear as plain sentences; their codes stay in the details. The previous assessment views remain under Details. Labels and messages describe the drilling problem; identifiers stay in the details.

A Field comparisons page shows the FORGE 16A and 16B circulation comparisons published with BoreTHM 1.8.0, with their bias and RMSE and what each covers. Every circulation run and assessment lists its inputs outside the conditions those comparisons cover. The list never blocks a run. `deploy/build_evidence.py` writes the data files from a BoreTHM checkout and records each source and checksum.

Completed circulation runs and assessments export a self-contained HTML report that prints to A4 or Letter. The same run and unit choice give the same bytes.

Wall histories, tool histories, scheduled interventions and checkpoint branching are under Advanced, labelled as research tools with prescribed boundaries. Their behaviour is unchanged.

Scenarios select and record current or previous model physics. Schema 1.1 records the choice; schema 1.0 loads with previous physics and an explanation. Reproduction and checkpoints require matching recorded physics.

The high-temperature preset tests a circulation that does not converge. With the formation at 450 °C, the model's circulation solve under current physics reports no success, and its unsuccessful profile reaches about 1,500 °C. The application returns `SOLVER_NOT_CONVERGED` with the message "The circulation calculation did not converge for these inputs." An earlier entry for this version described the failure as a property-table limit; that description was wrong. The preset converges with friction heating off and under previous physics. `PROPERTY_DOMAIN_OUTSIDE` now applies to a converged fluid profile outside the water-property table. The table limits fluid states only; a formation hotter than 479 °C goes to the solver.

Joint editing supports kind-dependent defaults and tapered transitions with an included angle. Segments can select named tool classes and collar sizes, with generic allowances displayed beside supplied figures. A supplied loss overrides its generic allowance.

First version in this repository. Installs BoreTHM from PyPI. Requires BoreTHM 1.8.0 or later within the 1.x release line. History modes and event checkpoints are available with the installed package. Earlier example archives and checkpoints, including those from Workbench 0.2.0rc2, are unsupported.
