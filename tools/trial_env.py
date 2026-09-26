#!/usr/bin/env python3
"""The trial environment: one versioned spec, an idempotent bootstrap, and a
cheap preflight that REFUSES before a long run.

    python3 tools/trial_env.py bootstrap                  # into THIS interpreter (CI)
    python3 tools/trial_env.py bootstrap --venv ~/work/venv   # the build box
    python3 tools/trial_env.py preflight --tools iverilog vvp

WHY. The same #255 run needed `make`, `pyyaml`, `pyserial`, `origin/main` and a
reference path repaired by hand on the build box, one failure at a time, each
found after work had started (docs/trials.md section 1). The laptop, the box and
CI each had a different interpreter and simulator. A verdict that depends on
which machine answered is not a verdict.

So: spec/trial-environment.json is the ONE specification. The box and CI both
run `bootstrap` from it; `tools/trial.py` runs `preflight` against it before
any child starts and returns NO VERDICT when it is not met.

IDEMPOTENT. `bootstrap` inspects first and acts only on what differs: a second
run on a satisfied machine installs nothing and downloads nothing, and says so
(`actions: []`). The oss-cad-suite archive is reused when its recorded release
and sha256 equal tools/setup_ci_oss_cad.py's pins.

Exit: 0 satisfied, 2 refused (setup absent or wrong -- no evidence, not a fail).
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SPEC = ROOT / "spec" / "trial-environment.json"


def load_spec(path: pathlib.Path = SPEC) -> dict:
    spec = json.loads(pathlib.Path(path).read_text())
    for key in ("version", "python", "packages", "toolchain"):
        if key not in spec:
            raise ValueError(f"{path}: missing {key!r}")
    return spec


def tool_path(spec: dict, root: pathlib.Path = ROOT, base: str | None = None) -> str:
    """PATH with the pinned toolchain's bin first, when it is installed."""
    base = os.environ.get("PATH", "") if base is None else base
    pinned = root / spec["toolchain"]["bin"]
    return f"{pinned}{os.pathsep}{base}" if pinned.is_dir() else base


