# Getting started

Install Python 3.11 to 3.14 and Node.js 24. Run the commands in README from the checkout root. The Python lock records exact dependency versions, with separate numerical-library versions for Python 3.11. The npm lock records the frontend dependencies.

`borethm-workbench prepare` uses BoreTHM's water-table generator. It preserves an existing valid table. A missing table is generated in the selected data directory. `borethm-workbench cache-info` reports readiness and fingerprints, which are checksums of each numerical array after conversion to little-endian 64-bit floating-point values. The checksums exclude the archive metadata.

`borethm-workbench serve` starts the browser service on port 8765. Open `http://127.0.0.1:8765`. Choose a packaged scenario and submit a calculation. Completed runs can be exported as archives. `borethm-workbench inspect result.zip` checks an archive without solving it again. `borethm-workbench reproduce result.zip` repeats its calculation and reports differences against the numerical tolerances.

Use `--data-dir selected-data` on prepare and serve to select a directory. The command-line argument takes precedence over `BORETHM_WORKBENCH_DATA`; the default is `.workbench-data` relative to the current directory. `data-info` prints the selection without creating a database. `BORETHM_CACHE_DIR` can independently select a shared water-table directory. Keep data directories outside the checkout when using a packaged installation.

The readiness endpoint is `/api/v1/health/ready`. `/api/v1/capabilities` reports available modes and the installed model version. With BoreTHM 1.6.0, a history request returns a capability error explaining that the next model release is needed. Circulation, site assessment and sweeps remain available.

For a local model checkout containing the optional observations, install that checkout into the same environment with `python -m pip --python .venv install --no-deps -e ../BoreTHM`. Restart the service to update the reported capabilities. The saved result records the checkout's commit identifier. Commit changes before generating results intended for later reproduction.

To check a checkout, run pytest with its environment's Python, regenerate contracts with `borethm-workbench contracts`, and run the frontend types, typecheck, test and build commands. Builds include the frontend bundle and its third-party licence texts. The release workflow checks a wheel installation from outside the checkout and generates examples as separate assets.
