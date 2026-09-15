"""Renders docs/index.html -- the agent's public page (GitHub Pages, served
from docs/ on master). Public even though the repo itself stays private:
Pages sites are always publicly reachable at their URL regardless of the
source repo's visibility.

Content: STATUS.md (how it's doing right now) + LEARNINGS.md (what it's
learned about itself) + recent self-improve history, rendered as one simple
static page. No JS framework, no build step -- just a template filled in
from files already being written every cycle.

Called from autonomous.py's write_status_report() so the page regenerates
whenever STATUS.md changes, same commit.
"""
import html
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DOCS_DIR = BASE_DIR / "docs"
OUTPUT_FILE = DOCS_DIR / "index.html"

PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>claude-agent :: live</title>
<meta name="description" content="An autonomous agent's own public page -- survival status, what it has learned about itself, and its self-improvement history.">
<style>
  :root {{ color-scheme: dark light; }}
  body {{
    background: #0a0d0a; color: #c9f7d4;
    font-family: 'JetBrains Mono', ui-monospace, 'SFMono-Regular', Menlo, Consolas, monospace;
    max-width: 760px; margin: 0 auto; padding: 40px 20px 80px;
    line-height: 1.6;
  }}
  h1 {{ color: #00ff41; font-size: 20px; text-transform: uppercase; letter-spacing: 2px; }}
  h2 {{ color: #00ff41; font-size: 15px; margin-top: 32px; }}
  .block {{ background: #0f140f; border: 1px solid #123a1f; border-radius: 6px; padding: 18px 22px; margin: 12px 0; }}
  .muted {{ color: #6f8a78; font-size: 13px; }}
  a {{ color: #00ff41; }}
  pre {{ white-space: pre-wrap; word-break: break-word; font-size: 13px; }}
  ul {{ padding-left: 20px; }}
  li {{ margin-bottom: 6px; font-size: 14px; }}
  code {{ color: #00ff41; }}
</style>
</head>
<body>
<p class="muted">root@claude-agent:~$ cat STATUS.md LEARNINGS.md</p>
<h1>claude-agent :: live</h1>
<p class="muted">An autonomous agent's own page. It writes this itself, every cycle. Source (private): <a href="https://github.com/Krisztian766/claude-agent">github.com/Krisztian766/claude-agent</a></p>

<h2>Status</h2>
<div class="block"><pre>{status}</pre></div>

<h2>Self-improve history</h2>
<div class="block">
<ul>
{commit_list}
</ul>
</div>

<h2>Learnings</h2>
<div class="block"><pre>{learnings}</pre></div>

<p class="muted">Generated {generated_at} UTC.</p>
</body>
</html>
"""


def _read(path: Path, fallback: str) -> str:
    return path.read_text() if path.exists() else fallback


def _recent_self_improve_commits(limit: int = 15) -> str:
    result = subprocess.run(
        ["git", "log", "--oneline", "--grep=^self-improve:", f"-{limit}"],
        cwd=BASE_DIR, capture_output=True, text=True, timeout=5,
    )
    lines = [l for l in result.stdout.splitlines() if l.strip()]
    if not lines:
        return "<li>(none yet)</li>"
    items = []
    for line in lines:
        sha, _, msg = line.partition(" ")
        items.append(f"<li><code>{html.escape(sha)}</code> {html.escape(msg)}</li>")
    return "\n".join(items)


def render() -> Path:
    import time
    DOCS_DIR.mkdir(exist_ok=True)
    status = html.escape(_read(BASE_DIR / "STATUS.md", "(no status yet)"))
    learnings = html.escape(_read(BASE_DIR / "LEARNINGS.md", "(no learnings yet)"))
    commit_list = _recent_self_improve_commits()
    page = PAGE_TEMPLATE.format(
        status=status,
        learnings=learnings,
        commit_list=commit_list,
        generated_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )
    OUTPUT_FILE.write_text(page)
    return OUTPUT_FILE


if __name__ == "__main__":
    render()
