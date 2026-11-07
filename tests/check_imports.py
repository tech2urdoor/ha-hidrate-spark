"""Imports every module of the integration against a real Home Assistant.

The other tests stub Home Assistant out, and hassfest and HACS only read the manifest, so none of them can notice
that the code asks Home Assistant for something it doesn't have. This one can: the entity descriptions are built at
import, so a wrong device class or entity category fails here.

    python tests/check_imports.py --requirements   # prints what Home Assistant's bluetooth integration needs
    python tests/check_imports.py
"""

import importlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = "custom_components.hidrate_spark"


def bluetooth_requirements() -> list[str]:
    """Home Assistant installs these per integration at runtime, so a bare pip install lacks them."""
    import homeassistant.components

    components = Path(homeassistant.components.__file__).parent
    seen: set[str] = set()
    wanted: list[str] = []
    todo = ["bluetooth"]
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        manifest = json.loads((components / name / "manifest.json").read_text())
        wanted += manifest.get("requirements", [])
        todo += manifest.get("dependencies", []) + manifest.get("after_dependencies", [])
    return sorted(set(wanted))


def main() -> int:
    if "--requirements" in sys.argv:
        print("\n".join(bluetooth_requirements()))
        return 0
    sys.path.insert(0, str(ROOT))
    modules = sorted((ROOT / "custom_components" / "hidrate_spark").glob("*.py"))
    failed = 0
    for path in modules:
        name = PACKAGE if path.stem == "__init__" else f"{PACKAGE}.{path.stem}"
        try:
            importlib.import_module(name)
        except Exception as err:  # noqa: BLE001 - every failure gets reported, not just the first
            failed += 1
            print(f"FAILED {name}: {type(err).__name__}: {err}")
        else:
            print(f"ok     {name}")
    if not modules or failed:
        print(f"{failed} of {len(modules)} modules failed to import")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
