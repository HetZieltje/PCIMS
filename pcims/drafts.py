"""Atomic local persistence for unrecorded purchase lines."""

import base64
import binascii
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from PySide6.QtCore import QStandardPaths

from pcims.domain import ItemDetails, NewExpense
from pcims.proofs import MAX_TOTAL_PROOF_BYTES, NewProof

_DRAFT_VERSION = 2
_MAX_DRAFT_JSON_BYTES = MAX_TOTAL_PROOF_BYTES * 2


@dataclass(frozen=True, slots=True)
class DraftPurchase:
    staged_id: int
    expense: NewExpense
    proofs: tuple[NewProof, ...] = ()


class PurchaseDraftStore:
    def __init__(self, database_path: Path, root: Path | None = None) -> None:
        location = root or Path(
            QStandardPaths.writableLocation(
                QStandardPaths.StandardLocation.AppLocalDataLocation
            )
        )
        key = hashlib.sha256(str(database_path.resolve()).encode()).hexdigest()[:16]
        self.path = location / "drafts" / f"purchase-{key}.json"

    def load(self) -> tuple[DraftPurchase, ...]:
        if not self.path.exists():
            return ()
        if self.path.stat().st_size > _MAX_DRAFT_JSON_BYTES:
            raise ValueError("The saved purchase draft is too large.")
        payload: Any = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("version") != _DRAFT_VERSION:
            raise ValueError("The saved purchase draft uses an unsupported format.")
        lines = payload.get("lines")
        proof_files = payload.get("proof_files")
        if not isinstance(lines, list) or not isinstance(proof_files, dict):
            raise TypeError("The saved purchase draft is invalid.")
        decoded_proofs = self._decode_proof_files(proof_files)
        decoded = tuple(self._decode_line(line, decoded_proofs) for line in lines)
        identifiers = tuple(line.staged_id for line in decoded)
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Saved purchase draft line IDs must be unique.")
        return decoded

    def save(self, lines: tuple[DraftPurchase, ...]) -> None:
        if not lines:
            self.discard()
            return
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name != "nt":
            self.path.parent.chmod(0o700)
        temporary = self.path.with_suffix(".tmp")
        proof_files: dict[str, NewProof] = {}
        for line in lines:
            for proof in line.proofs:
                digest = hashlib.sha256(proof.content).hexdigest()
                existing = proof_files.get(digest)
                if existing is not None and existing.content != proof.content:
                    raise ValueError(
                        "Two draft proofs have a conflicting content hash."
                    )
                proof_files[digest] = proof
        if (
            sum(len(proof.content) for proof in proof_files.values())
            > MAX_TOTAL_PROOF_BYTES
        ):
            raise ValueError("Saved draft proofs exceed the application storage limit.")
        payload = json.dumps(
            {
                "version": _DRAFT_VERSION,
                "proof_files": {
                    digest: {
                        "media_type": proof.media_type,
                        "content": base64.b64encode(proof.content).decode("ascii"),
                    }
                    for digest, proof in proof_files.items()
                },
                "lines": [self._encode_line(line) for line in lines],
            },
            separators=(",", ":"),
        )
        if len(payload.encode("utf-8")) > _MAX_DRAFT_JSON_BYTES:
            raise ValueError("The saved purchase draft is too large.")
        try:
            with temporary.open("w", encoding="utf-8", newline="") as draft_file:
                if os.name != "nt":
                    temporary.chmod(0o600)
                draft_file.write(payload)
                draft_file.flush()
                os.fsync(draft_file.fileno())
            os.replace(temporary, self.path)
            if os.name != "nt":
                self.path.chmod(0o600)
                descriptor = os.open(
                    self.path.parent,
                    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
                )
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def discard(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass

    @staticmethod
    def _encode_line(line: DraftPurchase) -> dict[str, Any]:
        expense = line.expense
        details = expense.details
        return {
            "id": line.staged_id,
            "name": expense.name,
            "type": expense.item_type,
            "price_cents": expense.price_cents,
            "purchase_date": expense.purchase_date.isoformat(),
            "details": {
                "vendor": details.vendor,
                "serial_number": details.serial_number,
                "storage_location": details.storage_location,
                "condition": details.condition,
                "warranty_until": (
                    details.warranty_until.isoformat()
                    if details.warranty_until is not None
                    else None
                ),
                "notes": details.notes,
            },
            "proofs": [
                {
                    "file_name": proof.file_name,
                    "sha256": hashlib.sha256(proof.content).hexdigest(),
                }
                for proof in line.proofs
            ],
        }

    @staticmethod
    def _decode_proof_files(value: dict[Any, Any]) -> dict[str, tuple[str, bytes]]:
        decoded: dict[str, tuple[str, bytes]] = {}
        total_bytes = 0
        for digest, proof in value.items():
            if (
                not isinstance(digest, str)
                or len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)
                or not isinstance(proof, dict)
                or not isinstance(proof.get("media_type"), str)
                or not isinstance(proof.get("content"), str)
            ):
                raise TypeError("A saved draft proof file is invalid.")
            try:
                content = base64.b64decode(proof["content"], validate=True)
            except (binascii.Error, ValueError) as error:
                raise ValueError("A saved proof contains invalid data.") from error
            if hashlib.sha256(content).hexdigest() != digest:
                raise ValueError("A saved proof failed its content hash check.")
            total_bytes += len(content)
            if total_bytes > MAX_TOTAL_PROOF_BYTES:
                raise ValueError("Saved draft proofs exceed the storage limit.")
            decoded[digest] = (proof["media_type"], content)
        return decoded

    @staticmethod
    def _decode_line(
        value: Any, proof_files: dict[str, tuple[str, bytes]]
    ) -> DraftPurchase:
        if not isinstance(value, dict) or not isinstance(value.get("details"), dict):
            raise TypeError("A saved purchase draft line is invalid.")
        staged_id = value.get("id")
        if (
            isinstance(staged_id, bool)
            or not isinstance(staged_id, int)
            or staged_id < 1
        ):
            raise TypeError("A saved purchase draft line ID is invalid.")
        details = value["details"]
        price_cents = value.get("price_cents")
        if not isinstance(price_cents, int) or isinstance(price_cents, bool):
            raise TypeError("A saved purchase price is invalid.")
        cents = price_cents
        expense = NewExpense.create(
            value.get("name"),
            value.get("type"),
            f"{cents // 100}.{cents % 100:02d}",
            value.get("purchase_date"),
            ItemDetails(
                vendor=details.get("vendor"),
                serial_number=details.get("serial_number"),
                storage_location=details.get("storage_location"),
                condition=details.get("condition"),
                warranty_until=(
                    date.fromisoformat(details["warranty_until"])
                    if details.get("warranty_until") is not None
                    else None
                ),
                notes=details.get("notes"),
            ),
        )
        proofs_value = value.get("proofs", [])
        if not isinstance(proofs_value, list):
            raise TypeError("A saved proof collection is invalid.")
        proofs: list[NewProof] = []
        for proof in proofs_value:
            if (
                not isinstance(proof, dict)
                or not isinstance(proof.get("file_name"), str)
                or not isinstance(proof.get("sha256"), str)
            ):
                raise TypeError("A saved proof entry is invalid.")
            stored = proof_files.get(proof["sha256"])
            if stored is None:
                raise ValueError("A saved proof reference is missing its content.")
            media_type, content = stored
            proofs.append(NewProof(proof["file_name"], media_type, content))
        return DraftPurchase(staged_id, expense, tuple(proofs))
