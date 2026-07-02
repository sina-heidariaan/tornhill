#!/usr/bin/env python3
"""
tornhill-to-html — render an tornhill markdown blueprint into an interactive HTML twin.

Adds the three things that make the view analyzable rather than a static picture:
  - ZOOM/PAN on every Mermaid diagram (svg-pan-zoom).
  - CLICK-NODE-TO-CODE — Mermaid `click <id> href "<repo-relative-path>"` links
    are rewritten to open in your editor / file / GitHub.
  - FINDINGS OVERLAY toggle — the critique section can be shown/hidden, and each
    item is colored by its `[severity]` tag (blocker/high/medium/low).

Usage:
    python tornhill-to-html.py [files...] [options]
      (no files = render all *.md under --out-dir)

Options:
    --out-dir <dir>                 where blueprints live (default: ./tornhill)
    --code-link vscode|file|github  how node clicks open code (default vscode)
    --repo-root <dir>               repo root for resolving links (default: CWD)
    --github-base <url>             e.g. https://github.com/org/repo/blob/main
    --assets cdn|inline|local       where viewer JS comes from (default cdn)
    --vendor-dir <dir>              vendored JS for inline/local (default: ./vendor)

Requires: markdown, pyyaml   (pip install markdown pyyaml)

Security: no network at generation time. The viewer JS (mermaid, svg-pan-zoom)
that renders diagrams in the browser comes from one of three sources:
    cdn     pinned + SRI-verified CDN <script> tags (default; fetches at view time)
    inline  JS embedded in the .html — self-contained, ZERO third-party fetch
    local   relative <script src="vendor/...">  — zero third-party fetch, multi-file
For inline/local, run tornhill-vendor-assets.py once to populate ./vendor.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

try:
    import markdown  # type: ignore
    import yaml  # type: ignore
except ImportError:
    print("Install deps:  pip install markdown pyyaml", file=sys.stderr)
    sys.exit(1)

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

# Pinned exact versions + SRI hashes. A version range would let the CDN silently
# change the bytes you load; the integrity hash makes a swapped payload fail loudly.
# Kept in sync with tornhill-vendor-assets.py via the shared ASSETS table below.
MERMAID_CDN = "https://cdn.jsdelivr.net/npm/mermaid@10.9.3/dist/mermaid.min.js"
PANZOOM_CDN = "https://cdn.jsdelivr.net/npm/svg-pan-zoom@3.6.1/dist/svg-pan-zoom.min.js"
MERMAID_SRI = "sha384-R63zfMfSwJF4xCR11wXii+QUsbiBIdiDzDbtxia72oGWfkT7WHJfmD/I/eeHPJyT"
PANZOOM_SRI = "sha384-yc/c2Lk1s2V2ir1rxvjo8YyVD9PlOlYTqpNr3Wm1WIuAA30GlDYNx6U5104OiavY"
# (name, cdn url, sri) — order is load order: mermaid before svg-pan-zoom.
ASSETS = (
    ("mermaid.min.js", MERMAID_CDN, MERMAID_SRI),
    ("svg-pan-zoom.min.js", PANZOOM_CDN, PANZOOM_SRI),
)

MERMAID_BLOCK = re.compile(r"```mermaid\s*\n(.*?)\n```", re.DOTALL)
HREF_RE = re.compile(r'(href\s+")([^"]+)(")')
SKIP_SCHEMES = ("http://", "https://", "vscode://", "mailto:", "#")

CSS = """\
  :root { --blocker:#cf222e; --high:#bc4c00; --medium:#9a6700; --low:#0969da; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
    max-width: 1040px; margin: 24px auto; padding: 0 18px; line-height: 1.55; color:#1f2328; background:#fff; }
  h1 { font-size:26px; border-bottom:1px solid #d0d7de; padding-bottom:8px; }
  h2 { font-size:20px; margin-top:30px; border-bottom:1px solid #eaecef; padding-bottom:4px; }
  h3 { font-size:16px; margin-top:20px; }
  code { background:#f6f8fa; padding:2px 5px; border-radius:5px; font-size:85%; }
  pre code { background:none; padding:0; }
  table { border-collapse:collapse; margin:14px 0; } th,td { border:1px solid #d0d7de; padding:6px 11px; }
  th { background:#f6f8fa; }
  .provenance { background:#f6f8fa; border:1px solid #d0d7de; border-radius:8px; padding:8px 14px; font-size:13px; color:#57606a; margin-bottom:18px; }
  .mermaid { border:1px solid #eaecef; border-radius:8px; margin:16px 0; background:#fbfcfd; overflow:hidden; }
  .mermaid svg { width:100%; height:520px; }
  .controls { position:sticky; top:0; z-index:50; background:#fff; padding:8px 0; border-bottom:1px solid #eaecef; display:flex; gap:8px; align-items:center; }
  .btn { cursor:pointer; border:1px solid #d0d7de; background:#f6f8fa; border-radius:6px; padding:5px 12px; font-size:13px; }
  .btn:hover { background:#eaeef2; }
  .hint { font-size:12px; color:#8b949e; }
  #findings.hidden { display:none; }
  .sev { border-left:4px solid #d0d7de; padding-left:10px; margin:8px 0; }
  .sev-blocker { border-color:var(--blocker); } .sev-high { border-color:var(--high); }
  .sev-medium { border-color:var(--medium); } .sev-low { border-color:var(--low); }
  .badge { font-size:11px; font-weight:600; text-transform:uppercase; padding:1px 6px; border-radius:10px; color:#fff; margin-right:6px; }
  .badge-blocker { background:var(--blocker); } .badge-high { background:var(--high); }
  .badge-medium { background:var(--medium); } .badge-low { background:var(--low); }
"""

JS = """\
  mermaid.initialize({ startOnLoad: false, securityLevel: 'loose', theme: 'neutral' });
  async function render() {
    await mermaid.run({ querySelector: '.mermaid' });
    document.querySelectorAll('.mermaid svg').forEach(svg => {
      svg.removeAttribute('height'); svg.style.height = '520px';
      try { svgPanZoom(svg, { controlIconsEnabled: true, fit: true, center: true, minZoom: 0.3 }); }
      catch (e) {}
    });
  }
  function wrapFindings() {
    const h = [...document.querySelectorAll('h2')].find(x => /findings/i.test(x.textContent));
    if (!h) return null;
    const box = document.createElement('div'); box.id = 'findings';
    const kids = []; let n = h.nextElementSibling;
    while (n && n.tagName !== 'H2') { kids.push(n); n = n.nextElementSibling; }
    h.after(box); kids.forEach(k => box.appendChild(k));
    return box;
  }
  function colorSeverities(box) {
    if (!box) return;
    box.querySelectorAll('li, p').forEach(el => {
      const m = el.textContent.match(/\\[(blocker|high|medium|low)\\]/i);
      if (!m) return;
      const sev = m[1].toLowerCase();
      el.classList.add('sev', 'sev-' + sev);
      const badge = document.createElement('span');
      badge.className = 'badge badge-' + sev; badge.textContent = sev;
      el.insertBefore(badge, el.firstChild);
    });
  }
  window.addEventListener('DOMContentLoaded', () => {
    const box = wrapFindings(); colorSeverities(box);
    const t = document.getElementById('toggle-findings');
    if (t && box) t.addEventListener('click', () => box.classList.toggle('hidden'));
    render();
  });
"""


def build_lib_scripts(assets, vendor_dir, out_path):
    """Return the <script> tags that load mermaid + svg-pan-zoom for `assets` mode.

    cdn    -> pinned, SRI-verified, crossorigin CDN tags (fetches at view time).
    inline -> the vendored bytes embedded directly (self-contained, zero fetch).
    local  -> relative src= to the vendor dir alongside the output (zero fetch).
    """
    if assets == "cdn":
        return "\n".join(
            f'<script src="{url}" integrity="{sri}" crossorigin="anonymous"></script>'
            for name, url, sri in ASSETS
        )

    missing = [name for name, _u, _s in ASSETS if not (vendor_dir / name).is_file()]
    if missing:
        raise SystemExit(
            f"--assets {assets} needs vendored libs but {vendor_dir} is missing: "
            f"{', '.join(missing)}\nRun:  python tornhill-vendor-assets.py "
            f"--vendor-dir {vendor_dir}"
        )

    if assets == "inline":
        tags = []
        for name, _u, _s in ASSETS:
            js = (vendor_dir / name).read_text(encoding="utf-8")
            # Guard against an accidental </script> in the payload closing our tag.
            js = js.replace("</script>", "<\\/script>")
            tags.append(f"<script>{js}</script>")
        return "\n".join(tags)

    # local: reference vendor files relative to the output html
    rel = Path(os.path.relpath(vendor_dir.resolve(), out_path.parent.resolve()))
    return "\n".join(
        f'<script src="{(rel / name).as_posix()}"></script>' for name, _u, _s in ASSETS
    )


LINE_SUFFIX = re.compile(r":(\d+)(?::\d+)?$")


def to_code_link(rel, md_path, mode, repo_root, gh_base):
    if rel.startswith(SKIP_SCHEMES):
        return None
    # L4 deep-dive nodes cite `path:line` (or `path:line:col`); split off the
    # anchor so the file still resolves, then re-attach it per link mode.
    line = None
    m = LINE_SUFFIX.search(rel)
    if m:
        rel, line = rel[: m.start()], m.group(1)
    target = (md_path.parent / rel).resolve()
    if not target.is_file():  # grounding: only link to files that exist
        return None
    if mode == "vscode":
        link = f"vscode://file/{target.as_posix()}"
        return f"{link}:{line}" if line else link
    if mode == "github" and gh_base:
        try:
            link = f"{gh_base.rstrip('/')}/{target.relative_to(repo_root).as_posix()}"
        except ValueError:
            return None
        return f"{link}#L{line}" if line else link
    return target.as_uri()


def rewrite_click_links(block, md_path, mode, repo_root, gh_base):
    def sub(m):
        link = to_code_link(m.group(2), md_path, mode, repo_root, gh_base)
        return f"{m.group(1)}{link}{m.group(3)}" if link else m.group(0)
    return HREF_RE.sub(sub, block)


def split_frontmatter(text):
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            try:
                return yaml.safe_load(text[3:end].strip()) or {}, text[end + 4:].lstrip("\n")
            except yaml.YAMLError:
                pass
    return {}, text


def render_one(md_path, mode, repo_root, gh_base, assets, vendor_dir):
    meta, body = split_frontmatter(md_path.read_text(encoding="utf-8"))
    out = md_path.with_suffix(".html")
    lib_scripts = build_lib_scripts(assets, vendor_dir, out)
    blocks = []

    def stash(m):
        blocks.append(rewrite_click_links(m.group(1), md_path, mode, repo_root, gh_base))
        return f"\x00MERMAID{len(blocks) - 1}\x00"

    body = MERMAID_BLOCK.sub(stash, body)
    html_body = markdown.markdown(body, extensions=["tables", "fenced_code", "toc"])
    for i, code in enumerate(blocks):
        html_body = html_body.replace(f"\x00MERMAID{i}\x00", f'<pre class="mermaid">{code}</pre>')

    title = meta.get("title", md_path.stem)
    stamp = meta.get("derived-from", "")
    provenance = (
        f'<div class="provenance">tornhill blueprint · '
        f'{("derived-from " + stamp) if stamp else "snapshot"} · '
        f'diagrams zoom/pan · nodes open code · toggle findings above.</div>'
    )
    controls = (
        '<div class="controls">'
        '<button class="btn" id="toggle-findings">Toggle findings</button>'
        '<span class="hint">scroll to zoom · drag to pan · click a node to open its file</span>'
        "</div>"
    )
    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} — tornhill</title><style>{CSS}</style></head><body>
{controls}
{provenance}
{html_body}
{lib_scripts}
<script>{JS}</script>
</body></html>
"""
    out.write_text(html, encoding="utf-8")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Render tornhill markdown to interactive HTML.")
    ap.add_argument("files", nargs="*", help="md files (default: all *.md under --out-dir)")
    ap.add_argument("--out-dir", default="tornhill", help="blueprint dir (default: ./tornhill)")
    ap.add_argument("--code-link", choices=["vscode", "file", "github"], default="vscode")
    ap.add_argument("--repo-root", default=".", help="repo root for github links (default: CWD)")
    ap.add_argument("--github-base", default=None)
    ap.add_argument("--assets", choices=["cdn", "inline", "local"], default="cdn",
                    help="viewer JS source (default cdn; inline/local need ./vendor)")
    ap.add_argument("--vendor-dir", default="vendor",
                    help="vendored JS dir for inline/local (default: ./vendor)")
    args = ap.parse_args()

    repo_root = Path(args.repo_root).resolve()
    vendor_dir = Path(args.vendor_dir)
    if args.files:
        targets = [Path(a).resolve() for a in args.files]
    else:
        out_dir = Path(args.out_dir)
        if not out_dir.exists():
            print(f"no blueprint dir: {out_dir}", file=sys.stderr)
            return 1
        targets = sorted(out_dir.rglob("*.md"))
    if not targets:
        print("nothing to render", file=sys.stderr)
        return 1
    for md in targets:
        out = render_one(md, args.code_link, repo_root, args.github_base,
                         args.assets, vendor_dir)
        print(f"rendered {out}  (links: {args.code_link}, assets: {args.assets})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
