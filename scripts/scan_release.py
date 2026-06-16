from __future__ import annotations

import argparse
import json
import re
import tarfile
from dataclasses import dataclass, field
from pathlib import Path


BLOCKED_NAME_PATTERNS = (
    re.compile(r"(^|/)\.env($|/)"),
    re.compile(r"\.(sqlite|sqlite3|db)(-[a-z]+)?$", re.IGNORECASE),
    re.compile(r"(^|/)__pycache__($|/)"),
    re.compile(r"\.py[co]$", re.IGNORECASE),
    re.compile(r"\.(pem|key)$", re.IGNORECASE),
    re.compile(r"(^|/)(id_rsa|id_ed25519|known_hosts)$"),
)

TEXT_NAME_PATTERN = re.compile(
    r"(\.(py|md|yaml|yml|toml|txt|sh|json|example|gitignore)$|(^|/)(README|SKILL|DEPLOYMENT)$)",
    re.IGNORECASE,
)

PRIVATE_KEY_PATTERN = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
UUID_PATTERN = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)
IPV4_PATTERN = re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b")
SENSITIVE_ASSIGNMENT_PATTERN = re.compile(
    r"^[ \t]*(?:[A-Z0-9_]*(?:PASSWORD|PASSWD|SECRET|API_KEY|AUTHCODE|ADMIN_KEY|BEARER)[A-Z0-9_]*)[ \t]*=[ \t]*([^\s#]+)",
    re.MULTILINE,
)

PLACEHOLDER_VALUES = {
    "",
    "0",
    "1",
    "bot",
    "change_me",
    "changeme",
    "dummy",
    "example",
    "false",
    "none",
    "null",
    "redacted",
    "replace_me",
    "true",
}


@dataclass
class ScanIssue:
    code: str
    path: str
    count: int = 1


@dataclass
class ScanReport:
    archive_files: int = 0
    text_files_scanned: int = 0
    installer_files_scanned: int = 0
    issues: list[ScanIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues

    def add(self, code: str, path: str, count: int = 1) -> None:
        if count > 0:
            self.issues.append(ScanIssue(code=code, path=path, count=count))

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "archive_files": self.archive_files,
            "text_files_scanned": self.text_files_scanned,
            "installer_files_scanned": self.installer_files_scanned,
            "issue_count": len(self.issues),
            "issues": [issue.__dict__ for issue in self.issues],
        }


def is_text_member(name: str) -> bool:
    return bool(TEXT_NAME_PATTERN.search(name))


def is_blocked_name(name: str) -> bool:
    normalized = name.replace("\\", "/")
    return any(pattern.search(normalized) for pattern in BLOCKED_NAME_PATTERNS)


def is_nonlocal_ipv4(value: str) -> bool:
    parts = [int(part) for part in value.split(".")]
    if parts[0] in {0, 10, 127}:
        return False
    if parts[0] == 169 and parts[1] == 254:
        return False
    if parts[0] == 172 and 16 <= parts[1] <= 31:
        return False
    if parts[0] == 192 and parts[1] == 168:
        return False
    if value == "255.255.255.255":
        return False
    return True


def looks_like_real_assignment(value: str) -> bool:
    stripped = value.strip().strip('"\'')
    if stripped.lower() in PLACEHOLDER_VALUES:
        return False
    if stripped.startswith("<") and stripped.endswith(">"):
        return False
    return len(stripped) >= 8


def scan_text(report: ScanReport, name: str, text: str) -> None:
    report.add("private_key_marker", name, len(PRIVATE_KEY_PATTERN.findall(text)))
    report.add("uuid_like_token", name, len(UUID_PATTERN.findall(text)))
    nonlocal_ips = [match.group(0) for match in IPV4_PATTERN.finditer(text) if is_nonlocal_ipv4(match.group(0))]
    report.add("nonlocal_ipv4_literal", name, len(nonlocal_ips))
    real_assignments = [
        match.group(1)
        for match in SENSITIVE_ASSIGNMENT_PATTERN.finditer(text)
        if looks_like_real_assignment(match.group(1))
    ]
    report.add("real_sensitive_assignment", name, len(real_assignments))


def scan_archive(archive_path: Path, report: ScanReport) -> None:
    with tarfile.open(archive_path, "r:gz") as archive:
        members = [member for member in archive.getmembers() if member.isfile()]
        report.archive_files += len(members)
        for member in members:
            if is_blocked_name(member.name):
                report.add("blocked_file_name", member.name)
            if not is_text_member(member.name):
                continue
            extracted = archive.extractfile(member)
            if extracted is None:
                continue
            data = extracted.read()
            text = data.decode("utf-8", errors="ignore")
            report.text_files_scanned += 1
            scan_text(report, member.name, text)


def scan_installer(installer_path: Path, report: ScanReport) -> None:
    text = installer_path.read_text(encoding="utf-8", errors="ignore")
    report.installer_files_scanned += 1
    if is_blocked_name(installer_path.name):
        report.add("blocked_file_name", str(installer_path))
    scan_text(report, str(installer_path), text)


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan release archive/installer for runtime files and obvious secrets")
    parser.add_argument("archive", type=Path, help="Release .tar.gz archive")
    parser.add_argument("--installer", type=Path, action="append", default=[], help="Optional installer script to scan")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    args = parser.parse_args()

    report = ScanReport()
    scan_archive(args.archive, report)
    for installer in args.installer:
        scan_installer(installer, report)

    data = report.to_dict()
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(f"scan ok: {str(report.ok).lower()}")
        print(f"archive files: {report.archive_files}")
        print(f"text files scanned: {report.text_files_scanned}")
        print(f"installer files scanned: {report.installer_files_scanned}")
        for issue in report.issues:
            print(f"issue: {issue.code} count={issue.count} path={issue.path}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
