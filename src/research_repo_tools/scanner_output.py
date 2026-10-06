"""Allowlisted terminal findings; scanner logs and match text never enter here."""

import json
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path


def _text(value: object) -> str:
    text = str(value) if isinstance(value, str | int) and not isinstance(value, bool) else "unknown"
    text = "".join(char if unicodedata.category(char) not in {"Cc", "Cf", "Cs"} else f"\\u{ord(char):04x}" for char in text)
    return json.dumps(text, ensure_ascii=False)


def _print(line: str) -> None:
    # Reporting a published file must not fail under a narrow terminal encoding.
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    print(line.encode(encoding, errors="backslashreplace").decode(encoding))


@dataclass
class FindingOutput:
    scanner: str
    label: str
    root: Path
    snapshot: Path | None = None
    json_reported: bool = False

    def _location(self, path: object, line: object = None) -> str:
        if isinstance(path, str):
            candidate = Path(path)
            for root in (self.snapshot, self.root):
                if root is not None and candidate.is_relative_to(root):
                    path = candidate.relative_to(root).as_posix()
                    break
        suffix = f":{line}" if type(line) is int and line > 0 else ""
        return _text(path) + suffix

    def __call__(self, value, fmt: str, destination: Path, code: int, findings: bool) -> None:
        prefix = f"{self.scanner} ({self.label})"
        rows = []
        if fmt == "json":
            if self.scanner == "OSV":
                for source in value["results"]:
                    for package in source["packages"]:
                        for vulnerability in package.get("vulnerabilities", []):
                            info = package["package"]
                            rows.append(
                                f"{self._location(source['source']['path'])}: {_text(info.get('name'))}@{_text(info.get('version'))}: {_text(vulnerability['id'])}"
                            )
            elif self.scanner == "Gitleaks":
                rows = [f"{self._location(item['File'], item.get('StartLine'))}: {_text(item['RuleID'])}: potential secret" for item in value]
            else:
                rows = [f"{self._location(item['path'], item['start']['line'])}: {_text(item['check_id'])}: rule matched" for item in value["results"]]
            self.json_reported = True
        elif not self.json_reported:
            # Fallback uses only rule/location identifiers, never SARIF messages,
            # snippets, fingerprints, commit messages or rule descriptions.
            for run in value["runs"]:
                for item in run["results"]:
                    locations = item.get("locations", [])
                    if not isinstance(locations, list):
                        locations = []
                    for location in locations:
                        physical = location.get("physicalLocation", {}) if isinstance(location, dict) else {}
                        if not isinstance(physical, dict):
                            continue
                        artifact, region = physical.get("artifactLocation", {}), physical.get("region", {})
                        if isinstance(artifact, dict) and isinstance(region, dict):
                            rows.append(f"{self._location(artifact.get('uri'), region.get('startLine'))}: {_text(item.get('ruleId'))}: finding")
        for row in rows:
            _print(f"{prefix}: {row}")
        if fmt == "json" or not self.json_reported:
            summary = f"{len(rows)} finding(s)" if rows or not findings else "findings present (details in report)"
            _print(f"{prefix}: {summary}; exit {code}; report {self._location(str(destination))}")
        else:
            _print(f"{prefix}: additional SARIF report {self._location(str(destination))}; {'findings present; ' if findings else ''}exit {code}")
