#!/usr/bin/env python3
"""
Office Hub Installer - The Ultimate Lightweight Blueprint
=========================================================
For: clubs, HOAs, non-profits, community groups.
Zero Docker. Runs on a normal office desktop.

What this installer does, in order:
  1. AUDIT       - measures CPU, RAM, disk, OS, and required tooling.
  2. DECIDE      - chooses dependencies automatically from the audit.
  3. PRIVACY     - the setup person picks how much privacy is needed:
                     1 = retrieval-only (100% offline, no AI answers)
                     2 = local AI    (Qwen on this machine, fully offline)
                     3 = cloud AI    (free-tier API, data leaves the building)
  4. MODEL       - picks the best Apache-2.0 Qwen GGUF that fits the RAM.
  5. DEPS        - creates a local venv and installs Python deps.
  6. CONFIG      - writes config.json that the hub runs on.
  7. DOWNLOADS   - optional: llama.cpp server binary + Qwen model file.
  8. REPORT      - prints what was decided and how to start everything.

Usage:
    python3 install.py                 # interactive (recommended)
    python3 install.py --yes           # accept all recommended defaults
    python3 install.py --privacy 2     # pre-select privacy level
    python3 install.py --skip-models   # offline: skip llama.cpp/Qwen downloads
    python3 install.py --reconfigure   # change privacy/model on existing install
"""

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import venv
from pathlib import Path

import check_deps

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
VENV_DIR = ROOT / ".venv"
AI_DIR = ROOT / "ai"
MODELS_DIR = AI_DIR / "models"
LLAMA_DIR = AI_DIR / "llama.cpp"

# Apache-2.0 licensed Qwen GGUFs (quantized by Qwen themselves).
# NOTE: 3B is deliberately excluded - it uses the restrictive Qwen license.
MODEL_CATALOG = [
    {
        "key": "qwen2.5-0.5b-instruct-q4_k_m.gguf",
        "url": "https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/main/qwen2.5-0.5b-instruct-q4_k_m.gguf",
        "label": "Qwen2.5 0.5B Instruct (Q4_K_M)",
        "ram_gb": 1.2, "size_gb": 0.4, "quality": 1,
    },
    {
        "key": "qwen2.5-1.5b-instruct-q4_k_m.gguf",
        "url": "https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/qwen2.5-1.5b-instruct-q4_k_m.gguf",
        "label": "Qwen2.5 1.5B Instruct (Q4_K_M)",
        "ram_gb": 2.0, "size_gb": 1.0, "quality": 2,
    },
    {
        "key": "qwen2.5-7b-instruct-q4_k_m.gguf",
        "url": "https://huggingface.co/Qwen/Qwen2.5-7B-Instruct-GGUF/resolve/main/qwen2.5-7b-instruct-q4_k_m.gguf",
        "label": "Qwen2.5 7B Instruct (Q4_K_M)",
        "ram_gb": 6.0, "size_gb": 4.7, "quality": 3,
    },
]

PRIVACY_LEVELS = {
    "1": {
        "mode": "retrieval_only",
        "name": "Retrieval-only (maximum privacy)",
        "desc": "AI answers are composed ONLY from your own documents, on this machine. "
                "No model download, no API key, works 100% offline. Best for: minutes, "
                "financials, anything you would not put in an envelope.",
    },
    "2": {
        "mode": "local",
        "name": "Local AI (private + generative)",
        "desc": "A Qwen model runs quietly on this desktop. Documents never leave the "
                "building. Needs the one-time model download (~1-5 GB) and ~2 GB RAM. "
                "Best for: drafting letters, summarizing minutes, answering member questions.",
    },
    "3": {
        "mode": "cloud",
        "name": "Cloud AI (free-tier API, most capable)",
        "desc": "Questions go to a free cloud model (OpenRouter / Groq free tiers). "
                "Most capable, zero local RAM, but document snippets LEAVE the building. "
                "Best for: public info - events, bylaws summaries, general help. "
                "Key is read from an environment variable, never stored in config.",
    },
}

