# BoreTHM Workbench

BoreTHM Workbench is a local, single-user browser application for running BoreTHM calculations and inspecting their results. It uses the [BoreTHM model package](https://github.com/ldocallaghan/BoreTHM); the [model documentation](https://ldocallaghan.github.io/BoreTHM/) describes the equations and input data.

From a checkout, with Python 3.11 to 3.14 and Node.js 24 available:

```text
python -m venv .venv
python -m pip --python .venv install -r requirements-lock.txt
python -m pip --python .venv install --no-deps -e .
npm --prefix frontend ci
npm --prefix frontend run build
.venv/Scripts/borethm-workbench prepare
.venv/Scripts/borethm-workbench serve
```

On Linux use `.venv/bin/borethm-workbench` for the last two commands. Open the local address printed at start-up. Preparation generates the model's water-property table and can take several minutes. Use `--data-dir` on both commands to select a persistent data directory. See [getting started](docs/getting-started.md) for the installation checks and data selection.

The application supports circulation calculations, site assessment and bounded parameter sweeps. A well survey describes the path through the ground. Thermal history modes use prescribed local boundary conditions and require optional model observations that are absent from the published BoreTHM 1.6.0 package. They become available with the next model release containing those arguments; its version number has not been assigned.

Windows and Ubuntu are the supported desktop platforms. See [scope and limits](docs/scope-and-limits.md) for the test evidence and numerical comparison tolerances. Results have not been compared with field measurements by this application. The service listens on the local machine and is intended for one user.

The code is licensed under Apache-2.0. [NOTICE](NOTICE) records data attribution and frontend library licences. Cite this application using [CITATION.cff](CITATION.cff), and cite the underlying model as BoreTHM, DOI [10.5281/zenodo.23549104](https://doi.org/10.5281/zenodo.23549104).
