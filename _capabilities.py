"""stapel-vocabularies capabilities.json emitter — thin shim over stapel_tools.capabilities."""
from pathlib import Path

from stapel_tools.capabilities import axis_group_rules, run_capabilities_cli

#: The one CTO-facing config axis (capability-config.md §16): does this
#: service answer vocabulary questions itself, or over comm. Every other
#: DEFAULTS key is a tuning knob (cache ages, page sizes, batch size).
_AXES = {"REGISTER_RESOLVER"}


def main(argv=None):
    from stapel_vocabularies._codegen import _configure

    _configure()
    from stapel_vocabularies.conf import DEFAULTS
    from stapel_vocabularies.urls import GATE_REGISTRY

    return run_capabilities_cli(
        argv,
        repo=Path(__file__).resolve().parent,
        canonical_prefix="/vocabularies/api/v1",
        defaults=DEFAULTS,
        registry=GATE_REGISTRY,
        is_axis=lambda k: k in _AXES,
        axis_group=axis_group_rules(
            exact={"REGISTER_RESOLVER": "vocabularies.resolver"}
        ),
        prog="stapel-vocabularies-capabilities",
    )


if __name__ == "__main__":
    raise SystemExit(main())
