"""Extracts PNG or JPEG files from ZIP, CBZ, or RAR archives while preserving the folder structure."""

import shutil
import zipfile
from pathlib import Path, PurePosixPath


def extract_png_from_archive(archive_path, output_path):
    """Extract all PNG files from a ZIP, CBZ, or RAR archive.

    The archive's folder structure is preserved inside ``output_path``.
    Returns a list containing the paths of the extracted PNG files.
    """
    archive_path = Path(archive_path).expanduser().resolve()
    output_path = Path(output_path).expanduser().resolve()

    if not archive_path.is_file():
        raise FileNotFoundError(f"Archive not found: {archive_path}")

    extension = archive_path.suffix.lower()
    if extension not in {".zip", ".cbz", ".rar"}:
        raise ValueError("Archive must be a .zip, .cbz, or .rar file")

    output_path.mkdir(parents=True, exist_ok=True)
    extracted_files = []

    if extension in {".zip", ".cbz"}:
        with zipfile.ZipFile(archive_path) as archive:
            members = (
                (item.filename, item.is_dir(), archive.open, item)
                for item in archive.infolist()
            )
            _extract_png_members(members, output_path, extracted_files)
    else:
        try:
            import rarfile
        except ImportError as error:
            raise RuntimeError(
                "RAR support requires the 'rarfile' package"
            ) from error

        with rarfile.RarFile(archive_path) as archive:
            members = (
                (item.filename, item.isdir(), archive.open, item)
                for item in archive.infolist()
            )
            _extract_png_members(members, output_path, extracted_files)

    return extracted_files


def _extract_png_members(members, output_path, extracted_files):
    for member_name, is_directory, open_member, member in members:
        if is_directory or Path(member_name).suffix.lower() not in {".png", ".jpeg"}:
            continue

        relative_path = PurePosixPath(member_name.replace("\\", "/"))
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise ValueError(f"Unsafe path in archive: {member_name}")

        destination = output_path.joinpath(*relative_path.parts).resolve()
        try:
            destination.relative_to(output_path)
        except ValueError as error:
            raise ValueError(f"Unsafe path in archive: {member_name}") from error

        destination.parent.mkdir(parents=True, exist_ok=True)
        with open_member(member) as source, destination.open("wb") as target:
            shutil.copyfileobj(source, target)
        extracted_files.append(destination)


x= extract_png_from_archive(r"C:\Users\xtb20\OneDrive\Documents\GitHub\TranslatorAPI\test images\Batman 013 (2026) (digital) (Pyrate-DCP).cbz", r"test images\extracted")