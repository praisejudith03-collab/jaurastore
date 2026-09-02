#!/usr/bin/env python3
"""Dual-sync: keep the GitHub repository's data state in sync with the live
catalogue, alongside the Supabase mirror.

The Flask app treats Supabase (when configured) and the local overrides file
(data/catalog.json on a persistent disk) as the source of truth for products.
This module folds those live products back into the repository data state so
that:

  * `js/products-data.js`  (the static snapshot the storefront serves offline
                            and on static hosting) always reflects the current
                            merged catalogue, including admin-added/edited
                            products;
  * `data/catalog.json`    (the admin overrides) is refreshed in the repo so a
                            fresh checkout/deploy has the same product edits;
  * and, when a GitHub token is available, that state is committed and pushed
                            to the configured branch.

Only the repository data files are written. The shop is never blocked by a git
operation: any failure is returned/reported, never raised into the storefront.
Git commands are serialised with a cross-process + per-process lock so the
background auto-sync (after an admin write) and the manual "Sync to GitHub"
button cannot collide on `.git/index.lock`.

Usage:
  python3 repo_sync.py            # regenerate + commit + push (if configured)
  python3 repo_sync.py --check    # report what would change, without writing
  python3 repo_sync.py --no-push  # regenerate + commit locally, no push

Required env (optional - sync degrades gracefully without them):
  GITHUB_TOKEN        fine-grained or classic token with repo contents+push
  GITHUB_REPOSITORY   "owner/repo" (defaults to the git remote origin)
  GITHUB_BRANCH       branch to push (default: main)
  GITHUB_COMMITTER    "Name <email>" used for the commit (default: repo config)
"""
import os, subprocess, sys, datetime, json, threading, base64

ROOT = os.path.dirname(os.path.abspath(__file__))
# The repository's data-state is written under this root. It defaults to the
# checkout root, but can be pointed elsewhere for tests / tooling (env
# `REPO_SYNC_ROOT`) so a test run never mutates the real repo files.
REPO_ROOT = (os.environ.get("REPO_SYNC_ROOT") or "").strip() or ROOT

# Files that hold the repository's product-data state.
REPO_DATA_FILES = (
    "js/products-data.js",
    "data/seed.json",
    "data/wix_products.json",
    "data/catalog.json",
)

# ---- git-safe serialisation -------------------------------------------------
# A per-process lock stops two threads in the same worker colliding; a file
# lock (advisory POSIX lock) stops two gunicorn workers doing the same.
_giant = threading.Lock()
try:
    import fcntl
except ImportError:                      # pragma: no cover - non-POSIX fallback
    fcntl = None
_LOCK_PATH = os.path.join(REPO_ROOT, ".git-repo-sync.lock")


def _git_lock():
    """Context manager that serialises git operations across threads + workers."""
    class _Ctx:
        def __enter__(self):
            _giant.acquire()
            self._fh = None
            try:
                os.makedirs(os.path.dirname(_LOCK_PATH) or ".", exist_ok=True)
                self._fh = open(_LOCK_PATH, "a+")
                if fcntl is not None:
                    fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX)
            except OSError:
                pass
            # A stale .git/index.lock (from a crashed sync) would block every
            # commit; remove it only once we hold the exclusive lock.
            stale = os.path.join(REPO_ROOT, ".git", "index.lock")
            if os.path.exists(stale):
                try:
                    os.remove(stale)
                except OSError:
                    pass
            return self

        def __exit__(self, *exc):
            try:
                if self._fh is not None and fcntl is not None:
                    fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
                if self._fh is not None:
                    self._fh.close()
            finally:
                _giant.release()
            return False
    return _Ctx()


def _env(name, default=""):
    return (os.environ.get(name) or default).strip()


def _git(*args, timeout=120):
    """Run a git command in the repo root. Returns (ok, output)."""
    try:
        out = subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True,
            timeout=timeout, check=False,
        )
        return out.returncode == 0, (out.stdout or out.stderr).strip()
    except Exception as exc:                      # pragma: no cover
        return False, f"git error: {exc}"


