"""Pi install — drop the bundled JS extension into ~/.pi/agent/extensions/evo/,
register it in ~/.pi/agent/settings.json#extensions[], and copy the four
evo skills under ~/.pi/agent/skills/evo-<name>/.

Pi (pi.dev, ``@earendil-works/pi-coding-agent``) is the upstream agent
SDK that OpenClaw embeds. Pi's ExtensionAPI is the surface OpenClaw
exposes, so the bundled extension at ``evo/openclaw_plugin/evo.bundle.js``
loads cleanly on pi. We point at it directly rather than maintain a
duplicate bundle: same source, two install adapters.

Differs from the openclaw adapter in three deliberate omissions:
  1. No ``plugins.allow`` trust grants — pi has no per-plugin trust gate.
  2. No openclaw-native ``tool_result_persist`` plugin — that hook is
     openclaw-only. ``before_provider_request`` (used by the bundle) still
     fires on pi 0.75.3 (verified by live probe at trials/pi-probe/).
  3. No ``pi install <source>`` marketplace call for the skills — we
     copy SKILL.md files directly. The pi marketplace path is available
     if a published package is preferred later.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path


_SKILLS = ("discover", "optimize", "subagent", "infra-setup")


def _pi_base() -> Path:
    home_override = os.environ.get("PI_HOME")
    return Path(home_override) if home_override else Path.home() / ".pi"


def _pi_settings_file() -> Path:
    return _pi_base() / "agent" / "settings.json"


def _evo_extension_dir() -> Path:
    return _pi_base() / "agent" / "extensions" / "evo"


def _evo_extension_path() -> Path:
    return _evo_extension_dir() / "index.js"


def _pi_skills_dir() -> Path:
    return _pi_base() / "agent" / "skills"


def _bundled_extension_source() -> Path | None:
    """Return the prebuilt JS extension bundle. Shared with openclaw —
    the bundle targets pi's ExtensionAPI which openclaw embeds."""
    here = Path(__file__).resolve().parent.parent  # evo/
    bundle = here / "openclaw_plugin" / "evo.bundle.js"
    return bundle if bundle.exists() else None


def _skills_source_root(from_path: str | None = None) -> Path:
    """Locate the plugins/evo/skills/ source dir.

    Resolution order:
      1. ``--from-path`` (live tests, manual installs from a clone) —
         expects either the plugin root or a parent containing
         ``plugins/evo/skills/``.
      2. Development checkout via ``__file__`` walk — works when running
         against ``plugins/evo/src/`` directly.

    Wheel-installed users have no local skills/ dir and need to fetch
    skills separately (same situation as hermes/opencode today). The
    install command logs a warning and continues with the extension
    install in that case.
    """
    if from_path:
        base = Path(from_path)
        # Two layouts: full repo tarball (parent of plugins/evo) OR a
        # bare plugin checkout (already at plugins/evo).
        for candidate in (base / "plugins" / "evo" / "skills", base / "skills"):
            if candidate.exists():
                return candidate
    # __file__ = .../plugins/evo/src/evo/host_install/pi.py
    return Path(__file__).resolve().parents[3] / "skills"


def _update_settings_extensions(settings: Path, target: str) -> bool:
    """Idempotent register: add `target` to settings.json#extensions[] if
    not already present. Returns True if file was modified.
    """
    settings.parent.mkdir(parents=True, exist_ok=True)
    data: dict = {}
    if settings.exists():
        try:
            data = json.loads(settings.read_text())
        except json.JSONDecodeError:
            print(f"ERROR: could not parse {settings}", file=sys.stderr)
            raise
    extensions = data.setdefault("extensions", [])
    if target in extensions:
        return False
    extensions.append(target)
    settings.write_text(json.dumps(data, indent=2) + "\n")
    return True


