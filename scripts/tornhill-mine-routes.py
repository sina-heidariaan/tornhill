#!/usr/bin/env python3
"""
tornhill-mine-routes — deterministically ENUMERATE HTTP routes/endpoints so the
`/audit` skill has a grounded, cite-able list to inspect (it does not have to
discover routes by reading every file with the LLM).

This is the audit analogue of tornhill-mine-graph.py: Tier-0 regex, no LSP/MCP/
network, ~0 LLM tokens. It ONLY enumerates a route's declaration — method, URL,
handler location, and any statically-attached auth/validation middleware. It does
NOT trace handler internals (that is a real taint engine and out of scope; the
audit skill traces internals, grounded by tornhill-scan-signals.py candidates).

Auth is resolved beyond the route line: a repo-wide global default (NestJS
`APP_GUARD`, DRF `DEFAULT_PERMISSION_CLASSES`, Express global `app.use(<auth>)`), a
class-level guard, or an auth decorator in a small window above/below the route —
with explicit `@Public`/`AllowAny` markers overriding back to open. Each route
records `has_auth` + `auth_source` (route|decorator|class|global|public|none), so a
line-local miss no longer inflates the "no auth" count. Still a candidate the audit
skill confirms.

Grounding contract ("cite or drop"): every route cites a real `path:line`. Routes
whose URL is not a static string literal (built from a variable / template) are
DROPPED and COUNTED in `dropped`, never guessed.

Frameworks v1: Express/Fastify/NestJS, Flask/FastAPI, Django, Spring, Rails,
Next.js (file-based). Pure stdlib. Read-only. No network. One git repo.

Usage:
    python tornhill-mine-routes.py <project-dir> [options]

Options:
    --frameworks <list>  limit to specific frameworks, or `auto` (default)
    --discovery git|walk how files are found (default git = `git ls-files`)
    --format json|md     default json (schema tornhill.routes/v1)
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

IGNORE_RE = re.compile(
    r"(^|/)(node_modules|dist|build|\.next|Pods|DerivedData|\.venv|vendor|"
    r"__pycache__|package-lock\.json|yarn\.lock|uv\.lock)(/|$)"
)
CODE_EXT = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".py", ".java", ".kt", ".rb"}

# identifiers that, when present on a route declaration, signal auth/validation
AUTH_HINTS = re.compile(
    r"\b(requireAuth|require_auth|authenticate|isAuthenticated|ensureLoggedIn|"
    r"verifyToken|verify_token|jwt|passport|authGuard|AuthGuard|checkAuth|"
    r"login_required|permission_classes|IsAuthenticated|authorize|Authorize|"
    r"before_action|authenticate_user|ensure_authenticated|withAuth|guard)\b"
)
VALIDATION_HINTS = re.compile(
    r"\b(validate|validator|schema|celebrate|zod|joi|yup|pydantic|serializers?|"
    r"body\(\)|check\(|param\(|sanitize)\b"
)

# auth-ish guard CLASS names (PascalCase, e.g. JwtAuthGuard) — `\b…\b` in AUTH_HINTS
# can't match a token glued inside a compound name, so match the class name directly
AUTH_GUARD_NAME = re.compile(
    r"(auth|jwt|passport|session|bearer|login|token|security)\w*guard", re.IGNORECASE)
# explicit "this route is intentionally open" markers — override any inherited auth
PUBLIC_HINTS = re.compile(
    r"@Public\b|@SkipAuth\b|@AllowAny\b|\bAllowAny\b|"
    r"permission_classes\s*=\s*\[\s*AllowAny|@permission_classes\(\s*\[\s*AllowAny"
)
# class-level guard (col-0 decorator) — covers every route in the class
GUARD_CLASS_LEVEL = re.compile(r"^@UseGuards\s*\(")
# for discovering custom composite auth decorators (Nest applyDecorators pattern):
# a decorator factory whose body wraps UseGuards(...AuthGuard...) — possibly via
# another custom decorator, so we resolve the reference chain to a fixpoint.
DEF_RE = re.compile(r"^\s*(?:export\s+)?(?:async\s+)?(?:function|const|let|var)\s+([A-Za-z_$][\w$]*)")
CALL_RE = re.compile(r"\b([A-Za-z_$][\w$]*)\s*\(")
USEGUARDS_ARGS = re.compile(r"UseGuards\s*\(([^)]*)\)")
DECORATOR_NAME_RE = re.compile(r"@([A-Za-z_$][\w$]*)")
# repo-wide global-auth signals (auth applied framework-wide, not per route line)
APP_GUARD_RE = re.compile(r"\bAPP_GUARD\b")                       # NestJS global guard
DRF_DEFAULT_PERM = re.compile(r"DEFAULT_PERMISSION_CLASSES")      # DRF global permission
IS_AUTHENTICATED = re.compile(r"\bIsAuthenticated\b")
EXPRESS_GLOBAL_USE = re.compile(r"\b(?:app|server)\.use\s*\(")    # Express global middleware

# how many lines above/below a route decorator to search for an auth/public decorator
AUTH_WINDOW = 3

HTTP_METHODS = ("get", "post", "put", "patch", "delete", "options", "head", "all")


def run_git(repo: Path, args: list[str]) -> str:
    try:
        out = subprocess.run(["git", "-C", str(repo), *args],
                             capture_output=True, text=True, check=True)
        return out.stdout
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        print(f"git failed: {' '.join(args)}\n{exc}", file=sys.stderr)
        return ""


def is_code(path: str) -> bool:
    if IGNORE_RE.search(path):
        return False
    return Path(path).suffix in CODE_EXT


def list_files(repo: Path, mode: str) -> list[str]:
    if mode == "git":
        paths = run_git(repo, ["ls-files"]).splitlines()
    else:
        paths = [p.relative_to(repo).as_posix()
                 for p in repo.rglob("*") if p.is_file()]
    return sorted(p for p in paths if is_code(p))


def read_lines(repo: Path, rel: str) -> list[str]:
    try:
        return (repo / rel).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []


def middleware_on(text: str) -> list[dict]:
    mw = []
    for hint, name in ((AUTH_HINTS, "auth"), (VALIDATION_HINTS, "validation")):
        m = hint.search(text)
        if m:
            mw.append({"kind": name, "name": m.group(1)})
    return mw


def detect_global_auth(contents: dict[str, list[str]]) -> dict:
    """Repo-wide scan for framework-level auth defaults applied off the route line.

    NestJS `APP_GUARD`, DRF `DEFAULT_PERMISSION_CLASSES` (with IsAuthenticated), and
    Express global `app.use(<auth>)`. Returns a flag + the reasons found. This is why
    a line-local scan under-reports auth on Nest/Django — the guard lives elsewhere.
    """
    def is_auth(text: str) -> bool:
        return bool(AUTH_HINTS.search(text) or AUTH_GUARD_NAME.search(text))

    reasons: set[str] = set()
    for lines in contents.values():
        text = "\n".join(lines)
        if APP_GUARD_RE.search(text) and is_auth(text):
            reasons.add("nest_app_guard")
        if DRF_DEFAULT_PERM.search(text) and IS_AUTHENTICATED.search(text):
            reasons.add("drf_default_permission")
        for line in lines:
            if EXPRESS_GLOBAL_USE.search(line) and is_auth(line):
                reasons.add("express_global_use")
                break
    return {"global_auth": bool(reasons), "reasons": sorted(reasons)}


def collect_auth_decorators(contents: dict[str, list[str]]) -> set[str]:
    """Find custom composite auth decorators (Nest `applyDecorators` pattern).

    A route may carry `@SomethingDecorator()` whose name reveals nothing, but which
    internally does `applyDecorators(... CommonAuthDecorator() ...)` →
    `UseGuards(JwtAuthGuard, ...)`. We scan every decorator factory, mark the ones
    that reach an auth guard directly, then propagate through the reference chain to a
    fixpoint so wrappers-of-wrappers are caught too. Returns the set of auth-decorator
    names to match on routes.
    """
    defs: dict[str, dict] = {}
    for rel, lines in contents.items():
        if Path(rel).suffix not in {".ts", ".tsx", ".js", ".jsx", ".mjs"}:
            continue
        for idx, line in enumerate(lines):
            m = DEF_RE.match(line)
            if not m:
                continue
            name = m.group(1)
            body: list[str] = []
            j = idx
            while j < len(lines) and j < idx + 40:
                if j > idx and DEF_RE.match(lines[j]):
                    break                       # stop at the next top-level definition
                body.append(lines[j])
                j += 1
            text = "\n".join(body)
            if "applyDecorators" not in text and "UseGuards" not in text \
                    and not name.endswith("Decorator"):
                continue
            direct = bool(AUTH_HINTS.search(name) or AUTH_GUARD_NAME.search(name))
            for g in USEGUARDS_ARGS.finditer(text):
                if AUTH_HINTS.search(g.group(1)) or AUTH_GUARD_NAME.search(g.group(1)):
                    direct = True
            defs[name] = {"refs": set(CALL_RE.findall(text)) - {name}, "direct": direct}

    auth = {n for n, d in defs.items() if d["direct"]}
    changed = True
    while changed:                              # propagate through the reference chain
        changed = False
        for n, d in defs.items():
            if n not in auth and (d["refs"] & auth):
                auth.add(n)
                changed = True
    return auth


def detect_file_auth(lines: list[str]) -> bool:
    """File-scoped auth that covers every route declared in the file: a class-level
    guard (Nest col-0 `@UseGuards(...Auth...)`) or Express router-level auth middleware
    (`router.use(authenticate)`)."""
    for line in lines:
        if GUARD_CLASS_LEVEL.match(line) and (AUTH_HINTS.search(line)
                                              or AUTH_GUARD_NAME.search(line)):
            return True
        if EXPRESS_GLOBAL_USE.search(line) and (AUTH_HINTS.search(line)
                                                or AUTH_GUARD_NAME.search(line)):
            return True
    return False


def call_span(lines: list[str], i: int, cap: int = 10) -> str:
    """Text of a (possibly multi-line) call starting at 1-based line i, read until its
    parentheses balance — so an Express `router.post('/x',\\n verifyToken,\\n h)` is
    seen whole, but the span never reaches into the next route's call."""
    depth = 0
    out: list[str] = []
    for k in range(i - 1, min(len(lines), i - 1 + cap)):
        out.append(lines[k])
        depth += lines[k].count("(") - lines[k].count(")")
        if k >= i - 1 and depth <= 0:
            break
    return "\n".join(out)