def _configure_committer():
    """Ensure a git user identity exists for the commit (best effort)."""
    identity = _env("GITHUB_COMMITTER")
    if identity and " <" in identity:
        name = identity.split(" <")[0].strip()
        email = identity.split("<")[1].rstrip(">").strip()
        _git("config", "user.name", name)
        _git("config", "user.email", email)
    else:
        # leave whatever the repo already has; if none, set a safe default
        ok_name, name = _git("config", "user.name")
        ok_email, email = _git("config", "user.email")
        if not ok_name or not name:
            _git("config", "user.name", "Jaura Store Sync")
        if not ok_email or not email:
            _git("config", "user.email", "jaurastore@gmail.com")


def _remote_url():
    """The push URL of the git origin remote, or ''."""
    ok, out = _git("remote", "get-url", "origin")
    return out if ok else ""


def _resolve_repo():
    repo = _env("GITHUB_REPOSITORY")
    if repo:
        return repo
    url = _remote_url()
    if not url:
        return ""
    # https://github.com/owner/repo.git  or  git@github.com:owner/repo.git
    url = url.replace(".git", "").rstrip("/")
    if "github.com:" in url:
        url = url.split("github.com:")[-1]
    elif "github.com/" in url:
        url = url.split("github.com/")[-1]
    return url


def _write_repo_state(overrides):
    """Write the repository data files from the live merged catalogue.

    `overrides` is the current admin overrides dict (products/deleted/etc). We
    keep data/catalog.json pointing at the repo's overrides so a fresh checkout
    has the same product edits, and rebuild js/products-data.js so the static
    snapshot matches the merged catalogue.

    Returns a dict describing what was written.
    """
    import catalog as catalog_mod

    report = {}

    # 1) Refresh the repository copy of the admin overrides so deploy state
    #    matches the live (disk) catalogue. Preserve the overrides shape.
    if isinstance(overrides, dict) and overrides:
        repo_catalog = os.path.join(REPO_ROOT, "data", "catalog.json")
        os.makedirs(os.path.dirname(repo_catalog), exist_ok=True)
        with open(repo_catalog, "w", encoding="utf-8") as fh:
            json.dump(overrides, fh, ensure_ascii=False, separators=(",", ":"))
        report["data/catalog.json"] = len(overrides.get("products") or [])
        report["deleted"] = len(overrides.get("deleted") or [])

    # 2) Rebuild the storefront static snapshot from the merged catalogue so
    #    admin-added products appear even on static hosting / offline.
    merged = catalog_mod.merged(include_hidden=True)
    snapshot = "window.JA_SEED = " + json.dumps(
        merged, ensure_ascii=False, separators=(",", ":")) + ";\n"
    snapshot_path = os.path.join(REPO_ROOT, "js", "products-data.js")
    os.makedirs(os.path.dirname(snapshot_path), exist_ok=True)
    with open(snapshot_path, "w", encoding="utf-8") as fh:
        fh.write(snapshot)
    report["js/products-data.js"] = len(merged)

    return report


def regenerate(overrides=None, commit=True, push=True, message=""):
    """Regenerate the repo data state, commit, and optionally push.

    Returns (ok, report). Never raises: any git/token problem is returned as
    ok=False with a message, so the shop is never blocked by sync.
    """
    report = {}

    # Load the canonical overrides (from disk / Supabase) if not passed in.
    if overrides is None:
        import catalog as catalog_mod
        overrides = catalog_mod.overrides()

    try:
        written = _write_repo_state(overrides)
        report.update(written)
    except Exception as exc:                      # pragma: no cover
        return False, {"error": f"regenerate failed: {exc}", **report}

    if not commit:
        report["committed"] = False
        report["pushed"] = False
        return True, report

    with _git_lock():
        return _commit_and_push(commit=True, push=push, message=message,
                                report=report)


def _git_repo_tracked(rel):
    """True when a repo-relative file is under the git checkout at ROOT."""
    return os.path.exists(os.path.join(ROOT, rel))


