# Changelog

## 0.3.0.dev0

The application requires BoreTHM 1.9.0 or later, which provides the operations interface.

On the Programme page, the operation defaults are the current site's, and a field starts at its default when "Model chooses" is unticked only where the model marks that default as restating its result; other fields, such as the flow grids and the run-in staging, start empty with the default and the rule shown. Sources name papers, data sets and the model's rules. The Operations table stays within the page at 1,280 px. Headings say "Programme: stated values and model choices" when anything is stated, and the bit temperature is "Fluid at the bit" everywhere. Lever case titles use the chosen units and the labels as written. A value that rounds to zero prints without a sign. The application uses only BoreTHM's public names. The capabilities' release status refers to the Field comparisons page.

The Programme page, results page and Design levers page use BoreTHM's settled operations interface, and the application requires it. A flow is stated as a mass flow or a volume flow at surface and is converted to lb/s or US gal/min with oilfield units; the flow levers show their unit with every value. Defaults, rules and sources come from `operation_defaults`, and validation messages attach to the fields the model's records name. A programme with the model's named pipes is written to the site file's `[programme]` table with the `pipe` key. Pump flows are the model's L/s values, and rows the model marks as not setting the status are labelled as comparisons.

With a BoreTHM that provides `Operations`, a site assessment has a Programme page. It states the hydraulic programme: the model's reference programme with options, or a custom string from surface to bit with the model's named pipes or user layers, joints and tools, hole and casing intervals and surface losses, drawn in the well view. It also states operating choices, one row per choice: each is either chosen by the model, with its rule and default from the installed model, or stated in the chosen units with a basis and a source, checked by the model's validator with warnings beside the row. The operating choices are written to the site file's `[operations]` table; the programme and operations import from and export to a site file. A site file without these tables, and a scenario without a programme, give the assessment and input hash they gave before. `/api/v1/capabilities` reports the interface.

The assessment results page and report take window widths, the bounds against depth with the mechanism that sets each, the pump limit and margins, and tool and coating margins and pass flags from the model's result keys, including a pass flag for the unstaged run-in. The application no longer computes widths or margins. The page lists the stated values and whether one of them is used in the check behind the controlling constraint.

A Design levers page runs computed assessments from an assessed site and programme: one lever at a time with 2 to 5 values, or a grid of two levers, up to 25 cases, queued one at a time with an estimate, progress and cancellation that keeps finished cases. It shows a table of verdicts, controlling constraints, margins and window widths, a tornado chart of the change of each margin from the base run, and a grid coloured by verdict. Each case is a saved run. A study exports as one archive and one HTML report.

Display units add velocity, rate of penetration and durations in minutes and hours.

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