def _install_skills(from_path: str | None = None) -> int:
    """Copy each evo skill into ~/.pi/agent/skills/evo-<name>/.

    Pi auto-discovers any directory under skills/ that contains a
    SKILL.md. Namespacing the dirs as `evo-<name>` avoids colliding
    with non-evo skills the user already has.
    """
    src_root = _skills_source_root(from_path)
    if not src_root.exists():
        print(f"WARNING: evo skills source not found at {src_root}; "
              f"skipping skill install", file=sys.stderr)
        return 0
    dest_root = _pi_skills_dir()
    dest_root.mkdir(parents=True, exist_ok=True)
    copied = 0
    for name in _SKILLS:
        src = src_root / name
        if not src.exists():
            print(f"  - {name}: source missing at {src}, skipping")
            continue
        dest = dest_root / f"evo-{name}"
        # Wipe + recopy so stale files from a prior version don't linger.
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(src, dest)
        print(f"  + installed skill: {dest}")
        copied += 1
    return copied


def install(args: argparse.Namespace) -> int:
    src = _bundled_extension_source()
    if src is None:
        print(
            "ERROR: bundled pi extension not found in this evo install.\n"
            "  Expected at evo/openclaw_plugin/evo.bundle.js (built via `bun build`).",
            file=sys.stderr,
        )
        return 2

    if shutil.which("pi") is None:
        print(
            "NOTE: `pi` binary not on PATH. Install pi first:\n"
            "  npm install -g @earendil-works/pi-coding-agent\n"
            "Continuing anyway — file install does not require the binary.",
            file=sys.stderr,
        )

    ext_dir = _evo_extension_dir()
    ext_dir.mkdir(parents=True, exist_ok=True)
    ext_path = _evo_extension_path()
    shutil.copyfile(src, ext_path)
    print(f"installed extension: {ext_path}")

    settings = _pi_settings_file()
    target = str(ext_path)
    if _update_settings_extensions(settings, target):
        print(f"added {target} to extensions in {settings}")
    else:
        print(f"{target} already in extensions in {settings}")

    print("\nInstalling evo skills under ~/.pi/agent/skills/evo-*/ ...")
    _install_skills(getattr(args, "from_path", None))

    print("\nRestart any running pi session to load the extension and skills.")
    return 0


def uninstall(args: argparse.Namespace) -> int:
    ext_path = _evo_extension_path()
    target = str(ext_path)

    settings = _pi_settings_file()
    if settings.exists():
        try:
            data = json.loads(settings.read_text())
            extensions = data.get("extensions", [])
            if target in extensions:
                extensions.remove(target)
                settings.write_text(json.dumps(data, indent=2) + "\n")
                print(f"removed {target} from {settings}")
        except json.JSONDecodeError:
            pass

    if ext_path.exists():
        ext_path.unlink()
        print(f"removed {ext_path}")
    # Best-effort dir cleanup.
    if ext_path.parent.exists() and not any(ext_path.parent.iterdir()):
        ext_path.parent.rmdir()

    skills_root = _pi_skills_dir()
    for name in _SKILLS:
        d = skills_root / f"evo-{name}"
        if d.exists():
            shutil.rmtree(d)
            print(f"removed {d}")
    return 0


def doctor(args: argparse.Namespace) -> int:
    ext_path = _evo_extension_path()
    settings = _pi_settings_file()
    rc = 0

    if ext_path.exists():
        print(f"✓ extension installed: {ext_path}")
    else:
        print(f"✗ extension not at {ext_path}")
        print("  Run: evo install pi")
        rc = 1

    if settings.exists():
        try:
            data = json.loads(settings.read_text())
            target = str(ext_path)
            if target in data.get("extensions", []):
                print(f"✓ extension registered in {settings}")
            else:
                print(f"✗ extension not in {settings} extensions list")
                print("  Run: evo install pi")
                rc = 1
        except json.JSONDecodeError:
            print(f"✗ could not parse {settings}")
            rc = 1
    else:
        print(f"✗ {settings} not found (pi may not have run yet)")
        rc = 1

    skills_root = _pi_skills_dir()
    missing = [name for name in _SKILLS
               if not (skills_root / f"evo-{name}" / "SKILL.md").exists()]
    if missing:
        print(f"✗ missing skills: {', '.join(missing)}")
        print("  Run: evo install pi")
        rc = 1
    else:
        print(f"✓ all {len(_SKILLS)} evo skills installed under {skills_root}/evo-*/")

    if shutil.which("pi") is None:
        print("? `pi` binary not on PATH (run `npm install -g @earendil-works/pi-coding-agent`)")
    return rc
