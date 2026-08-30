"""Per-module contract artifacts + drift gate (contract-pipeline.md §2-3).

stapel-vocabularies emits its own ``docs/schema.json`` (drf-spectacular
OpenAPI), ``docs/flows.json``, ``docs/errors.json``, ``docs/capabilities.json``
and ``docs/llms.txt`` from a single-module ``{vocabularies + core}`` Django
instance mounted at the canonical ``/vocabularies/api/v1/`` prefix.

This module is not mounted in stapel-example-monolith, so there is no
aggregate slice to diff against for byte-identity. Standalone validation
(contract-pipeline.md §9 fallback) substitutes: determinism, self-contained
``$ref`` closure, canonical-prefix paths, and that the error keys the views
actually return are in the emitted registry.

Regenerate after any serializer/view/url/error change:

    make contract        # or: python -m stapel_vocabularies._codegen --out docs

The harness runs in a subprocess: this test process already configured Django
on the bare test urlconf, and the harness needs its own canonical-prefix
urlconf plus the drf-spectacular singleton.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

_PY = sys.version_info[:2]
if _PY != (3, 12):
    pytest.skip(
        "contract tests require Python 3.12 (the CI/monolith pin) — running "
        f"{_PY[0]}.{_PY[1]}. drf-spectacular renders component descriptions "
        "differently across Python minors, so drift checks are only defined "
        "on 3.12.",
        allow_module_level=True,
    )

REPO = Path(__file__).resolve().parent.parent
DOCS = REPO / "docs"
TRIAD = ("schema.json", "flows.json", "errors.json")
ARTIFACTS = TRIAD + ("capabilities.json", "llms.txt")
#: Must match the Makefile's `contract` / `contract-check` targets.
LLMS_TXT_BUDGET = "4000"


def _emit(out_dir: Path) -> None:
    for module in ("stapel_vocabularies._codegen", "stapel_vocabularies._capabilities"):
        subprocess.run(
            [sys.executable, "-m", module, "--out", str(out_dir)],
            cwd=str(REPO),
            check=True,
            capture_output=True,
        )
    # llms.txt is rendered from the REAL committed docs/capabilities.json, the
    # same as `make contract-check`, so this also catches a stale llms.txt
    # independently of the loop above.
    subprocess.run(
        [
            sys.executable, "-m", "stapel_tools.llms_txt", ".",
            "--out", str(out_dir), "--budget", LLMS_TXT_BUDGET,
        ],
        cwd=str(REPO),
        check=True,
        capture_output=True,
    )


def test_contract_artifacts_committed():
    for name in ARTIFACTS:
        assert (DOCS / name).is_file(), f"missing docs/{name} — run `make contract`"
    assert (DOCS / "capabilities.meta.json").is_file()
    assert (DOCS / "vocabulary-fixture.schema.json").is_file()


def test_contract_has_no_drift(tmp_path):
    _emit(tmp_path)
    for name in ARTIFACTS:
        assert (DOCS / name).read_bytes() == (tmp_path / name).read_bytes(), (
            f"docs/{name} drifted — run `make contract` and commit docs/{name}"
        )


def test_emission_is_deterministic(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    _emit(a)
    _emit(b)
    for name in ARTIFACTS:
        assert (a / name).read_bytes() == (b / name).read_bytes()


def test_paths_carry_the_canonical_prefix():
    schema = json.loads((DOCS / "schema.json").read_text())
    assert schema["paths"], "schema has no paths"
    assert all(path.startswith("/vocabularies/api/v1/") for path in schema["paths"])


def test_every_endpoint_is_emitted():
    """Four reads: the catalogue, one of them, the terms, the resolve."""
    schema = json.loads((DOCS / "schema.json").read_text())
    assert set(schema["paths"]) == {
        "/vocabularies/api/v1/vocabularies/",
        "/vocabularies/api/v1/vocabularies/{slug}/",
        "/vocabularies/api/v1/vocabularies/{slug}/terms/",
        "/vocabularies/api/v1/vocabularies/{slug}/terms/resolve/",
    }


def test_flows_are_empty_no_flow_step_annotations():
    assert json.loads((DOCS / "flows.json").read_text()) == []


def test_schema_refs_are_self_contained():
    schema = json.loads((DOCS / "schema.json").read_text())
    components = schema.get("components", {}).get("schemas", {})
    referenced = set(re.findall(r'"#/components/schemas/([^"]+)"', json.dumps(schema)))
    assert referenced <= set(components), referenced - set(components)


def test_this_modules_error_keys_are_in_the_registry():
    """The three keys the views return, as the emitted registry sees them."""
    from stapel_vocabularies.errors import STAPEL_VOCABULARIES_ERRORS

    emitted = {entry["code"] for entry in json.loads((DOCS / "errors.json").read_text())}
    assert set(STAPEL_VOCABULARIES_ERRORS) <= emitted


def test_the_capabilities_axis_is_the_resolver_switch():
    capabilities = json.loads((DOCS / "capabilities.json").read_text())
    assert [axis["key"] for axis in capabilities["axes"]] == ["REGISTER_RESOLVER"]
    # It changes who ANSWERS, never which endpoints exist — so it gates
    # nothing, and the read surface is one always-on block.
    assert capabilities["axes"][0]["gates"]["operations"] == []
    assert capabilities["operations_total"] == 4
