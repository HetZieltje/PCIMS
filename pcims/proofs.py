"""Validated proof-of-purchase files stored with the inventory database."""

from dataclasses import dataclass
from pathlib import Path

from pcims.domain import normalized_text

MAX_PROOF_BYTES = 20 * 1024 * 1024
MAX_PROOFS_PER_ITEM = 20
PROOF_FILE_FILTER = "Proofs (*.pdf *.png *.jpg *.jpeg *.webp)"


def _detected_media_type(content: bytes) -> str | None:
    if content.startswith(b"%PDF-"):
        return "application/pdf"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    return None


@dataclass(frozen=True, slots=True)
class NewProof:
    file_name: str
    media_type: str
    content: bytes

    def __post_init__(self) -> None:
        name = normalized_text(self.file_name, "Proof file name")
        if Path(name).name != name or name in {".", ".."}:
            raise ValueError("Proof file name must not contain a path.")
        if not isinstance(self.content, bytes):
            raise TypeError("Proof content must be bytes.")
        if not self.content:
            raise ValueError("Proof file cannot be empty.")
        if len(self.content) > MAX_PROOF_BYTES:
            raise ValueError("Proof file cannot exceed 20 MiB.")
        detected = _detected_media_type(self.content)
        if detected is None:
            raise ValueError("Proof must be a PDF, PNG, JPEG, or WebP file.")
        if self.media_type != detected:
            raise ValueError("Proof file type does not match its content.")
        object.__setattr__(self, "file_name", name)

    @classmethod
    def from_path(cls, path: str | Path) -> "NewProof":
        source = Path(path).expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(f"Proof file does not exist: {source}")
        if source.stat().st_size > MAX_PROOF_BYTES:
            raise ValueError("Proof file cannot exceed 20 MiB.")
        with source.open("rb") as proof_file:
            content = proof_file.read(MAX_PROOF_BYTES + 1)
        if len(content) > MAX_PROOF_BYTES:
            raise ValueError("Proof file cannot exceed 20 MiB.")
        media_type = _detected_media_type(content)
        if media_type is None:
            raise ValueError("Proof must be a PDF, PNG, JPEG, or WebP file.")
        return cls(source.name, media_type, content)


@dataclass(frozen=True, slots=True)
class ProofSummary:
    id: int
    file_name: str
    media_type: str
    size_bytes: int


def validate_proof_collection(proofs: tuple[NewProof, ...]) -> None:
    if len(proofs) > MAX_PROOFS_PER_ITEM:
        raise ValueError(f"An item can have at most {MAX_PROOFS_PER_ITEM} proofs.")
    if any(not isinstance(proof, NewProof) for proof in proofs):
        raise TypeError("Every proof must be a validated proof file.")
    names = [proof.file_name.casefold() for proof in proofs]
    if len(names) != len(set(names)):
        raise ValueError("Proof file names must be unique for each item.")