def express_auth(lines: list[str], i: int, global_auth: bool,
                 file_auth: bool) -> tuple[bool, str]:
    """Auth for a call-style (Express/Fastify) route: scan its own argument span for an
    auth middleware, then fall back to file-level / global auth."""
    span = call_span(lines, i)
    if AUTH_HINTS.search(span) or AUTH_GUARD_NAME.search(span):
        return True, "route"
    if file_auth:
        return True, "class"
    if global_auth:
        return True, "global"
    return False, "none"


def resolve_auth(lines: list[str], i: int, line: str,
                 global_auth: bool, file_auth: bool,
                 auth_decorators: frozenset = frozenset()) -> tuple[bool, str]:
    """Decide a route's auth, in priority order: explicit public marker, an auth hint
    on the route line, an auth decorator adjacent to it (built-in OR a discovered
    custom composite decorator), a class-level guard, then a repo-wide global default.

    The adjacent-decorator window is used ONLY when the route line is itself a
    decorator (Nest `@Get`, Flask `@app.route`) — where auth decorators legitimately
    sit above/below. Call-style routes (Express `app.get(...)`) use the line alone,
    so auth never bleeds between two routes on neighbouring lines.
    """
    decorator_route = line.lstrip().startswith("@")
    if decorator_route:
        # only the CONTIGUOUS decorator stack around this route — decorators bind to
        # the single route they wrap, so we must not bleed a sibling method's markers
        idx = i - 1
        stack = [line]
        j = idx - 1
        while j >= 0 and lines[j].lstrip().startswith("@"):
            stack.append(lines[j]); j -= 1
        j = idx + 1
        while j < len(lines) and lines[j].lstrip().startswith("@"):
            stack.append(lines[j]); j += 1
        window = "\n".join(stack)
    else:
        window = line

    if PUBLIC_HINTS.search(window):
        return False, "public"
    if AUTH_HINTS.search(line) or AUTH_GUARD_NAME.search(line):
        return True, "route"
    if decorator_route:
        if AUTH_HINTS.search(window) or AUTH_GUARD_NAME.search(window):
            return True, "decorator"
        # a discovered custom composite auth decorator (e.g. @CommonAuthDecorator())
        if auth_decorators and any(d in auth_decorators
                                   for d in DECORATOR_NAME_RE.findall(window)):
            return True, "custom_decorator"
    if file_auth:
        return True, "class"
    if global_auth:
        return True, "global"
    return False, "none"