def tool_identity(name: str, want: dict, path: str) -> tuple[str | None, str | None]:
    """(version line, problem). The version is read at the point of use."""
    exe = shutil.which(name, path=path)
    if exe is None:
        return None, f"tool {name!r} not found on PATH"
    try:
        r = subprocess.run([exe, want.get("version_arg", "--version")], capture_output=True,
                           text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"tool {name!r} did not report a version: {exc}"
    line = next((ln.strip() for ln in (r.stdout + r.stderr).splitlines() if ln.strip()), "")
    if want.get("version_contains") and want["version_contains"] not in line:
        return line, (f"tool {name!r} at {exe} is {line!r}; the spec requires "
                      f"{want['version_contains']!r}")
    return line, None


def package_versions(spec: dict, python: str | None = None) -> dict:
    """{distribution: installed version or None}, from the interpreter that will
    run the checkers (not necessarily this one)."""
    names = list(spec["packages"])
    if python is None or os.path.realpath(python) == os.path.realpath(sys.executable):
        from importlib import metadata
        out = {}
        for n in names:
            try:
                out[n] = metadata.version(n)
            except metadata.PackageNotFoundError:
                out[n] = None
        return out
    code = ("import json,sys\nfrom importlib import metadata\nout={}\n"
            "for n in sys.argv[1:]:\n"
            "  try: out[n]=metadata.version(n)\n"
            "  except metadata.PackageNotFoundError: out[n]=None\n"
            "print(json.dumps(out))")
    r = subprocess.run([python, "-c", code, *names], capture_output=True, text=True)
    if r.returncode != 0:
        return {n: None for n in names}
    return json.loads(r.stdout)


def preflight(spec: dict, *, tools: list[str], root: pathlib.Path = ROOT,
              python: str | None = None, path: str | None = None) -> tuple[list[str], dict]:
    """(problems, identity). Cheap: no simulation, no network. Every problem is a
    reason for NO VERDICT; an empty list means the environment IS the spec."""
    problems: list[str] = []
    python = python or sys.executable
    r = subprocess.run([python, "-c", "import sys; print('%d.%d.%d' % sys.version_info[:3])"],
                       capture_output=True, text=True)
    pyver = r.stdout.strip() or "?"
    if not (pyver + ".").startswith(spec["python"] + "."):
        problems.append(f"python {pyver} at {python}; the spec requires {spec['python']}")
    pkgs = package_versions(spec, python)
    for name, want in spec["packages"].items():
        have = pkgs.get(name)
        if have is None:
            problems.append(f"package {name} is not installed (spec {want})")
        elif have != want:
            problems.append(f"package {name} is {have}; the spec pins {want}")
    path = tool_path(spec, root) if path is None else path
    tool_ids = {}
    for name in tools:
        want = spec["toolchain"]["tools"].get(name)
        if want is None:
            problems.append(f"tool {name!r} is not in the environment spec")
            continue
        line, problem = tool_identity(name, want, path)
        tool_ids[name] = line
        if problem:
            problems.append(problem)
    identity = {"spec": spec["version"], "python": pyver, "python_exe": python,
                "packages": pkgs, "tools": tool_ids}
    return problems, identity


# ---- bootstrap --------------------------------------------------------------
def _installer():
    sys.path.insert(0, str(ROOT / "tools"))
    return importlib.import_module("setup_ci_oss_cad")


def toolchain_current(spec: dict, root: pathlib.Path = ROOT, installer=None) -> bool:
    """The archive on disk is the one tools/setup_ci_oss_cad.py pins."""
    installer = installer or _installer()
    rec = root / spec["toolchain"]["record"]
    try:
        got = json.loads(rec.read_text())
    except (OSError, ValueError):
        return False
    return (got.get("release") == installer.VERSION and got.get("sha256") == installer.SHA256
            and (root / spec["toolchain"]["bin"]).is_dir())


def plan_bootstrap(spec: dict, *, python_version: str, packages: dict,
                   toolchain_ok: bool, need_toolchain: bool) -> list[str]:
    """What bootstrap would do, from what is already there. Pure, so its
    idempotence is testable: a satisfied state plans nothing."""
    actions = []
    if not (python_version + ".").startswith(spec["python"] + "."):
        actions.append("python")
    if any(packages.get(n) != v for n, v in spec["packages"].items()):
        actions.append("packages")
    if need_toolchain and not toolchain_ok:
        actions.append("toolchain")
    return actions


def bootstrap(spec: dict, *, venv: pathlib.Path | None, need_toolchain: bool,
              root: pathlib.Path = ROOT) -> int:
    python = sys.executable
    if venv is not None:
        python = str(venv / "bin" / "python")
        if not pathlib.Path(python).exists():
            uv = shutil.which("uv")
            cmd = ([uv, "venv", "--python", spec["python"], str(venv)] if uv else
                   [f"python{spec['python']}", "-m", "venv", str(venv)])
            print(f"trial_env: creating {venv}: {' '.join(cmd)}")
            if subprocess.run(cmd).returncode != 0:
                print("trial_env: REFUSED -- could not create the interpreter the spec requires")
                return 2
    r = subprocess.run([python, "-c", "import sys; print('%d.%d.%d' % sys.version_info[:3])"],
                       capture_output=True, text=True)
    pyver = r.stdout.strip()
    actions = plan_bootstrap(spec, python_version=pyver, packages=package_versions(spec, python),
                             toolchain_ok=(toolchain_current(spec, root) if need_toolchain else True),
                             need_toolchain=need_toolchain)
    print(f"trial_env: spec {spec['version']}; python {python} ({pyver}); actions: {actions}")
    if "python" in actions:
        print(f"trial_env: REFUSED -- {python} is {pyver}, the spec requires {spec['python']}; "
              "pass --venv to create one")
        return 2
    if "packages" in actions:
        pins = [f"{n}=={v}" for n, v in spec["packages"].items()]
        uv = shutil.which("uv") if venv is not None else None
        cmd = ([uv, "pip", "install", "--python", python, *pins] if uv
               else [python, "-m", "pip", "install", "--quiet", *pins])
        if subprocess.run(cmd).returncode != 0:
            print("trial_env: REFUSED -- package install failed")
            return 2
    if "toolchain" in actions:
        env = dict(os.environ)
        env.setdefault("GITHUB_PATH", str(root / "build" / "oss-cad-pinned" / "github_path"))
        (root / "build" / "oss-cad-pinned").mkdir(parents=True, exist_ok=True)
        if subprocess.run([python, str(root / spec["toolchain"]["installer"])], env=env,
                          cwd=root).returncode != 0:
            print("trial_env: REFUSED -- the pinned toolchain did not install")
            return 2
    tools = list(spec["toolchain"]["tools"]) if need_toolchain else []
    problems, ident = preflight(spec, tools=tools, root=root, python=python)
    print(json.dumps(ident, indent=1))
    if problems:
        for p in problems:
            print(f"trial_env: REFUSED -- {p}")
        return 2
    print(f"trial_env: SATISFIED -- spec {spec['version']}"
          + (f"; export PATH={root / spec['toolchain']['bin']}:$PATH to use the pinned "
             "simulator outside tools/trial.py" if need_toolchain else ""))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("bootstrap")
    b.add_argument("--venv", type=pathlib.Path, default=None,
                   help="create/reuse this venv; default installs into this interpreter")
    b.add_argument("--no-toolchain", action="store_true",
                   help="skip the simulator (for trials whose modes need none)")
    p = sub.add_parser("preflight")
    p.add_argument("--tools", nargs="*", default=[])
    a = ap.parse_args(argv)
    spec = load_spec()
    if a.cmd == "bootstrap":
        return bootstrap(spec, venv=a.venv.expanduser() if a.venv else None,
                         need_toolchain=not a.no_toolchain)
    problems, ident = preflight(spec, tools=a.tools)
    print(json.dumps(ident, indent=1))
    for pr in problems:
        print(f"trial_env: REFUSED -- {pr}")
    return 2 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