def _github_contents_sha(api, repo, rel, branch, headers):
    """Look up the current blob SHA for a Contents API path.

    Distinguishes:
      (sha, remote_bytes, None)  — file exists (sha may still be "")
      ("", None, None)           — 404, the file is new
      (None, None, error)        — GET failed; caller must NOT PUT without sha
    """
    import urllib.request, urllib.error, urllib.parse
    url = (f"{api}/repos/{repo}/contents/{urllib.parse.quote(rel)}"
           f"?ref={urllib.parse.quote(branch)}")
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=20) as r:
            cur = json.loads(r.read().decode("utf-8", "replace"))
        sha = cur.get("sha") or ""
        remote = None
        raw = cur.get("content")
        if raw:
            try:
                remote = base64.b64decode("".join(str(raw).split()))
            except Exception:
                remote = None
        return sha, remote, None
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return "", None, None
        return None, None, f"GET sha {rel}: HTTP {exc.code}"
    except Exception as exc:
        return None, None, f"GET sha {rel}: {exc}"


def _push_via_api(repo, token, branch, message, report):
    """Push the repo data files to GitHub via the Contents API.

    This is the reliable path on hosts (Render / a serverless web service)
    where the runtime directory is not a git worktree, so `git push` has no
    checkout to act on. We fetch each file's current blob SHA, then PUT the
    new bytes. A PUT without sha is only allowed when GET returned 404
    (the file is new). A failed SHA lookup is never treated as "file is
    new" — that would 409/422 and previously got skipped as success.
    Returns (ok, report).
    """
    try:
        import urllib.request, urllib.error, urllib.parse
    except ImportError:                          # pragma: no cover
        return False, {"error": "urllib unavailable for API push", **report}

    api = "https://api.github.com"
    branch = branch or "main"
    headers = {
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "User-Agent": "jaurastore-sync",
    }

    changed = []
    errors = []
    for rel in REPO_DATA_FILES:
        path = os.path.join(REPO_ROOT, rel)
        if not os.path.exists(path):
            continue
        content = open(path, "rb").read()
        sha, remote, err = _github_contents_sha(api, repo, rel, branch, headers)
        if err:
            sha, remote, err = _github_contents_sha(api, repo, rel, branch, headers)
        if err:
            errors.append(err)
            report.setdefault("skipped", []).append(rel + " (no sha)")
            continue
        if remote is not None and remote == content:
            report.setdefault("skipped", []).append(rel)
            continue
        payload = {
            "message": message,
            "content": base64.b64encode(content).decode(),
            "branch": branch,
        }
        if sha:
            payload["sha"] = sha
        try:
            req = urllib.request.Request(
                f"{api}/repos/{repo}/contents/{urllib.parse.quote(rel)}",
                data=json.dumps(payload).encode("utf-8"), method="PUT",
                headers={**headers, "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                r.read()
            changed.append(rel)
        except urllib.error.HTTPError as exc:
            if exc.code in (409, 422):
                sha2, remote2, err2 = _github_contents_sha(
                    api, repo, rel, branch, headers)
                if remote2 is not None and remote2 == content:
                    report.setdefault("skipped", []).append(rel)
                    continue
                if (not sha) and sha2:
                    payload["sha"] = sha2
                    try:
                        req = urllib.request.Request(
                            f"{api}/repos/{repo}/contents/{urllib.parse.quote(rel)}",
                            data=json.dumps(payload).encode("utf-8"), method="PUT",
                            headers={**headers, "Content-Type": "application/json"})
                        with urllib.request.urlopen(req, timeout=30) as r:
                            r.read()
                        changed.append(rel)
                        continue
                    except Exception as exc2:
                        errors.append(f"API push {rel}: {exc2} (could not recover sha)")
                        continue
                errors.append(f"API push {rel}: {exc} (could not recover sha)")
                continue
            return False, {"error": f"API push {rel}: {exc}", "pushed": False, **report}
        except Exception as exc:
            return False, {"error": f"API push {rel}: {exc}", "pushed": False, **report}

    report["pushed"] = bool(changed)
    report["branch"] = branch
    report["repo"] = repo
    report["api"] = True
    report["files"] = changed
    if errors:
        report["errors"] = errors
    if not changed:
        report["note"] = ("SHA lookup failed: " + "; ".join(errors)
                          if errors else "No repository files changed via API.")
        if errors:
            return False, {"error": "; ".join(errors), "pushed": False, **report}
    return True, report


def _commit_and_push(commit, push, message, report):
    """Run commit + push under an exclusive git lock.

    Preferred path is a real git commit in the checkout (so the history is
    clean and local reviewers see it). When the checkout has no git (deployed
    web service), fall back to the GitHub Contents API.
    """
    repo = _resolve_repo()
    token = _env("GITHUB_TOKEN") or _env("GITHUB_API_TOKEN")
    branch = _env("GITHUB_BRANCH") or "main"
    subject = message or (
        "Sync catalogue state to the repository ("
        + datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC") + ")"
    )

    # On a deployed web service the runtime directory is usually NOT the git
    # checkout (Render copies the source out of the clone), so `git` has no
    # worktree to commit in. We detect that and push via the GitHub Contents
    # API instead so the repo still gets the updated data files.
    is_checkout = os.path.isdir(os.path.join(ROOT, ".git"))
    if push and token and repo and not is_checkout:
        the_report = dict(report)
        ok, res = _push_via_api(repo, token, branch, subject, the_report)
        if res.get("pushed"):
            res["committed"] = True
            res["commit"] = subject
            return True, res
        if ok and res.get("note") == "No repository files changed via API.":
            return True, res
        # Otherwise fall through to the local git path (it may still work).

    # ---- local git commit (sandbox / any host with a checkout) ----
    # stage only the repository data files (never secrets / lock files)
    added = []
    for rel in REPO_DATA_FILES:
        path = os.path.join(REPO_ROOT, rel)
        if os.path.exists(path):
            added.append(rel)
    if not added:
        report["committed"] = False
        report["pushed"] = False
        report["note"] = "No repository data files to sync."
        return True, report

    ok, out = _git("add", "--", *added)
    if not ok:
        return False, {"error": out, **report}

    # see if there is actually something staged
    ok, staged = _git("diff", "--cached", "--name-only")
    if not ok:
        return False, {"error": out, **report}
    if not staged.strip():
        report["committed"] = False
        report["pushed"] = False
        report["note"] = "Repository data already up to date (nothing changed)."
        return True, report

    _configure_committer()
    ok, out = _git("commit", "-m", subject)
    if not ok:
        return False, {"error": f"commit failed: {out}", **report}
    report["committed"] = True
    report["commit"] = subject

    if not push:
        report["pushed"] = False
        report["note"] = "Committed locally; push skipped (--no-push)."
        return True, report

    if not repo:
        report["pushed"] = False
        report["note"] = (
            "Committed locally; no GitHub repository could be resolved so the "
            "push was skipped. Set GITHUB_REPOSITORY or configure a git origin "
            "(see .env.example)."
        )
        return True, report

    if token:
        # Configure a push URL that carries the token (scoped to this repo only).
        push_url = f"https://x-access-token:{token}@github.com/{repo}.git"
        push_args = ["push", push_url, f"HEAD:{branch}"]
    else:
        # No token in the environment: fall back to the repo's configured
        # remote / credential helper, which covers the sandbox and any host
        # where `git push origin` already works (Render will set GITHUB_TOKEN).
        if not _remote_url():
            report["pushed"] = False
            report["note"] = (
                "Committed locally; no GITHUB_TOKEN and no git origin configured "
                "so the push was skipped (see .env.example)."
            )
            return True, report
        push_args = ["push", "origin", f"HEAD:{branch}"]

    ok, out = _git(*push_args)
    if not ok:
        # clear any credential from the URL in errors before returning it
        safe = out.replace(token, "***") if token and token in (out or "") else out
        return False, {"pushed": False, "error": f"push failed: {safe}", **report}
    report["pushed"] = True
    report["branch"] = branch
    report["repo"] = repo
    return True, report


def _check():
    """Dry-run: report what would change without writing or committing."""
    import catalog as catalog_mod
    merged = catalog_mod.merged(include_hidden=True)
    overrides = catalog_mod.overrides()
    out = {
        "productsInCatalogue": len(merged),
        "adminOverrides": len(overrides.get("products") or []),
        "deletedIds": len(overrides.get("deleted") or []),
        "gitRepo": _resolve_repo(),
        "gitCommitted": True,
        "pushConfigured": bool((_env("GITHUB_TOKEN") or _env("GITHUB_API_TOKEN"))
                               and _resolve_repo()),
        "branch": _env("GITHUB_BRANCH") or "main",
    }
    return True, out


def main():
    args = sys.argv[1:]
    if "--check" in args:
        ok, report = _check()
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0

    push = "--no-push" not in args
    ok, report = regenerate(commit=True, push=push)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
