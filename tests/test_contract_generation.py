"""API metadata is independent of whether production UI assets exist."""

from borethm_workbench import service


def test_contract_generation_is_identical_with_and_without_frontend(
    tmp_path, monkeypatch
):
    package = tmp_path / "package"
    package.mkdir()
    monkeypatch.setattr(service, "PACKAGE", package)
    without = service.create_app(tmp_path / "without", start_jobs=False)
    expected = without.openapi()
    without.state.store.close()
    static = package / "static"
    static.mkdir()
    (static / "index.html").write_text("<title>BoreTHM Workbench</title>")
    with_assets = service.create_app(tmp_path / "with", start_jobs=False)
    assert with_assets.openapi() == expected
    with_assets.state.store.close()
    assert "/" not in expected["paths"]
    responses = expected["paths"]["/api/v1/runs"]["post"]["responses"]
    assert responses["413"]["description"] == "Request too large"
    assert responses["422"]["description"] == "Invalid input"
