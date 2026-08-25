"""Atomic local persistence for unrecorded purchase lines."""

import base64
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from PySide6.QtCore import QStandardPaths

from pcims.domain import ItemDetails, NewExpense
from pcims.proofs import NewProof


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
        payload: Any = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("version") != 1:
            raise ValueError("The saved purchase draft uses an unsupported format.")
        lines = payload.get("lines")
        if not isinstance(lines, list):
            raise TypeError("The saved purchase draft is invalid.")
        return tuple(self._decode_line(line) for line in lines)

    def save(self, lines: tuple[DraftPurchase, ...]) -> None:
        if not lines:
            self.discard()
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {"version": 1, "lines": [self._encode_line(line) for line in lines]},
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        os.replace(temporary, self.path)

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
                    "media_type": proof.media_type,
                    "content": base64.b64encode(proof.content).decode("ascii"),
                }
                for proof in line.proofs
            ],
        }

    @staticmethod
    def _decode_line(value: Any) -> DraftPurchase:
        if not isinstance(value, dict) or not isinstance(value.get("details"), dict):
            raise TypeError("A saved purchase draft line is invalid.")
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
        proofs = tuple(
            NewProof(
                proof["file_name"],
                proof["media_type"],
                base64.b64decode(proof["content"], validate=True),
            )
            for proof in proofs_value
        )
        return DraftPurchase(int(value["id"]), expense, proofs)