def route_id(method: str, url: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", url).strip("_")
    return f"{method.upper()}__{slug}" if slug else f"{method.upper()}__root"


# --- per-framework detectors: yield (method, url|None, file, line, middleware) ---
EXPRESS_RE = re.compile(
    r"""\b(?:app|router|fastify|server|api)\.(get|post|put|patch|delete|options|head|all)\(\s*(['"`])([^'"`]*)\2""",
    re.IGNORECASE)
EXPRESS_DYN = re.compile(
    r"""\b(?:app|router|fastify|server|api)\.(get|post|put|patch|delete|all)\(\s*[^'"`\s]""")
NEST_CTRL = re.compile(r"""@Controller\(\s*['"]?([^'")]*)['"]?\s*\)""")
NEST_ROUTE = re.compile(r"""@(Get|Post|Put|Patch|Delete|All)\(\s*(?:['"]([^'"]*)['"])?\s*\)""")
FLASK_RE = re.compile(
    r"""@(?:app|router|bp|blueprint|api)\.(get|post|put|patch|delete|route)\(\s*['"]([^'"]+)['"]([^)]*)\)""",
    re.IGNORECASE)
FLASK_METHODS = re.compile(r"""methods\s*=\s*\[([^\]]*)\]""")
DJANGO_RE = re.compile(r"""\b(?:path|re_path|url)\(\s*(?:r?['"])([^'"]*)['"]""")
SPRING_MAP = re.compile(
    r"""@(Get|Post|Put|Patch|Delete|Request)Mapping\(\s*(?:value\s*=\s*)?['"]?([^'")\s,]*)['"]?""")
RAILS_RE = re.compile(r"""^\s*(get|post|put|patch|delete)\s+['"]([^'"]+)['"]""")
RAILS_RES = re.compile(r"""^\s*resources?\s+:(\w+)""")


def detect_js(rel: str, lines: list[str], routes: list, dropped: dict, ctx: tuple):
    """Express/Fastify + NestJS in one pass over a JS/TS file."""
    nest_base = ""
    for i, line in enumerate(lines, 1):
        cb = NEST_CTRL.search(line)
        if cb:
            nest_base = "/" + cb.group(1).strip("/") if cb.group(1) else ""
        for m in EXPRESS_RE.finditer(line):
            method, url = m.group(1).lower(), m.group(3)
            routes.append(_mk(method, url, rel, i, middleware_on(call_span(lines, i)),
                              *express_auth(lines, i, ctx[0], ctx[1])))
        if EXPRESS_DYN.search(line) and not EXPRESS_RE.search(line):
            dropped["dynamic_url"] += 1
        for m in NEST_ROUTE.finditer(line):
            method = m.group(1).lower()
            sub = ("/" + m.group(2).strip("/")) if m.group(2) else ""
            url = (nest_base + sub) or "/"
            routes.append(_mk(method, url, rel, i, middleware_on(line),
                              *resolve_auth(lines, i, line, *ctx)))


def detect_python(rel: str, lines: list[str], routes: list, dropped: dict, ctx: tuple):
    is_django_urls = rel.endswith("urls.py")
    for i, line in enumerate(lines, 1):
        for m in FLASK_RE.finditer(line):
            verb, url, tail = m.group(1).lower(), m.group(2), m.group(3)
            if verb == "route":
                mm = FLASK_METHODS.search(tail)
                methods = ([x.strip().strip("'\"").lower() for x in mm.group(1).split(",")]
                           if mm else ["get"])
            else:
                methods = [verb]
            auth = resolve_auth(lines, i, line, *ctx)
            for method in [x for x in methods if x]:
                routes.append(_mk(method, url, rel, i, middleware_on(line), *auth))
        if is_django_urls:
            for m in DJANGO_RE.finditer(line):
                url = "/" + m.group(1).lstrip("^").strip("/")
                routes.append(_mk("any", url, rel, i, middleware_on(line),
                                  *resolve_auth(lines, i, line, *ctx)))


def detect_java(rel: str, lines: list[str], routes: list, dropped: dict, ctx: tuple):
    for i, line in enumerate(lines, 1):
        for m in SPRING_MAP.finditer(line):
            kind, url = m.group(1), m.group(2) or "/"
            method = "any" if kind == "Request" else kind.lower()
            routes.append(_mk(method, "/" + url.strip("/"), rel, i, middleware_on(line),
                              *resolve_auth(lines, i, line, *ctx)))


def detect_ruby(rel: str, lines: list[str], routes: list, dropped: dict, ctx: tuple):
    if not rel.endswith("routes.rb"):
        return
    for i, line in enumerate(lines, 1):
        m = RAILS_RE.match(line)
        if m:
            routes.append(_mk(m.group(1).lower(), "/" + m.group(2).strip("/"), rel, i,
                              [], *resolve_auth(lines, i, line, *ctx)))
        r = RAILS_RES.match(line)
        if r:
            routes.append(_mk("any", f"/{r.group(1)}", rel, i, [],
                              *resolve_auth(lines, i, line, *ctx)))


def detect_nextjs(rel: str, lines: list[str], routes: list, dropped: dict, ctx: tuple):
    """File-based routing: pages/api/** and app/**/route.{ts,js}."""
    posix = rel
    if "/pages/api/" in posix or posix.startswith("pages/api/"):
        url = "/api/" + posix.split("pages/api/", 1)[1]
        url = re.sub(r"\.(t|j)sx?$", "", url)
        url = re.sub(r"/index$", "", url) or "/api"
        url = re.sub(r"\[([^\]]+)\]", r":\1", url)
        routes.append(_mk("any", url, rel, 1, [],
                          *resolve_auth(lines, 1, lines[0] if lines else "", *ctx)))
    elif re.search(r"(^|/)app/.*/route\.(t|j)sx?$", posix):
        seg = posix.split("app/", 1)[1]
        url = "/" + re.sub(r"/route\.(t|j)sx?$", "", seg)
        url = re.sub(r"\[([^\]]+)\]", r":\1", url) or "/"
        for i, line in enumerate(lines, 1):
            for m in re.finditer(r"export\s+(?:async\s+)?function\s+(GET|POST|PUT|PATCH|DELETE)", line):
                routes.append(_mk(m.group(1).lower(), url, rel, i, middleware_on(line),
                                  *resolve_auth(lines, i, line, *ctx)))


def _mk(method, url, rel, line, mw, auth=False, auth_source="none"):
    if not url.startswith("/"):
        url = "/" + url
    return {
        "id": route_id(method, url),
        "method": method.upper(),
        "url": url,
        "file": rel,
        "line": line,
        "cite": f"{rel}:{line}",
        "handler_cite": f"{rel}:{line}",
        "middleware": mw,
        "has_auth": bool(auth),
        "auth_source": auth_source,
    }


def build(repo: Path, args) -> dict:
    files = list_files(repo, args.discovery)
    contents = {rel: read_lines(repo, rel) for rel in files}
    # pass 1: repo-wide auth context — a global default (guard/permission applied off
    # the route line) and the set of custom composite auth decorators.
    gauth_info = detect_global_auth(contents)
    global_auth = gauth_info["global_auth"]
    auth_decorators = frozenset(collect_auth_decorators(contents))

    routes: list = []
    dropped: dict = defaultdict(int)
    frameworks = set()
    for rel, lines in contents.items():   # pass 2: enumerate routes with auth context
        suf = Path(rel).suffix
        ctx = (global_auth, detect_file_auth(lines), auth_decorators)
        before = len(routes)
        if suf in {".ts", ".tsx", ".js", ".jsx", ".mjs"}:
            detect_js(rel, lines, routes, dropped, ctx)
            detect_nextjs(rel, lines, routes, dropped, ctx)
        elif suf == ".py":
            detect_python(rel, lines, routes, dropped, ctx)
        elif suf in {".java", ".kt"}:
            detect_java(rel, lines, routes, dropped, ctx)
        elif suf == ".rb":
            detect_ruby(rel, lines, routes, dropped, ctx)
        if len(routes) > before:
            frameworks.add(_framework_of(rel, suf))

    # de-dup identical (method,url,cite)
    seen = set()
    uniq = []
    for r in routes:
        key = (r["method"], r["url"], r["cite"])
        if key not in seen:
            seen.add(key)
            uniq.append(r)
    uniq.sort(key=lambda r: (r["url"], r["method"]))

    by_source: dict = defaultdict(int)
    for r in uniq:
        by_source[r["auth_source"]] += 1

    sha = run_git(repo, ["rev-parse", "--short", "HEAD"]).strip()
    return {
        "schema": "tornhill.routes/v1",
        "project": str(repo),
        "derived_from": sha,
        "frameworks": sorted(f for f in frameworks if f),
        "global_auth": gauth_info["global_auth"],
        "global_auth_reasons": gauth_info["reasons"],
        "custom_auth_decorators": len(auth_decorators),
        "auth_detection": "approx",   # heuristic — a candidate to confirm, never a fact
        "auth_note": ("has_auth is inferred (route|decorator|custom_decorator|class|"
                      "global|public|none) and can miss unusual patterns (gateway/"
                      "proxy auth, router-mount middleware, other languages). Treat "
                      "'none' as 'confirm this', not 'proven open'."),
        "counts": {
            "routes": len(uniq),
            "no_auth": sum(1 for r in uniq if not r["has_auth"]),
            "by_auth_source": dict(sorted(by_source.items())),
        },
        "dropped": dict(dropped),
        "routes": uniq,
    }


def _framework_of(rel: str, suf: str) -> str:
    if "pages/api/" in rel or re.search(r"app/.*/route\.", rel):
        return "nextjs"
    if suf in {".ts", ".tsx", ".js", ".jsx", ".mjs"}:
        return "express/nest"
    if suf == ".py":
        return "flask/fastapi/django"
    if suf in {".java", ".kt"}:
        return "spring"
    if suf == ".rb":
        return "rails"
    return ""


def to_md(data: dict) -> str:
    ga = f"global-auth: {'yes (' + ','.join(data['global_auth_reasons']) + ')' if data.get('global_auth') else 'no'}"
    L = [f"# routes — {data['project']}",
         f"_frameworks: {', '.join(data['frameworks']) or 'none'} · "
         f"routes: {data['counts']['routes']} · no-auth: {data['counts']['no_auth']} · "
         f"{ga} · dropped: {data['dropped'] or 'none'}_\n",
         "| method | url | auth? | source | cite |",
         "|--------|-----|-------|--------|------|"]
    for r in data["routes"]:
        L.append(f"| {r['method']} | `{r['url']}` | "
                 f"{'yes' if r['has_auth'] else '**no**'} | {r['auth_source']} | `{r['cite']}` |")
    L.append("\n> enumeration only (Tier 0, approx). Handler internals are traced "
             "by the audit skill, seeded by tornhill-scan-signals.py.")
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="Enumerate HTTP routes/endpoints.")
    ap.add_argument("project")
    ap.add_argument("--frameworks", default="auto")
    ap.add_argument("--discovery", choices=["git", "walk"], default="git")
    ap.add_argument("--format", choices=["json", "md"], default="json")
    args = ap.parse_args()

    repo = Path(args.project).resolve()
    if not (repo / ".git").exists():
        print(f"not a git repo: {repo}", file=sys.stderr)
        return 1
    data = build(repo, args)
    print(to_md(data) if args.format == "md" else json.dumps(data, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
