"""Publish only generated benchmark charts and the managed README block."""
import argparse
import base64
import json
import os
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request

from .charts import ASSET_PATH, arguments, asset_names, from_arguments, is_managed_chart, render, replace_readme


class ApiError(RuntimeError):
    def __init__(self, status, method, path):
        super().__init__(f"GitHub API {method} {path} failed with HTTP {status}")
        self.status = status


class GitHub:
    def __init__(self, token):
        if not token:
            raise ValueError("GITHUB_TOKEN is required in the publishing job")
        self.token = token

    def request(self, method, path, payload=None):
        data = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request("https://api.github.com/" + path, data=data, method=method,
            headers={"Authorization": "Bearer " + self.token, "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2026-03-10", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            # Never print request headers/tokens or untrusted response bodies.
            raise ApiError(error.code, method, path) from None


def trusted_environment(env, repository):
    return (env.get("GITHUB_ACTIONS") == "true" and env.get("GITHUB_REF") == "refs/heads/main"
            and env.get("GITHUB_EVENT_NAME") in ("push", "workflow_dispatch")
            and env.get("GITHUB_REPOSITORY") == repository)


def newer_than(current, incoming):
    if current is None or current["run"]["repository"] != incoming["run"]["repository"]:
        return True
    before, after = current["run"], incoming["run"]
    return (after["number"], after["attempt"]) > (before["number"], before["attempt"])


def read_file(api, repository, name, revision, optional=False):
    path = f"repos/{repository}/contents/{name}?ref={revision}"
    try:
        result = api.request("GET", path)
    except ApiError as error:
        if error.status == 404 and optional:
            return None
        raise
    if result.get("encoding") != "base64" or result.get("type") != "file":
        raise ValueError(f"Expected a small UTF-8 repository file: {name}")
    return base64.b64decode(result["content"]).decode("utf-8")


def obsolete_charts(api, repository, revision, current):
    """Delete only existing generated SVGs, leaving unrelated directory files alone."""
    try:
        entries = api.request("GET", f"repos/{repository}/contents/{ASSET_PATH}?ref={revision}")
    except ApiError as error:
        if error.status == 404:
            return []
        raise
    if not isinstance(entries, list):
        raise ValueError("Expected the generated chart directory")
    return sorted(f"{ASSET_PATH}/{entry['name']}" for entry in entries
                  if entry.get("type") == "file" and is_managed_chart(entry["name"])
                  and f"{ASSET_PATH}/{entry['name']}" not in current)


def publish(api, value, output, attempts=3):
    """Retry only fast-forward races; merge the README against the latest tree."""
    repository = value["run"]["repository"]
    assets = {f"{ASSET_PATH}/{name}": (Path(output) / name).read_text() for name in asset_names(value)}
    for _ in range(attempts):
        head = api.request("GET", f"repos/{repository}/git/ref/heads/main")["object"]["sha"]
        previous_text = read_file(api, repository, f"{ASSET_PATH}/manifest.json", head, optional=True)
        previous = json.loads(previous_text) if previous_text else None
        if not newer_than(previous, value):
            return {"status": "skipped", "reason": "This run/attempt is already published or superseded", "head": head}
        readme = read_file(api, repository, "README.md", head)
        files = {**assets, "README.md": replace_readme(readme, value)}
        removed = obsolete_charts(api, repository, head, files)
        base = api.request("GET", f"repos/{repository}/git/commits/{head}")["tree"]["sha"]
        tree = api.request("POST", f"repos/{repository}/git/trees", {
            "base_tree": base,
            "tree": [{"path": name, "mode": "100644", "type": "blob", "content": content} for name, content in files.items()]
                    + [{"path": name, "mode": "100644", "type": "blob", "sha": None} for name in removed],
        })["sha"]
        run = value["run"]
        commit = api.request("POST", f"repos/{repository}/git/commits", {
            "message": f"docs: refresh benchmark charts for run {run['number']}.{run['attempt']}\n\n{run['url']}",
            "tree": tree, "parents": [head],
            "author": {"name": "github-actions[bot]", "email": "41898282+github-actions[bot]@users.noreply.github.com"},
        })["sha"]
        try:
            api.request("PATCH", f"repos/{repository}/git/refs/heads/main", {"sha": commit, "force": False})
            return {"status": "published", "commit": commit, "url": f"https://github.com/{repository}/commit/{commit}"}
        except ApiError as error:
            if error.status not in (409, 422):
                raise
            # A human edit or a newer publication may have landed. Re-read both
            # README and provenance; never force-push or rebase over their work.
    raise RuntimeError("README publication could not fast-forward main after bounded retries")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    arguments(parser)
    args = parser.parse_args()
    if not trusted_environment(os.environ, args.repository):
        raise RuntimeError("Only trusted main push/workflow_dispatch jobs may publish README charts")
    value = from_arguments(args)
    render(value, args.output)
    result = publish(GitHub(os.environ.get("GITHUB_TOKEN")), value, args.output)
    print(json.dumps(result))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with Path(summary).open("a") as file:
            file.write(f"\nREADME charts: **{result['status']}** for run #{value['run']['number']}.{value['run']['attempt']}.\n")
            if result.get("url"):
                file.write(f"[Generated chart commit]({result['url']})\n")


if __name__ == "__main__":
    main()