CLOUD_PRESETS = {
    "openrouter": {"base_url": "https://openrouter.ai/api/v1", "api_key_env": "OPENROUTER_API_KEY",
                   "model": "qwen/qwen-2.5-72b-instruct:free", "hint": "openrouter.ai/keys"},
    "groq": {"base_url": "https://api.groq.com/openai/v1", "api_key_env": "GROQ_API_KEY",
             "model": "llama-3.1-8b-instant", "hint": "console.groq.com/keys"},
}


# ----------------------------------------------------------------------------
# 1. AUDIT
# ----------------------------------------------------------------------------

def which_any(names):
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    return None


def read_meminfo_mb():
    """Return (total_mb, available_mb) of RAM, cross-platform."""
    system = platform.system()
    try:
        if system == "Linux":
            info = {}
            for line in Path("/proc/meminfo").read_text().splitlines():
                k, _, v = line.partition(":")
                info[k.strip()] = int(v.strip().split()[0])  # kB
            return info["MemTotal"] // 1024, info["MemAvailable"] // 1024
        if system == "Darwin":
            total = int(subprocess.check_output(["sysctl", "-n", "hw.memsize"]).strip()) // (1024 * 1024)
            vm = subprocess.check_output(["vm_stat"]).decode()
            page = 4096
            free = 0
            for line in vm.splitlines():
                if "Pages free" in line:
                    free = int(line.split(":")[1].strip().rstrip(".")) * page // (1024 * 1024)
            return total, free
        if system == "Windows":
            import ctypes
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            st = MEMORYSTATUSEX(); st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
            return int(st.ullTotalPhys // (1024 * 1024)), int(st.ullAvailPhys // (1024 * 1024))
    except Exception:
        pass
    return 0, 0


def audit():
    system = platform.system()
    machine = platform.machine()
    cpu = os.cpu_count() or 1
    total_mb, avail_mb = read_meminfo_mb()
    free_disk_gb = shutil.disk_usage(ROOT).free / (1024 ** 3)
    tools = {
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        "php": bool(which_any(["php"])),
        "mariadb": bool(which_any(["mariadbd", "mysqld", "mariadb"])),
        "git": bool(which_any(["git"])),
        "gh": bool(which_any(["gh"])),
        "composer": bool(which_any(["composer", "composer.bat", "composer.phar"])),
        "curl": bool(which_any(["curl"])),
    }
    return {
        "os": f"{system} {platform.release()}",
        "machine": machine,
        "cpu_cores": cpu,
        "ram_total_mb": total_mb,
        "ram_available_mb": avail_mb,
        "free_disk_gb": round(free_disk_gb, 1),
        "tools": tools,
    }


# ----------------------------------------------------------------------------
# 2. DECIDE (dependencies, from the audit - no human input needed)
# ----------------------------------------------------------------------------

def decide(a):
    """Turn audit facts into install decisions."""
    ram_gb = a["ram_total_mb"] / 1024.0
    decisions = []

    decisions.append(("Python venv + FastAPI glue service", True,
                      "core - always required (AI glue, document index, web API)"))
    decisions.append(("Python PDF reader (pypdf)", True,
                      "core - lets the AI read PDFs in documents/"))

    can_local = ram_gb >= 3.5 and a["free_disk_gb"] >= 4
    decisions.append(("Local AI engine (llama.cpp, CPU only)", can_local,
                      "needs >= 3.5 GB RAM and 4 GB free disk" if not can_local
                      else "fits: llama.cpp is a single binary, no service, no Docker"))

    if not a["tools"]["php"]:
        decisions.append(("PHP 8.2+ for Admidio + Flarum", False,
                          "MISSING - install PHP to run the member portal / forum "
                          "(Windows: php.net zip; Linux: sudo apt install php php-mysql php-sqlite3)"))
    else:
        decisions.append(("PHP for Admidio + Flarum", True, "already present"))

    if not a["tools"]["mariadb"]:
        decisions.append(("MariaDB (members + forum database)", False,
                          "MISSING - required by Admidio & Flarum "
                          "(Windows: portable zip from mariadb.org; Linux: sudo apt install mariadb-server)"))
    else:
        decisions.append(("MariaDB", True, "already present"))

    if not a["tools"]["gh"]:
        decisions.append(("GitHub CLI (auto-download of AI binaries)", a["tools"]["curl"] is not None,
                          "optional - makes downloads one command; get it from cli.github.com"))

    return decisions


def offer_system_install(a, args):
    """Offer to install missing system packages (PHP, MariaDB, gh) on Linux."""
    missing = [name for name in ("php", "mariadb") if not a["tools"].get(name)]
    if not a["tools"].get("gh"):
        missing.append("gh")
    if not missing:
        return
    pm = check_deps.pkg_manager()
    if pm is None:
        print("\n  [setup] Missing system packages (" + ", ".join(missing) + ") - "
              "no package manager detected (Windows?). Install manually:\n"
              "    PHP 8.1+      https://windows.php.net/download (zip, add to PATH)\n"
              "    MariaDB       https://mariadb.org/download (portable zip works)\n"
              "    GitHub CLI    https://cli.github.com")
        return
    cmds = {
        "apt-get": ["sudo", "apt-get", "install", "-y", "php-cli", "php-mysql",
                    "php-mbstring", "php-gd", "php-xml", "php-curl", "mariadb-server"],
        "dnf": ["sudo", "dnf", "install", "-y", "php", "php-mysqlnd", "php-mbstring",
                "php-gd", "php-xml", "php-curl", "mariadb-server"],
        "pacman": ["sudo", "pacman", "-S", "--noconfirm", "php", "php-intl",
                   "mariadb"],
    }
    cmd = cmds[pm] + (["gh"] if pm != "pacman" else [])
    if pm == "pacman":
        cmd[cmd.index("mariadb") + 1:cmd.index("mariadb") + 1] = ["github-cli"]
    print(f"\n  [setup] Missing system packages: {', '.join(missing)}")
    print("  Would run: " + " ".join(cmd))
    if args.auto_install_system:
        run = True
    elif args.yes:
        run = False
        print("  (--yes does not touch system packages; re-run with "
              "--auto-install-system to install them automatically)")
    else:
        raw = ask("  Attempt automatic install now? [y/N] ", "n").lower()
        run = raw.startswith("y")
    if run:
        print("  (you may be asked for your password)\n")
        subprocess.run(cmd)
        print("\n  [setup] Done - the readiness check at the end will confirm.")


def pick_model(a):
    """Best Apache-2.0 Qwen that fits, with headroom for the office desktop."""
    ram_gb = a["ram_total_mb"] / 1024.0
    # Leave the OS + PHP + DB room: never plan to use more than 45% of RAM for the model.
    budget = max(0.0, ram_gb * 0.45)
    best = None
    for m in MODEL_CATALOG:
        if m["ram_gb"] <= budget:
            best = m  # catalog is ordered small->large; keep the largest that fits
    if best is None:
        return None
    return best


# ----------------------------------------------------------------------------
# small helpers
# ----------------------------------------------------------------------------

def ask(prompt, default=None):
    try:
        raw = input(prompt).strip()
    except EOFError:
        raw = ""
    return raw or (default or "")


def menu(title, options, default_idx=0):
    """Numbered menu. options: list of (label, description). Returns index."""
    print(f"\n{title}")
    for i, (label, desc) in enumerate(options, 1):
        star = "  <-- recommended" if i == default_idx + 1 else ""
        print(f"  {i}) {label}{star}")
        if desc:
            print(f"       {desc}")
    while True:
        raw = ask(f"Choose [1-{len(options)}, Enter = {default_idx + 1}]: ", str(default_idx + 1))
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return int(raw) - 1
        print("  Invalid choice.")


def hr(title):
    print(f"\n{'=' * 70}\n  {title}\n{'=' * 70}")


# ----------------------------------------------------------------------------
# 5. DEPS
# ----------------------------------------------------------------------------

def install_deps():
    hr("STEP 5 - Installing Python dependencies (into local .venv)")
    if not VENV_DIR.exists():
        print("  Creating virtual environment .venv ...")
        venv.EnvBuilder(with_pip=True).create(VENV_DIR)
    py = VENV_DIR / ("Scripts/python.exe" if platform.system() == "Windows" else "bin/python")
    print("  Installing: fastapi uvicorn pypdf  (pinned, small, pure-python where possible)")
    subprocess.check_call([str(py), "-m", "pip", "install", "--quiet",
                           "fastapi", "uvicorn", "pypdf"])
    print("  Done.")
    return py


# ----------------------------------------------------------------------------
# 6. CONFIG
# ----------------------------------------------------------------------------

def write_config(a, privacy_mode, model, cloud, py):
    threads = max(2, (a["cpu_cores"] or 4) - 2)
    system = platform.system()
    llama_bin = {
        "Windows": "ai/llama.cpp/llama-server.exe",
        "Linux": "ai/llama.cpp/llama-server",
        "Darwin": "ai/llama.cpp/llama-server",
    }.get(system, "ai/llama.cpp/llama-server")

    cfg = {
        "version": 1,
        "privacy_mode": privacy_mode,
        "python": str(py.relative_to(ROOT)),
        "cloud": {
            "base_url": (cloud or {}).get("base_url", ""),
            "model": (cloud or {}).get("model", ""),
            "api_key_env": (cloud or {}).get("api_key_env", "HUB_CLOUD_API_KEY"),
            "note": "Key is read from this environment variable at runtime - never stored here.",
        },
        "local_ai": {
            "llamacpp_path": llama_bin,
            "model_path": f"ai/models/{model['key']}" if model else "",
            "host": "127.0.0.1", "port": 8082,
            "ctx": 4096, "threads": threads, "gpu_layers": 0,
        },
        "index": {
            "documents_dir": "documents",
            "db_path": "data/index.db",
            "top_k": 4, "chunk_chars": 1200, "chunk_overlap": 150,
        },
        "server": {"host": "0.0.0.0", "port": 8090},
        "audit": a,
        "model_label": model["label"] if model else "",
    }
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
    (ROOT / "documents").mkdir(exist_ok=True)
    (ROOT / "data").mkdir(exist_ok=True)
    print(f"  Wrote {CONFIG_PATH.name}")
    return cfg


# ----------------------------------------------------------------------------
# 7. DOWNLOADS (llama.cpp via gh, Qwen GGUF via HuggingFace)
# ----------------------------------------------------------------------------

def download_file(url, dest: Path):
    """Stream a file with plain urllib (no external deps needed)."""
    import urllib.request
    print(f"  Downloading {dest.name} ({url.split('/')[-1]}) ...")
    tmp = dest.with_suffix(dest.suffix + ".part")

    def progress(blocks, bs, total):
        if total > 0 and blocks % 400 == 0:
            pct = min(100, blocks * bs * 100 // total)
            print(f"\r    {pct}% of ~{total // (1024 * 1024)} MB", end="", flush=True)

    urllib.request.urlretrieve(url, tmp, reporthook=progress)
    print()
    tmp.rename(dest)


def download_llamacpp():
    """Use the GitHub CLI to fetch the right prebuilt llama.cpp release binary."""
    if not shutil.which("gh"):
        print("  SKIP: gh CLI not found. Get llama-server manually from "
              "https://github.com/ggml-org/llama.cpp/releases and place it in ai/llama.cpp/")
        return False
    system = platform.system()
    patterns = {"Windows": "*-bin-win-cpu-x64.zip",
                "Linux": "*-bin-ubuntu-x64.tar.gz",
                "Darwin": "*-bin-macos-arm64.tar.gz" if platform.machine() == "arm64"
                          else "*-bin-macos-x64.tar.gz"}
    pattern = patterns.get(system)
    if not pattern:
        print(f"  SKIP: no prebuilt llama.cpp pattern for {system}")
        return False
    LLAMA_DIR.mkdir(parents=True, exist_ok=True)
    tag = subprocess.check_output(
        ["gh", "release", "view", "--repo", "ggml-org/llama.cpp", "--json", "tagName",
         "-q", ".tagName"], text=True).strip()
    print(f"  Fetching llama.cpp {tag} ({pattern}) via gh ...")
    subprocess.check_call(["gh", "release", "download", tag, "--repo", "ggml-org/llama.cpp",
                           "--pattern", pattern, "--clobber"], cwd=LLAMA_DIR)
    archive = next(LLAMA_DIR.glob(pattern), None)
    if archive is None:
        print("  WARNING: no llama.cpp archive matched the download pattern - "
              "grab llama-server manually from github.com/ggml-org/llama.cpp/releases")
        return False
    if archive.suffix == ".zip":
        if shutil.which("unzip"):
            subprocess.check_call(["unzip", "-o", "-q", archive.name], cwd=LLAMA_DIR)
        else:
            import zipfile
            with zipfile.ZipFile(LLAMA_DIR / archive.name) as z:
                z.extractall(LLAMA_DIR)
    else:
        subprocess.check_call(["tar", "-xzf", archive.name], cwd=LLAMA_DIR)
    archive.unlink()
    # locate or hoist llama-server
    exe = "llama-server.exe" if system == "Windows" else "llama-server"
    found = next(LLAMA_DIR.rglob(exe), None)
    if found and found.parent != LLAMA_DIR:
        shutil.move(str(found), LLAMA_DIR / exe)
    if found:
        if system != "Windows":
            os.chmod(LLAMA_DIR / exe, 0o755)
        print(f"  OK: {LLAMA_DIR / exe}")
        return True
    print("  WARNING: llama-server not found in archive - check ai/llama.cpp/")
    return False


def download_model(model):
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    dest = MODELS_DIR / model["key"]
    if dest.exists():
        print(f"  Model already present: {dest.name}")
        return True
    print(f"  Model: {model['label']}  (~{model['size_gb']} GB download)")
    try:
        download_file(model["url"], dest)
        return True
    except Exception as e:
        print(f"\n  DOWNLOAD FAILED: {e}\n  You can retry later or place the file manually at: {dest}")
        return False


# ----------------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Office Hub installer")
    ap.add_argument("--yes", action="store_true", help="accept all recommended defaults")
    ap.add_argument("--privacy", choices=["1", "2", "3"], help="pre-select privacy level")
    ap.add_argument("--skip-models", action="store_true", help="offline install: skip downloads")
    ap.add_argument("--auto-install-system", action="store_true",
                    help="install missing system packages (PHP, MariaDB, gh) via the OS package manager")
    ap.add_argument("--auto-cloud", action="store_true",
                    help="with --yes --privacy 3: also set a placeholder cloud API key env var")
    ap.add_argument("--reconfigure", action="store_true", help="change privacy/model, keep everything")
    args = ap.parse_args()

    hr("OFFICE HUB INSTALLER - clubs / HOAs / non-profits")
    print("  Zero Docker. One office desktop. 100% open source (Apache-2.0 AI core).")

    hr("STEP 1 - Auditing this machine")
    a = audit()
    for k, v in a.items():
        if k != "tools":
            print(f"  {k:16}: {v}")
    print(f"  {'tools':16}: " + ", ".join(f"{k}={'yes' if v else 'NO'}" for k, v in a["tools"].items()))

    hr("STEP 2 - Dependency decisions (automatic)")
    for name, ok, note in decide(a):
        print(f"  [{'INSTALL' if ok else 'NEEDED '}] {name}\n              {note}")
    offer_system_install(a, args)

    hr("STEP 3 - Privacy choice (made by you, the setup person)")
    print("  This single choice decides where member data and AI answers go.\n")
    opts = [(PRIVACY_LEVELS[k]["name"], PRIVACY_LEVELS[k]["desc"]) for k in ("1", "2", "3")]
    if args.privacy:
        pi = int(args.privacy) - 1
        print(f"  Pre-selected via --privacy: {opts[pi][0]}")
    elif args.yes:
        pi = 0  # safest default
        print(f"  --yes: using safest default -> {opts[0][0]}")
    else:
        pi = menu("How much privacy is needed for this organization?", opts, default_idx=0)
    privacy_mode = PRIVACY_LEVELS[["1", "2", "3"][pi]]["mode"]
    print(f"  >> Privacy mode: {privacy_mode.upper()}")

    hr("STEP 4 - AI model selection")
    model = pick_model(a)
    cloud = None
    if privacy_mode == "local":
        if model:
            print(f"  Best Apache-2.0 fit for {a['ram_total_mb'] // 1024} GB RAM: {model['label']} "
                  f"(~{model['ram_gb']} GB RAM in use)")
        else:
            print("  RAM is too small for local generation - falling back to retrieval-only.")
            privacy_mode = "retrieval_only"
    elif privacy_mode == "cloud":
        keys = list(CLOUD_PRESETS)
        if args.yes or args.auto_cloud:
            ci = 0
        else:
            ci = menu("Which free-tier provider?", 
                      [(k.capitalize(), f"base: {v['base_url']}  key: {v['api_key_env']} ({v['hint']})")
                       for k, v in CLOUD_PRESETS.items()], default_idx=0)
        cloud = CLOUD_PRESETS[keys[ci]]
        print(f"  >> Cloud provider: {keys[ci]}  (set {cloud['api_key_env']} before starting)")
    else:
        print("  Retrieval-only: no model, no key, no downloads needed.")

    py = install_deps()

    hr("STEP 6 - Writing configuration")
    cfg = write_config(a, privacy_mode, model if privacy_mode == "local" else None, cloud, py)

    if not args.skip_models and privacy_mode == "local":
        hr("STEP 7 - Downloading AI stack (one-time)")
        download_llamacpp()
        if model:
            download_model(model)
    elif privacy_mode == "cloud" and args.auto_cloud:
        env = cfg["cloud"]["api_key_env"]
        os.environ.setdefault(env, "REPLACE_ME")
        print(f"  [--auto-cloud] placeholder {env}=REPLACE_ME set for this session - "
              "replace with your free key from " + (cloud or {}).get("hint", "the provider") )
    elif args.skip_models:
        print("\n  STEP 7 skipped (--skip-models). Run the downloads later by re-running "
              "install.py without the flag.")

    hr("STEP 8 - Readiness check (same checks the start scripts run)")
    subprocess.run([sys.executable, str(ROOT / "check_deps.py"), "--quiet"])

    hr("STEP 9 - Done. Your blueprint:")
    print(f"""
  Privacy mode      : {privacy_mode}
  AI engine         : {cfg['model_label'] or ('cloud: ' + cfg['cloud']['model'] if privacy_mode == 'cloud' else 'retrieval-only (documents only)')}
  Documents folder  : {ROOT / 'documents'}   <- drop club PDFs/text here
  AI web API        : http://localhost:{cfg['server']['port']}  (chat UI at / )

  Next steps:
    1. python3 setup_apps.py            # fetch Admidio (members) - needs PHP + MariaDB
    2. Drop documents into documents/   # the AI reads them dynamically
    3. {'export ' if platform.system() != 'Windows' else 'set '}{cfg['cloud']['api_key_env']}=<key>   # only if using cloud mode
    4. ./start-hub.sh  (or start-hub.bat on Windows)
       then open http://localhost:{cfg['server']['port']}
""")


if __name__ == "__main__":
    main()
